"""Monitor selection must isolate complete ledgers without changing their contents."""

import hashlib
import sqlite3

import pytest
import yaml
from fastapi.testclient import TestClient

from quant_platform.app import create_app


def ledger_fixture(root, name, cash, *, pending=1, snapshot=True):
    from live_trading.modules.fill_importer import LiveRecorder
    from live_trading.modules.monitor_store import MonitorStore

    db = root / "live_trading/data" / f"{name}.db"
    recorder = LiveRecorder(str(db))
    recorder.set_cash(cash)
    recorder.record_batch("shared-batch", "2026-10-30", "LIVE", pending)
    recorder.record_orders("shared-batch", [
        {
            "client_order_id": f"order-{i}", "stock_code": "600000.SH",
            "instrument_qlib": "SH600000", "side": "BUY", "quantity": 0,
            "target_value": cash / pending, "price_type": "CLOSE_AUCTION_TOPK_CASH_V1",
            "limit_price": 0, "priority": i,
        }
        for i in range(pending)
    ])
    store = MonitorStore(str(db))
    if snapshot:
        store.upsert_daily_snapshot({
            "date": "2026-10-29", "cash": cash, "market_value": 0,
            "total_value": cash, "position_count": 0,
        })
    store.record_pipeline_event("2026-10-30", "report", "OK", name)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE batches SET strategy_id=?, signal_date='2026-10-29'", (name,))
        conn.execute("INSERT INTO predictions VALUES ('2026-10-29','SH600000',?,1)", (cash,))
        conn.execute(
            "INSERT INTO fills (batch_id,client_order_id,mode,stock_code,side,status,message) "
            "VALUES ('shared-batch','receipt','LIVE','600000.SH','BUY','REJECTED',?)", (name,),
        )
    conn.close()
    # Stabilize the fixture before hashing: live WAL coordination is tested by
    # the recorder suite, and SQLite can create coordination sidecars in ro mode.
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    config = {
        "storage": {"db_path": str(db.relative_to(root))},
        "live": {"bridge_root": str(root / "bridge"), "strategy_id": name},
        "monitor": {"benchmark_name": name},
    }
    config_path = root / "live_trading/configs" / f"{name}.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(config))
    return config


def client_for(root, config=None):
    research = root / "research"
    research.mkdir(exist_ok=True)
    return TestClient(
        create_app(research, root / "archive", live_config=config, project_root=root),
        base_url="http://127.0.0.1:8090",
    )


