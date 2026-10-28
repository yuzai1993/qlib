"""The platform can inspect a live ledger without initializing or changing it."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.monitor_store import MonitorStore
from live_trading.modules.signal_schema import BatchHeader, FillEvent, SchemaError, SignalOrder
from live_trading.web.api import create_router


@pytest.fixture
def ledger(tmp_path):
    db = tmp_path / "ledger.db"
    recorder = LiveRecorder(str(db), opening_cash=100_000)
    order = SignalOrder(
        batch_id="batch-1", client_order_id="order-1", stock_code="600000.SH",
        instrument_qlib="SH600000", side="BUY", quantity=0,
        target_value=1000, price_type="CLOSE_AUCTION_LIMIT", limit_price=10, priority=1,
        reason="fixture",
    )
    header = BatchHeader(
        batch_id="batch-1", strategy_id="fixture", trade_date="2026-07-13",
        signal_date="2026-07-12", account_id="fixture", account_type="STOCK",
        account_environment="SIMULATION", mode="LIVE",
        created_at="2026-07-12T20:00:00+08:00", order_count=1, checksum="",
    )
    recorder.record_publish_plan(header, [order])
    recorder.apply_fill(FillEvent(
        batch_id="batch-1", client_order_id="order-1", mode="LIVE",
        stock_code="600000.SH", side="BUY", status="FILLED",
        requested_qty=100, filled_qty=100, avg_price=10, qmt_order_id="1",
        message="", ts="2026-07-13T15:00:00",
    ))
    recorder.save_stock_names([
        {"stock_code": "600000.SH", "instrument": "SH600000", "name": "浦发银行"},
    ])
    recorder.save_predictions("2026-07-12", {"SH600000": .7, "SZ000001": -.2})
    recorder.record_cash_flow("2026-07-13", "DIVIDEND", 10, stock_code="600000.SH")
    store = MonitorStore(str(db))
    store.upsert_daily_snapshot({
        "date": "2026-07-13", "cash": 99000, "market_value": 1100,
        "total_value": 100100, "daily_return": .001, "cumulative_return": .001,
        "position_count": 1,
    })
    store.upsert_position_snapshots("2026-07-13", [{
        "stock_code": "600000.SH", "shares": 100, "avg_cost": 10,
        "close_price": 11, "market_value": 1100, "profit": 100, "weight": .011,
    }])
    store.record_pipeline_event("2026-07-13", "report", "OK", "fixture")
    store.try_record_alert("2026-07-13", "WARN", "FIXTURE", "fixture")
    return db, recorder, store


@pytest.mark.parametrize("reader", [MonitorStore, LiveRecorder])
def test_missing_ledger_never_creates_parent_directories(tmp_path, reader):
    db = tmp_path / "missing" / "ledger.db"
    with pytest.raises((FileNotFoundError, SchemaError)):
        reader(str(db), read_only=True)
    assert not db.parent.exists()


@pytest.mark.parametrize("reader", [MonitorStore, LiveRecorder])
def test_read_only_never_initializes_migrates_seeds_or_changes_journal(tmp_path, reader):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE daily_snapshot (date TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO daily_snapshot VALUES ('2026-07-13')")
    before = db.read_bytes()
    obj = reader(str(db), read_only=True)
    with obj._conn() as conn:
        assert conn.execute("SELECT * FROM daily_snapshot").fetchone()[0] == "2026-07-13"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert [r[1] for r in conn.execute("PRAGMA table_info(daily_snapshot)")] == ["date"]
        assert [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ["daily_snapshot"]
    assert db.read_bytes() == before
    assert list(tmp_path.iterdir()) == [db]


@pytest.mark.parametrize("reader", [MonitorStore, LiveRecorder])
def test_read_only_escapes_sqlite_uri_special_characters(tmp_path, monkeypatch, reader):
    directory = tmp_path / "账本 # ? % space"
    directory.mkdir()
    db = directory / "live?#%.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES ('correct ledger')")
    monkeypatch.chdir(tmp_path)
    obj = reader(str(db.relative_to(tmp_path)), read_only=True)
    with obj._conn() as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "correct ledger"
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
    assert list(directory.iterdir()) == [db]


@pytest.mark.parametrize("reader", [MonitorStore, LiveRecorder])
@pytest.mark.parametrize("sql", [
    "INSERT INTO marker VALUES (2)", "UPDATE marker SET value=2",
    "DELETE FROM marker", "CREATE TABLE unexpected (id INTEGER)",
    "CREATE TEMP TABLE unexpected (id INTEGER)",
])
def test_read_only_connections_reject_persistent_and_temp_writes(tmp_path, reader, sql):
    db = tmp_path / "ledger.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE marker (value INTEGER)")
        conn.execute("INSERT INTO marker VALUES (1)")
    obj = reader(str(db), read_only=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with obj._conn() as conn:
            conn.execute(sql)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT value FROM marker").fetchall() == [(1,)]


def test_read_only_public_writers_are_denied(ledger):
    db, _, _ = ledger
    store = MonitorStore(str(db), read_only=True)
    recorder = LiveRecorder(str(db), read_only=True)
    for write in (
        lambda: store.record_pipeline_event("2026-07-13", "report", "FAIL"),
        lambda: store.upsert_position_snapshots("2026-07-13", []),
        lambda: store.mark_alert_sent("2026-07-13", "FIXTURE", "test", True),
        lambda: recorder.set_cash(1),
        lambda: recorder.upsert_position("600000.SH", 0, 0),
    ):
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            write()


@pytest.mark.parametrize("reader", [MonitorStore, LiveRecorder])
def test_read_only_observes_new_commits_in_active_wal(tmp_path, reader):
    db = tmp_path / "wal.db"
    with sqlite3.connect(db) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE marker (value INTEGER)")
        writer.execute("INSERT INTO marker VALUES (1)")
        writer.commit()
        obj = reader(str(db), read_only=True)
        with obj._conn() as conn:
            assert conn.execute("SELECT value FROM marker").fetchall()[0][0] == 1
        writer.execute("UPDATE marker SET value=2")
        writer.commit()
        assert Path(str(db) + "-wal").is_file()
        with obj._conn() as conn:
            assert conn.execute("SELECT value FROM marker").fetchall()[0][0] == 2


GET_REQUESTS = [
    "/overview", "/nav", "/nav?start=2026-07-13&end=2026-07-13",
    "/positions", "/positions/history?date=2026-07-13", "/batches",
    "/batches/batch-1", "/batches/batch-1/fills", "/predictions",
    "/predictions?instrument=600000.SH&sort_order=desc&limit=1",
    "/predictions/dates", "/predictions/daily-mean?instruments=600000.SH",
    "/predictions/summary", "/predictions/instruments", "/pipeline",
    "/alerts", "/cashflows", "/corporate-actions",
]


def test_all_get_routes_match_legacy_without_initialization_or_bridge_access(ledger, tmp_path, monkeypatch):
    db, _, _ = ledger
    bridge = tmp_path / "missing-bridge"
    config = {
        "live": {"bridge_root": str(bridge), "strategy_id": "fixture"},
        "storage": {"db_path": str(db)},
    }
    legacy = FastAPI()
    legacy.include_router(create_router(config, tmp_path))
    with TestClient(legacy) as client:
        expected = {url: client.get(url).json() for url in GET_REQUESTS}
    assert expected["/positions"]["positions"][0]["stock_code"] == "600000.SH"
    assert expected["/batches"][0]["terminal"] == 1
    assert expected["/predictions"]["total"] == 2
    with sqlite3.connect(db) as conn:
        before = list(conn.iterdump())

    def forbidden(*args, **kwargs):
        pytest.fail("read-only monitor attempted initialization or a shared-directory scan")

    monkeypatch.setattr(MonitorStore, "_init_db", forbidden)
    for name in ("_init_db", "_backup_legacy_db", "_seed_opening_cash", "_seed_opening_value_adjustment"):
        monkeypatch.setattr(LiveRecorder, name, forbidden)
    monkeypatch.setattr(Path, "glob", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)
    monkeypatch.setattr(Path, "iterdir", forbidden)
    app = FastAPI()
    router = create_router(config, tmp_path, read_only=True)
    covered = {url.split("?")[0].replace("batch-1", "{batch_id}") for url in GET_REQUESTS}
    assert {route.path for route in router.routes} == covered
    app.include_router(router)
    with TestClient(app) as client:
        for url, value in expected.items():
            response = client.get(url)
            assert response.status_code == 200, url
            assert response.json() == value, url
    with sqlite3.connect(db) as conn:
        assert list(conn.iterdump()) == before
    assert not bridge.exists()