def file_state(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_catalog_deduplicates_database_aliases_and_prefers_explicit_default_config(tmp_path):
    old = ledger_fixture(tmp_path, "old", 123)
    current = ledger_fixture(tmp_path, "new", 456)
    alias = {**current, "live": {**current["live"], "strategy_id": "prepared-alias"}}
    (tmp_path / "live_trading/configs/00_prepared.yaml").write_text(yaml.safe_dump(alias))
    with client_for(tmp_path, current) as client:
        response = client.get("/api/ledgers")
        assert response.status_code == 200
        catalog = response.json()
        assert len(catalog["ledgers"]) == 2
        selected = next(row for row in catalog["ledgers"] if row["id"] == catalog["default_ledger"])
        assert selected["strategy_id"] == "new"
        assert str(tmp_path) not in response.text
        assert ".db" not in response.text
        assert client.get("/api/overview").json()["cash"] == 456


def test_every_monitor_route_uses_the_selected_ledger_and_clients_stay_isolated(tmp_path):
    old = ledger_fixture(tmp_path, "old", 123)
    ledger_fixture(tmp_path, "new", 456, pending=5, snapshot=False)
    with client_for(tmp_path, old) as client:
        rows = client.get("/api/ledgers").json()["ledgers"]
        ids = {row["strategy_id"]: row["id"] for row in rows}
        for name, cash, planned in [("new", 456, 5), ("old", 123, 1), ("new", 456, 5)]:
            def get(path):
                response = client.get(path, params={"ledger": ids[name]})
                assert response.status_code == 200, response.text
                return response.json()

            overview = get("/api/overview")
            assert overview["strategy_id"] == name
            assert overview["cash"] == cash
            assert get("/api/positions")["cash"] == cash
            assert get("/api/cashflows")["cash"] == cash
            assert get("/api/batches")[0]["planned"] == planned
            detail = get("/api/batches/shared-batch")
            assert len(detail["orders"]) == planned
            assert detail["fills"][0]["message"] == name
            assert get("/api/batches/shared-batch/fills")[0]["message"] == name
            assert get("/api/predictions")["data"][0]["score"] == cash
            assert get("/api/predictions/summary")["mean_score"] == cash
            assert get("/api/predictions/daily-mean")[0]["mean_score"] == cash
            assert get("/api/predictions/dates") == ["2026-10-29"]
            assert get("/api/predictions/instruments")[0]["instrument"] == "SH600000"
            assert get("/api/pipeline")["days"]["2026-10-30"]["report"]["message"] == name
            assert get("/api/alerts") == []
            assert get("/api/corporate-actions")["events"] == []
            assert len(get("/api/nav")) == (0 if name == "new" else 1)
        # Selection is request state; an unscoped request still reads the server default.
        assert client.get("/api/overview").json()["cash"] == 123


@pytest.mark.parametrize("unsafe", ["../new", "/etc/passwd", "file:///etc/passwd", "new.db", "x' OR 1=1--", ""])
def test_unknown_or_unsafe_ledger_is_rejected_without_falling_back(tmp_path, unsafe):
    config = ledger_fixture(tmp_path, "old", 123)
    with client_for(tmp_path, config) as client:
        for route in ["/api/overview", "/api/nav", "/api/batches/shared-batch/fills"]:
            response = client.get(route, params={"ledger": unsafe})
            assert response.status_code == 404
            assert unsafe not in response.json()["detail"] or unsafe == ""
            assert str(tmp_path) not in response.text


def test_discovery_excludes_missing_malformed_outside_and_old_schema_without_mutation(tmp_path):
    valid = ledger_fixture(tmp_path, "valid", 123)
    config_root = tmp_path / "live_trading/configs"
    (config_root / "malformed.yaml").write_text("[invalid: [")
    missing = {**valid, "storage": {"db_path": "live_trading/data/missing/sub.db"}}
    (config_root / "missing.yaml").write_text(yaml.safe_dump(missing))
    outside = tmp_path / "private.db"
    with sqlite3.connect(outside) as conn:
        conn.execute("CREATE TABLE secret (value TEXT)")
    conn.close()
    (config_root / "outside.yaml").write_text(yaml.safe_dump({**valid, "storage": {"db_path": str(outside)}}))
    legacy = tmp_path / "live_trading/data/legacy.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE daily_snapshot (date TEXT)")
    conn.close()
    (config_root / "legacy.yaml").write_text(yaml.safe_dump({**valid, "storage": {"db_path": str(legacy)}}))
    (tmp_path / "live_trading/data/escape.db").symlink_to(outside)
    (config_root / "escape.yaml").write_text(yaml.safe_dump({**valid, "storage": {"db_path": "live_trading/data/escape.db"}}))
    before = file_state(tmp_path / "live_trading")
    with client_for(tmp_path, missing) as client:
        catalog = client.get("/api/ledgers").json()
        assert [row["strategy_id"] for row in catalog["ledgers"]] == ["valid"]
        assert catalog["default_ledger"] == catalog["ledgers"][0]["id"]
        assert client.get("/api/overview").json()["cash"] == 123
        assert client.get("/api/research").status_code == 200
    assert file_state(tmp_path / "live_trading") == before
    assert not (tmp_path / "live_trading/data/missing").exists()


def test_catalog_and_queries_fail_closed_if_ledger_disappears(tmp_path):
    config = ledger_fixture(tmp_path, "current", 123)
    with client_for(tmp_path, config) as client:
        ledger_id = client.get("/api/ledgers").json()["default_ledger"]
        db = tmp_path / config["storage"]["db_path"]
        db.unlink()
        assert client.get("/api/ledgers").json() == {"ledgers": [], "default_ledger": None}
        assert client.get("/api/overview", params={"ledger": ledger_id}).status_code == 503
        assert client.get("/api/overview").status_code == 503
        assert client.get("/api/health").json()["monitor_status"] == "unavailable"
        assert not db.exists()


def test_new_ledger_without_monitor_tables_displays_pending_plan_and_cash_without_initialization(tmp_path, monkeypatch):
    from live_trading.modules.fill_importer import LiveRecorder
    from live_trading.modules.monitor_store import MonitorStore

    config = ledger_fixture(tmp_path, "new", 244607, pending=5, snapshot=False)
    db = tmp_path / config["storage"]["db_path"]
    with sqlite3.connect(db) as conn:
        for table in ("daily_snapshot", "position_snapshot", "pipeline_events", "alerts"):
            conn.execute(f"DROP TABLE {table}")
    conn.close()
    before = file_state(tmp_path / "live_trading")

    def forbidden(*args, **kwargs):
        raise AssertionError("monitor selection must not initialize or migrate the ledger")

    monkeypatch.setattr(LiveRecorder, "_init_db", forbidden)
    monkeypatch.setattr(MonitorStore, "_init_db", forbidden)
    with client_for(tmp_path, config) as client:
        catalog = client.get("/api/ledgers").json()
        assert len(catalog["ledgers"]) == 1
        selected = {"ledger": catalog["default_ledger"]}
        overview = client.get("/api/overview", params=selected).json()
        assert overview["snapshot"] is None
        assert overview["cash"] == 244607
        assert client.get("/api/positions", params=selected).json()["cash"] == 244607
        assert client.get("/api/nav", params=selected).json() == []
        assert client.get("/api/alerts", params=selected).json() == []
        assert client.get("/api/pipeline", params=selected).json()["days"] == {}
        assert client.get("/api/positions/history", params={"date": "2026-10-29", **selected}).json()["positions"] == []
        detail = client.get("/api/batches/shared-batch", params=selected).json()
        assert len(detail["orders"]) == 5
        assert detail["reconcile"]["planned"] == 5
    assert file_state(tmp_path / "live_trading") == before


@pytest.mark.parametrize("column", ["is_fresh", "ordering_trusted"])
def test_incompatible_snapshot_metadata_is_not_listed_or_ready_and_is_never_migrated(tmp_path, column):
    config = ledger_fixture(tmp_path, "old-metadata", 123)
    db = tmp_path / config["storage"]["db_path"]
    with sqlite3.connect(db) as conn:
        conn.execute(f"ALTER TABLE broker_snapshot_imports DROP COLUMN {column}")
    conn.close()
    before = file_state(tmp_path / "live_trading")
    with client_for(tmp_path, config) as client:
        assert client.get("/api/ledgers").json() == {"ledgers": [], "default_ledger": None}
        assert client.get("/api/health").json()["monitor_status"] == "unavailable"
        assert client.get("/api/overview").status_code == 503
    assert file_state(tmp_path / "live_trading") == before


def test_selected_ledger_that_becomes_incompatible_returns_503_without_migration_details(tmp_path):
    config = ledger_fixture(tmp_path, "current", 123)
    with client_for(tmp_path, config) as client:
        ledger_id = client.get("/api/ledgers").json()["default_ledger"]
        db = tmp_path / config["storage"]["db_path"]
        with sqlite3.connect(db) as conn:
            conn.execute("ALTER TABLE broker_snapshot_imports DROP COLUMN is_fresh")
        conn.close()
        response = client.get("/api/overview", params={"ledger": ledger_id})
        assert response.status_code == 503
        assert "migration" not in response.text
        assert str(db) not in response.text


@pytest.mark.parametrize("loop_kind", ["config", "database"])
def test_discovery_skips_symlink_loops_without_blocking_research_or_valid_ledgers(tmp_path, loop_kind):
    config = ledger_fixture(tmp_path, "valid", 123)
    config_root = tmp_path / "live_trading/configs"
    if loop_kind == "config":
        path = config_root / "loop.yaml"
        path.symlink_to(path)
    else:
        path = tmp_path / "live_trading/data/loop.db"
        path.symlink_to(path)
        (config_root / "loop.yaml").write_text(yaml.safe_dump({
            **config, "storage": {"db_path": str(path)},
        }))
    with client_for(tmp_path, config) as client:
        rows = client.get("/api/ledgers").json()["ledgers"]
        assert [row["strategy_id"] for row in rows] == ["valid"]
        assert client.get("/api/overview").json()["cash"] == 123
        assert client.get("/api/research").status_code == 200
        assert client.get("/api/analyses").status_code == 200


@pytest.mark.parametrize("monitor", [None, [], "invalid"])
def test_malformed_optional_config_section_is_not_advertised(tmp_path, monitor):
    valid = ledger_fixture(tmp_path, "valid", 123)
    malformed = ledger_fixture(tmp_path, "malformed", 456)
    malformed["monitor"] = monitor
    (tmp_path / "live_trading/configs/malformed.yaml").write_text(yaml.safe_dump(malformed))
    with client_for(tmp_path, valid) as client:
        assert [row["strategy_id"] for row in client.get("/api/ledgers").json()["ledgers"]] == ["valid"]
        assert client.get("/api/overview").json()["cash"] == 123


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE signal_orders DROP COLUMN priority",
    "ALTER TABLE corporate_actions DROP COLUMN ex_date",
    "ALTER TABLE pipeline_events RENAME TO old_pipeline; "
    "CREATE TABLE pipeline_events AS SELECT trade_date,stage,status,message,created_at FROM old_pipeline; "
    "DROP TABLE old_pipeline",
    "ALTER TABLE alerts RENAME TO old_alerts; "
    "CREATE TABLE alerts AS SELECT trade_date,level,rule,message,channel,sent_ok,created_at FROM old_alerts; "
    "DROP TABLE old_alerts",
])
def test_catalog_rejects_schema_missing_columns_needed_to_order_monitor_queries(tmp_path, mutation):
    config = ledger_fixture(tmp_path, "old-schema", 123)
    db = tmp_path / config["storage"]["db_path"]
    with sqlite3.connect(db) as conn:
        conn.executescript(mutation)
    conn.close()
    with client_for(tmp_path, config) as client:
        assert client.get("/api/ledgers").json() == {"ledgers": [], "default_ledger": None}
        assert client.get("/api/health").json()["monitor_status"] == "unavailable"
        assert client.get("/api/batches/shared-batch").status_code == 503
