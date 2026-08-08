"""FillImporter：回执导入、SIMULATE 隔离、幂等、对账。"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.fees import DEFAULT_FEES, order_total_fee
from live_trading.modules.fill_importer import FillImporter, LiveRecorder
from live_trading.modules.signal_schema import (
    BatchHeader,
    FillEvent,
    SchemaError,
    SignalOrder,
)

BATCH_ID = "20260714_csi300_topk10_001"


def _fill(client_order_id="20260714001S", mode="LIVE", status="FILLED",
          side="SELL", stock_code="000001.SZ", requested=800, filled=800,
          price=10.45, batch_id=BATCH_ID):
    return {
        "type": "fill_event",
        "batch_id": batch_id,
        "client_order_id": client_order_id,
        "mode": mode,
        "stock_code": stock_code,
        "side": side,
        "status": status,
        "requested_qty": requested,
        "filled_qty": filled,
        "avg_price": price,
        "qmt_order_id": "1001",
        "message": "",
        "ts": "2026-07-14T09:31:12+08:00",
    }


def _write_fills(bridge_root: Path, fills: list, batch_id=BATCH_ID, with_done=True):
    outbound = bridge_root / "outbound"
    outbound.mkdir(parents=True, exist_ok=True)
    jsonl = outbound / f"fills_{batch_id}.jsonl"
    jsonl.write_text(
        "\n".join(json.dumps(f, ensure_ascii=False) for f in fills) + "\n",
        encoding="utf-8",
    )
    if with_done:
        (outbound / f"fills_{batch_id}.done").write_text("ok\n", encoding="utf-8")


def _record_plan(
    recorder,
    fills,
    batch_id=BATCH_ID,
    mode=None,
    planned_orders=None,
    trade_date="2026-07-14",
):
    """为回执测试写入对应的原始计划，模拟正常发布链路。"""
    mode = mode or fills[0]["mode"]
    recorder.record_batch(
        batch_id, trade_date, mode,
        planned_orders if planned_orders is not None else len(fills),
    )
    recorder.record_orders(batch_id, [
        {
            "client_order_id": f["client_order_id"],
            "stock_code": f["stock_code"],
            "instrument_qlib": "",
            "side": f["side"],
            "quantity": f["requested_qty"] if f["side"] == "SELL" else 0,
            "target_value": (
                0.0 if f["side"] == "SELL"
                else f.get(
                    "target_value",
                    float(f["requested_qty"])
                    * max(float(f["avg_price"]), 1.0),
                )
            ),
            "price_type": "CLOSE_AUCTION_LIMIT",
            "limit_price": 0.0,
            "priority": 10 if f["side"] == "SELL" else 20,
            "reason": "test",
        }
        for f in fills
    ])


@pytest.fixture
def env(tmp_path):
    db_path = tmp_path / "live.db"
    recorder = LiveRecorder(str(db_path))
    importer = FillImporter(tmp_path, recorder)
    return tmp_path, recorder, importer


def test_opening_cash_seeds_only_a_fresh_ledger(tmp_path):
    db_path = tmp_path / "fresh.db"

    recorder = LiveRecorder(str(db_path), opening_cash=500_000.0)
    assert recorder.get_cash() == pytest.approx(500_000.0)

    recorder.set_cash(490_000.0)
    reopened = LiveRecorder(str(db_path), opening_cash=500_000.0)
    assert reopened.get_cash() == pytest.approx(490_000.0)


def test_opening_value_adjustment_seeds_only_a_fresh_ledger(tmp_path):
    db_path = tmp_path / "fresh-adjusted.db"

    recorder = LiveRecorder(
        str(db_path),
        opening_cash=9_949_714.06,
        opening_value_adjustment=-681_126.98,
    )
    assert recorder.get_cash() == pytest.approx(9_949_714.06)
    assert recorder.get_value_adjustment() == pytest.approx(-681_126.98)

    reopened = LiveRecorder(
        str(db_path),
        opening_cash=9_949_714.06,
        opening_value_adjustment=-123.0,
    )
    assert reopened.get_value_adjustment() == pytest.approx(-681_126.98)


def test_missing_opening_value_adjustment_defaults_to_zero(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "legacy.db"), opening_cash=500_000.0)

    assert recorder.get_value_adjustment() == 0.0


def test_opening_cash_refuses_to_seed_an_already_used_ledger(tmp_path):
    db_path = tmp_path / "used.db"
    recorder = LiveRecorder(str(db_path))
    recorder.record_batch("used", "2026-07-14", "SIMULATE", 0)

    with pytest.raises(SchemaError, match="opening_cash"):
        LiveRecorder(str(db_path), opening_cash=500_000.0)


def test_opening_value_adjustment_refuses_used_ledger_migration(tmp_path):
    db_path = tmp_path / "used-adjustment.db"
    recorder = LiveRecorder(str(db_path), opening_cash=500_000.0)
    recorder.record_batch("used", "2026-07-14", "SIMULATE", 0)

    with pytest.raises(SchemaError, match="opening_value_adjustment"):
        LiveRecorder(str(db_path), opening_value_adjustment=-100.0)


def test_batch_ledger_accepts_real_account_environment(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "real.db"))

    recorder.record_batch(
        "real", "2026-07-14", "LIVE", 1,
        account_environment="REAL",
    )

    assert recorder.get_batch("real")["account_environment"] == "REAL"


def test_batch_ledger_rejects_unknown_account_environment(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "invalid.db"))

    with pytest.raises(SchemaError, match="account_environment"):
        recorder.record_batch(
            "bad", "2026-07-14", "LIVE", 1,
            account_environment="UNKNOWN",
        )


def test_position_open_date_survives_add_on_and_resets_after_full_exit(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "dates.db"), opening_cash=100_000.0)

    first = _fill(
        batch_id="b1", client_order_id="buy1", side="BUY",
        stock_code="600000.SH", requested=100, filled=100, price=10.0,
    )
    _record_plan(recorder, [first], batch_id="b1", trade_date="2026-07-14")
    recorder.apply_fill(FillEvent.from_dict(first))
    assert recorder.get_positions()["600000.SH"]["opened_trade_date"] == "2026-07-14"

    add_on = _fill(
        batch_id="b2", client_order_id="buy2", side="BUY",
        stock_code="600000.SH", requested=100, filled=100, price=11.0,
    )
    _record_plan(recorder, [add_on], batch_id="b2", trade_date="2026-07-15")
    recorder.apply_fill(FillEvent.from_dict(add_on))
    assert recorder.get_positions()["600000.SH"]["opened_trade_date"] == "2026-07-14"

    sell = _fill(
        batch_id="b3", client_order_id="sell1", side="SELL",
        stock_code="600000.SH", requested=200, filled=200, price=12.0,
    )
    _record_plan(recorder, [sell], batch_id="b3", trade_date="2026-07-16")
    recorder.apply_fill(FillEvent.from_dict(sell))
    assert "600000.SH" not in recorder.get_positions()

    reentry = _fill(
        batch_id="b4", client_order_id="buy3", side="BUY",
        stock_code="600000.SH", requested=100, filled=100, price=12.0,
    )
    _record_plan(recorder, [reentry], batch_id="b4", trade_date="2026-07-17")
    recorder.apply_fill(FillEvent.from_dict(reentry))
    assert recorder.get_positions()["600000.SH"]["opened_trade_date"] == "2026-07-17"


def test_buy_fill_is_broker_sized_and_cannot_exceed_target_value(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "target.db"), opening_cash=100_000.0)
    fill = _fill(
        batch_id="buy-target", client_order_id="buy-target-1", side="BUY",
        stock_code="600000.SH", requested=1_000, filled=1_000, price=10.0,
    )
    _record_plan(recorder, [fill], batch_id="buy-target")

    recorder.apply_fill(FillEvent.from_dict(fill))
    assert recorder.get_positions()["600000.SH"]["shares"] == 1_000

    over = dict(fill, avg_price=10.01)
    with pytest.raises(SchemaError, match="target_value"):
        recorder.apply_fill(FillEvent.from_dict(over))


def test_sell_fill_remains_bounded_by_planned_quantity(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "sell-plan.db"))
    fill = _fill(requested=800, filled=800)
    _record_plan(recorder, [fill])

    with pytest.raises(SchemaError, match="planned"):
        recorder.apply_fill(FillEvent.from_dict(dict(fill, requested_qty=900)))


def test_same_client_order_id_can_exist_in_two_batches(env):
    _, recorder, _ = env
    first = _fill()
    second = dict(first, batch_id="20260714_csi300_topk10_002")
    _record_plan(recorder, [first], batch_id=first["batch_id"])
    _record_plan(recorder, [second], batch_id=second["batch_id"])

    assert len(recorder.get_orders(first["batch_id"])) == 1
    assert len(recorder.get_orders(second["batch_id"])) == 1


def test_legacy_single_key_database_migrates_without_changing_balances(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE batches (
                batch_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL,
                mode TEXT NOT NULL, planned_orders INTEGER NOT NULL DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE fills (
                client_order_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
                mode TEXT NOT NULL, stock_code TEXT NOT NULL, side TEXT NOT NULL,
                status TEXT NOT NULL, requested_qty INTEGER, filled_qty INTEGER,
                avg_price REAL, qmt_order_id TEXT, message TEXT, ts TEXT,
                applied_qty INTEGER NOT NULL DEFAULT 0,
                applied_fee REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE signal_orders (
                client_order_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
                stock_code TEXT NOT NULL, instrument_qlib TEXT, side TEXT NOT NULL,
                quantity INTEGER NOT NULL, price_type TEXT, limit_price REAL NOT NULL,
                priority INTEGER, reason TEXT
            );
            CREATE TABLE positions (
                stock_code TEXT PRIMARY KEY, shares INTEGER NOT NULL,
                avg_cost REAL NOT NULL, updated_at TEXT
            );
            CREATE TABLE account_state (key TEXT PRIMARY KEY, value REAL NOT NULL);
            INSERT INTO batches VALUES ('b1', '2026-07-14', 'LIVE', 1, NULL);
            INSERT INTO fills VALUES (
                'old1', 'b1', 'LIVE', '600000.SH', 'BUY', 'FILLED',
                100, 100, 10.0, 'q1', '', '', 100, 5.1
            );
            INSERT INTO signal_orders VALUES (
                'old1', 'b1', '600000.SH', 'SH600000', 'BUY', 100,
                'FIX', 10.0, 20, 'legacy'
            );
            INSERT INTO positions VALUES ('600000.SH', 100, 10.0, NULL);
            INSERT INTO account_state VALUES ('cash', 98994.9);
        """)

    recorder = LiveRecorder(str(db))

    assert recorder.get_cash() == pytest.approx(98994.9)
    assert recorder.get_positions()["600000.SH"]["shares"] == 100
    assert recorder.get_fills("b1")[0]["applied_amount"] == pytest.approx(1000.0)
    assert list(tmp_path.glob("legacy.db.pre_hardening_*.bak"))
    with sqlite3.connect(db) as conn:
        fill_pk = [r[1] for r in conn.execute("PRAGMA table_info(fills)") if r[5]]
        order_pk = [r[1] for r in conn.execute("PRAGMA table_info(signal_orders)") if r[5]]
        batch_cols = {r[1] for r in conn.execute("PRAGMA table_info(batches)")}
    assert fill_pk == ["batch_id", "client_order_id"]
    assert order_pk == ["batch_id", "client_order_id"]
    assert {"superseded_by", "superseded_at"} <= batch_cols


def test_supersede_batch_is_idempotent_and_active_queries_exclude_old(env):
    _, recorder, _ = env
    batch_ids = [
        "20260715_csi300_topk10_001",
        "20260715_csi300_topk10_002",
        "20260715_csi300_topk10_003",
    ]
    for batch_id in batch_ids:
        recorder.record_batch(batch_id, "2026-07-15", "LIVE", 10)

    assert recorder.supersede_batch(batch_ids[0], batch_ids[2])
    assert not recorder.supersede_batch(batch_ids[0], batch_ids[2])
    assert recorder.supersede_batch(batch_ids[1], batch_ids[2])

    active = recorder.get_active_batches_by_date("2026-07-15")
    assert [row["batch_id"] for row in active] == [batch_ids[2]]
    assert recorder.get_latest_active_batch("LIVE")["batch_id"] == batch_ids[2]

    history = {row["batch_id"]: row for row in recorder.list_batches()}
    assert history[batch_ids[0]]["superseded_by"] == batch_ids[2]
    assert history[batch_ids[0]]["superseded_at"]
    assert history[batch_ids[1]]["superseded_by"] == batch_ids[2]
    assert history[batch_ids[2]]["superseded_by"] is None


def test_batch_queries_can_be_scoped_to_one_strategy(env):
    _, recorder, _ = env
    day = "2026-08-07"
    main_strategy_id = "csi1000_b6m_b2s_postclose_real"
    probe_strategy_id = "csi1000_pr49_one_lot_probe"
    main_batch_id = "20260807_csi1000_b6m_b2s_postclose_real_001"
    probe_batch_id = "20260807_csi1000_pr49_one_lot_probe_001"

    for batch_id, strategy_id in (
        (main_batch_id, main_strategy_id),
        (probe_batch_id, probe_strategy_id),
    ):
        recorder.record_publish_plan(BatchHeader(
            batch_id=batch_id,
            strategy_id=strategy_id,
            trade_date=day,
            signal_date="2026-08-06",
            account_id="test-account",
            account_type="STOCK",
            account_environment="SIMULATION",
            mode="SIMULATE",
            created_at="2026-08-06T21:00:00+08:00",
            order_count=0,
            checksum="",
        ), [])

    assert {b["strategy_id"] for b in recorder.get_batches_by_date(day)} == {
        main_strategy_id,
        probe_strategy_id,
    }
    assert [b["batch_id"] for b in recorder.get_batches_by_date(
        day, strategy_id=probe_strategy_id,
    )] == [probe_batch_id]
    assert {
        b["strategy_id"]
        for b in recorder.get_active_batches_by_date(day)
    } == {
        "csi1000_b6m_b2s_postclose_real",
        "csi1000_pr49_one_lot_probe",
    }
    assert [b["batch_id"] for b in recorder.get_active_batches_by_date(
        day, strategy_id="csi1000_pr49_one_lot_probe",
    )] == [probe_batch_id]


def test_live_ledger_initializes_exact_operator_probe_lifecycle_schema(tmp_path):
    db = tmp_path / "shared.db"
    LiveRecorder(str(db))

    with sqlite3.connect(db) as conn:
        columns = [
            row[1] for row in conn.execute(
                "PRAGMA table_info(operator_probe_lifecycle)"
            )
        ]

    assert columns == [
        "strategy_id", "stock_code", "buy_batch_id", "buy_trade_date",
        "sell_batch_id", "sell_trade_date", "state", "updated_at",
    ]


def test_legacy_read_only_ledger_without_probe_table_has_no_lifecycle(tmp_path):
    db = tmp_path / "legacy-shared.db"
    LiveRecorder(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE operator_probe_lifecycle")

    recorder = LiveRecorder(str(db), read_only=True)

    assert recorder.get_operator_probe_lifecycle() is None


def test_probe_terminal_fill_receipt_is_immutable(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "shared.db"), opening_cash=100_000.0)
    batch_id = "20260807_csi1000_pr49_one_lot_probe_900"
    recorder.record_publish_plan(BatchHeader(
        batch_id=batch_id,
        strategy_id="csi1000_pr49_one_lot_probe",
        trade_date="2026-08-07",
        signal_date="2026-08-07",
        account_id="real-account",
        account_type="STOCK",
        account_environment="REAL",
        mode="LIVE",
        created_at="2026-08-07T00:00:00+08:00",
        order_count=1,
        checksum="",
    ), [SignalOrder(
        batch_id=batch_id,
        client_order_id="20260807900001B",
        stock_code="600000.SH",
        side="BUY",
        quantity=0,
        target_value=1_000_000.0,
        price_type="AFTER_HOURS_CLOSE",
        limit_price=0.0,
        priority=20,
        instrument_qlib="SH600000",
        reason="probe",
        max_quantity=100,
    )], probe_transition={"side": "BUY", "stock_code": "600000.SH"})
    rejected = FillEvent.from_dict(_fill(
        batch_id=batch_id,
        client_order_id="20260807900001B",
        stock_code="600000.SH",
        side="BUY",
        requested=100,
        filled=0,
        price=0.0,
        status="REJECTED",
    ))
    recorder.apply_fill(rejected)
    recorder.save_broker_snapshot(batch_id, {"account_id": "real-account"}, [])
    assert recorder.get_operator_probe_lifecycle()["state"] == "FAILED"

    with pytest.raises(SchemaError, match="terminal probe fill is immutable"):
        recorder.apply_fill(FillEvent.from_dict({
            **_fill(
                batch_id=batch_id,
                client_order_id="20260807900001B",
                stock_code="600000.SH",
                side="BUY",
                requested=100,
                filled=100,
                price=10.0,
                status="FILLED",
            ),
            "qmt_order_id": "late-order",
        }))

    assert "600000.SH" not in recorder.get_positions()


def test_probe_import_archives_only_inside_probe_root(env):
    probe_root, recorder, importer = env
    main_root = probe_root / "main"
    main_outbound = main_root / "outbound"
    main_outbound.mkdir(parents=True)
    main_fill = main_outbound / "fills_main.jsonl"
    main_done = main_outbound / "fills_main.done"
    main_fill.write_text("main\n", encoding="utf-8")
    main_done.write_text("done\n", encoding="utf-8")
    fill = _fill(mode="SIMULATE")
    _record_plan(recorder, [fill])
    _write_fills(probe_root, [fill])

    assert importer.import_fills() == 1

    assert (probe_root / "archive" / f"fills_{BATCH_ID}.jsonl").is_file()
    assert (probe_root / "archive" / f"fills_{BATCH_ID}.done").is_file()
    assert main_fill.is_file()
    assert main_done.is_file()


def test_import_cli_reconciles_only_configured_probe_strategy(
    tmp_path, monkeypatch, capsys,
):
    from live_trading.scripts import run_import_fills

    db = tmp_path / "shared.db"
    recorder = LiveRecorder(str(db), opening_cash=100_000.0)
    day = "2026-08-07"
    for strategy_id in (
        "csi1000_b6m_b2s_postclose_real",
        "csi1000_pr49_one_lot_probe",
    ):
        recorder.record_publish_plan(BatchHeader(
            batch_id=f"20260807_{strategy_id}_001",
            strategy_id=strategy_id,
            trade_date=day,
            signal_date=day,
            account_id="test-account",
            account_type="STOCK",
            account_environment="SIMULATION",
            mode="SIMULATE",
            created_at="2026-08-07T00:00:00+08:00",
            order_count=0,
            checksum="",
        ), [])
    config = {
        "live": {
            "kind": "OPERATOR_PROBE",
            "strategy_id": "csi1000_pr49_one_lot_probe",
            "bridge_root": str(tmp_path / "pr49_probe"),
        },
        "storage": {"db_path": str(db)},
        "account": {"opening_cash": 100_000.0},
        "fees": DEFAULT_FEES,
    }
    monkeypatch.setattr(
        run_import_fills, "load_live_config", lambda *args: config,
    )
    monkeypatch.setattr(
        sys, "argv", ["run_import_fills.py", "--config", "probe"],
    )

    run_import_fills.main()

    output = capsys.readouterr().out
    assert "20260807_csi1000_pr49_one_lot_probe_001" in output
    assert "20260807_csi1000_b6m_b2s_postclose_real_001" not in output
    assert "probe lifecycle state=" in output


def test_unreconciled_live_batches_exclude_other_strategy(env):
    _, recorder, _ = env
    day = "2026-08-07"
    main_strategy_id = "csi1000_b6m_b2s_postclose_real"
    probe_strategy_id = "csi1000_pr49_one_lot_probe"
    main_batch_id = "20260807_csi1000_b6m_b2s_postclose_real_001"
    probe_batch_id = "20260807_csi1000_pr49_one_lot_probe_001"

    for batch_id, strategy_id in (
        (main_batch_id, main_strategy_id),
        (probe_batch_id, probe_strategy_id),
    ):
        recorder.record_publish_plan(BatchHeader(
            batch_id=batch_id,
            strategy_id=strategy_id,
            trade_date=day,
            signal_date="2026-08-06",
            account_id="test-account",
            account_type="STOCK",
            account_environment="SIMULATION",
            mode="LIVE",
            created_at="2026-08-06T21:00:00+08:00",
            order_count=1,
            checksum="",
        ), [SignalOrder(
            batch_id=batch_id,
            client_order_id=f"{batch_id}_buy",
            stock_code="600000.SH",
            side="BUY",
            quantity=0,
            target_value=1_000.0,
            price_type="CLOSE_AUCTION_LIMIT",
            limit_price=0.0,
            priority=20,
            instrument_qlib="SH600000",
            reason="test",
        )])

    assert {
        batch["batch_id"]
        for batch in recorder.get_unreconciled_active_live_batches_before(
            "2026-08-08",
        )
    } == {main_batch_id, probe_batch_id}
    assert [
        batch["batch_id"]
        for batch in recorder.get_unreconciled_active_live_batches_before(
            "2026-08-08", strategy_id=probe_strategy_id,
        )
    ] == [probe_batch_id]


def test_supersede_batch_rejects_invalid_or_conflicting_relationships(env):
    _, recorder, _ = env
    old = "20260715_csi300_topk10_001"
    replacement = "20260715_csi300_topk10_003"
    alternate = "20260715_csi300_topk10_004"
    recorder.record_batch(old, "2026-07-15", "LIVE", 10)
    recorder.record_batch(replacement, "2026-07-15", "LIVE", 10)
    recorder.record_batch(alternate, "2026-07-15", "LIVE", 10)
    recorder.record_batch(
        "20260716_csi300_topk10_001", "2026-07-16", "LIVE", 10,
    )
    recorder.record_batch(
        "20260715_csi300_topk10_005", "2026-07-15", "SIMULATE", 10,
    )
    recorder.record_batch("20260715_other_001", "2026-07-15", "LIVE", 10)

    with pytest.raises(SchemaError, match="same batch"):
        recorder.supersede_batch(old, old)
    with pytest.raises(SchemaError, match="unknown source"):
        recorder.supersede_batch("missing", replacement)
    with pytest.raises(SchemaError, match="unknown replacement"):
        recorder.supersede_batch(old, "missing")
    with pytest.raises(SchemaError, match="trade_date"):
        recorder.supersede_batch(old, "20260716_csi300_topk10_001")
    with pytest.raises(SchemaError, match="mode"):
        recorder.supersede_batch(old, "20260715_csi300_topk10_005")
    with pytest.raises(SchemaError, match="strategy"):
        recorder.supersede_batch(old, "20260715_other_001")

    assert recorder.supersede_batch(old, replacement)
    with pytest.raises(SchemaError, match="already superseded"):
        recorder.supersede_batch(old, alternate)


def test_promote_shadow_batch_marks_unexecuted_same_session_replacement(env):
    _, recorder, _ = env
    old = "20260805_csi1000_b6m_b2s_postclose_001"
    new = "20260805_csi1000_b6m_b2s_postclose_002"
    recorder.record_batch(old, "2026-08-05", "SIMULATE", 2)
    recorder.record_batch(new, "2026-08-05", "LIVE", 2)

    assert recorder.promote_shadow_batch(old, new)
    assert not recorder.promote_shadow_batch(old, new)
    assert recorder.get_batch(old)["superseded_by"] == new


def test_promote_shadow_batch_rejects_wrong_modes_date_or_strategy(env):
    _, recorder, _ = env
    recorder.record_batch("20260805_same_001", "2026-08-05", "LIVE", 1)
    recorder.record_batch("20260805_same_002", "2026-08-05", "LIVE", 1)
    recorder.record_batch("20260805_same_003", "2026-08-05", "SIMULATE", 1)
    recorder.record_batch("20260805_same_004", "2026-08-05", "SIMULATE", 1)
    recorder.record_batch("20260806_same_002", "2026-08-06", "LIVE", 1)
    recorder.record_batch("20260805_other_002", "2026-08-05", "LIVE", 1)

    with pytest.raises(SchemaError, match="source must be an unexecuted SIMULATE"):
        recorder.promote_shadow_batch("20260805_same_001", "20260805_same_002")
    with pytest.raises(SchemaError, match="replacement must be LIVE"):
        recorder.promote_shadow_batch("20260805_same_003", "20260805_same_004")
    with pytest.raises(SchemaError, match="trade_date"):
        recorder.promote_shadow_batch("20260805_same_003", "20260806_same_002")
    with pytest.raises(SchemaError, match="strategy"):
        recorder.promote_shadow_batch("20260805_same_003", "20260805_other_002")


def test_promote_shadow_batch_rejects_source_with_any_fill(env):
    _, recorder, _ = env
    old = "20260805_same_001"
    new = "20260805_same_002"
    skipped = _fill(
        batch_id=old,
        client_order_id="20260805001B",
        mode="SIMULATE",
        status="SKIPPED",
        side="BUY",
        requested=100,
        filled=0,
        price=0.0,
    )
    _record_plan(
        recorder, [skipped], batch_id=old, mode="SIMULATE",
        trade_date="2026-08-05",
    )
    recorder.apply_fill(FillEvent.from_dict(skipped))
    recorder.record_batch(new, "2026-08-05", "LIVE", 1)

    with pytest.raises(SchemaError, match="source must be an unexecuted SIMULATE"):
        recorder.promote_shadow_batch(old, new)


def test_promote_shadow_batch_rejects_conflicting_relationships(env):
    _, recorder, _ = env
    old = "20260805_same_001"
    new = "20260805_same_002"
    alternate = "20260805_same_003"
    recorder.record_batch(old, "2026-08-05", "SIMULATE", 1)
    recorder.record_batch(new, "2026-08-05", "LIVE", 1)
    recorder.record_batch(alternate, "2026-08-05", "LIVE", 1)

    with pytest.raises(SchemaError, match="same batch"):
        recorder.promote_shadow_batch(old, old)
    assert recorder.promote_shadow_batch(old, new)
    with pytest.raises(SchemaError, match="already superseded"):
        recorder.promote_shadow_batch(old, alternate)


def test_fill_must_match_recorded_order_before_mutating_ledger(env):
    _, recorder, _ = env
    recorder.set_cash(100000.0)
    planned = _fill(side="BUY", stock_code="600000.SH", requested=200, filled=200)
    _record_plan(recorder, [planned])
    wrong = FillEvent.from_dict(dict(planned, stock_code="000001.SZ"))

    with pytest.raises(SchemaError, match="stock_code"):
        recorder.apply_fill(wrong)

    assert recorder.get_cash() == pytest.approx(100000.0)
    assert recorder.get_positions() == {}


@pytest.mark.parametrize(("changes", "message"), [
    ({"mode": "SIMULATE"}, "mode"),
    ({"side": "SELL"}, "side"),
])
def test_fill_rejects_batch_and_order_mismatches(env, changes, message):
    _, recorder, _ = env
    recorder.set_cash(100000.0)
    planned = _fill(side="BUY", stock_code="600000.SH", requested=200, filled=200)
    _record_plan(recorder, [planned])
    fill = FillEvent.from_dict(dict(planned, **changes))

    with pytest.raises(SchemaError, match=message):
        recorder.apply_fill(fill)

    assert recorder.get_cash() == pytest.approx(100000.0)
    assert recorder.get_positions() == {}


def test_fill_rejects_decreasing_cumulative_quantity(env):
    _, recorder, _ = env
    recorder.set_cash(100000.0)
    partial = _fill(
        side="BUY", stock_code="600000.SH", requested=500,
        filled=200, price=10.0, status="PARTIAL",
    )
    _record_plan(recorder, [partial])
    recorder.apply_fill(FillEvent.from_dict(partial))

    with pytest.raises(SchemaError, match="decrease"):
        recorder.apply_fill(FillEvent.from_dict(dict(partial, filled_qty=100)))

    assert recorder.get_positions()["600000.SH"]["shares"] == 200


def test_fill_requested_quantity_cannot_change_after_first_event(env):
    _, recorder, _ = env
    recorder.set_cash(100000.0)
    partial = _fill(
        side="BUY", stock_code="600000.SH", requested=500,
        filled=200, price=10.0, status="PARTIAL",
    )
    _record_plan(recorder, [partial])
    recorder.apply_fill(FillEvent.from_dict(partial))

    changed = dict(partial, requested_qty=600, filled_qty=300)
    with pytest.raises(SchemaError, match="requested_qty changed"):
        recorder.apply_fill(FillEvent.from_dict(changed))


def test_sell_fill_cannot_credit_cash_beyond_ledger_position(env):
    _, recorder, _ = env
    recorder.set_cash(100000.0)
    recorder.upsert_position("000001.SZ", 100, 10.0)
    sell = _fill(requested=200, filled=200)
    _record_plan(recorder, [sell])

    with pytest.raises(SchemaError, match="exceeds ledger position"):
        recorder.apply_fill(FillEvent.from_dict(sell))

    assert recorder.get_cash() == pytest.approx(100000.0)
    assert recorder.get_positions()["000001.SZ"]["shares"] == 100
    assert recorder.get_fills(BATCH_ID) == []


def test_partial_fill_average_change_uses_cumulative_amount_delta(env):
    _, recorder, _ = env
    recorder.set_cash(100000.0)
    planned = _fill(
        side="BUY", stock_code="600000.SH", requested=200,
        filled=100, price=10.0, status="PARTIAL",
    )
    planned["target_value"] = 2_500.0
    _record_plan(recorder, [planned])
    recorder.apply_fill(FillEvent.from_dict(planned))
    final = dict(planned, status="FILLED", filled_qty=200, avg_price=11.0)
    recorder.apply_fill(FillEvent.from_dict(final))

    expected_fee = order_total_fee("BUY", 2200.0, DEFAULT_FEES)
    assert recorder.get_cash() == pytest.approx(100000.0 - 2200.0 - expected_fee)
    assert recorder.get_positions()["600000.SH"] == {
        "shares": 200,
        "avg_cost": pytest.approx(11.0),
        "opened_trade_date": "2026-07-14",
    }


def test_record_and_get_orders(env):
    _, recorder, _ = env
    recorder.record_batch(BATCH_ID, "2026-07-14", "SIMULATE", 2)
    recorder.record_orders(BATCH_ID, [
        {
            "client_order_id": "20260714001S",
            "stock_code": "000001.SZ",
            "instrument_qlib": "SZ000001",
            "side": "SELL",
            "quantity": 800,
            "price_type": "FIX",
            "limit_price": 10.0,
            "priority": 10,
            "reason": "topk_dropout",
        },
        {
            "client_order_id": "20260714002B",
            "stock_code": "600000.SH",
            "instrument_qlib": "SH600000",
            "side": "BUY",
            "quantity": 500,
            "price_type": "FIX",
            "limit_price": 11.0,
            "priority": 20,
            "reason": "topk_dropout",
        },
    ])
    orders = recorder.get_orders(BATCH_ID)
    assert len(orders) == 2
    assert orders[0]["side"] == "SELL"  # priority 升序
    assert orders[1]["stock_code"] == "600000.SH"
    # 重跑覆盖不翻倍
    recorder.record_orders(BATCH_ID, orders[:1])
    assert len(recorder.get_orders(BATCH_ID)) == 1


def test_stock_names_roundtrip(env):
    _, recorder, _ = env
    recorder.save_stock_names([
        {"stock_code": "600000.SH", "instrument": "SH600000", "name": "浦发银行"},
    ])
    assert recorder.get_stock_names()["600000.SH"] == "浦发银行"


def test_prediction_ranks_break_score_ties_by_instrument(env):
    _, recorder, _ = env
    recorder.save_predictions(
        "2026-07-22",
        {
            "SZ000002": 1.0,
            "SH600001": 1.0,
            "SH600000": 1.0,
        },
    )

    rows = recorder.get_predictions_by_date("2026-07-22")

    assert rows["SH600000"]["rank"] == 1
    assert rows["SH600001"]["rank"] == 2
    assert rows["SZ000002"]["rank"] == 3



def test_live_filled_updates_positions(env):
    bridge_root, recorder, importer = env
    # 账簿统一用 QMT 格式 stock_code；预置持仓，卖出后减少
    recorder.upsert_position("000001.SZ", 800, 10.0)
    fills = [
        _fill(),  # SELL 800 @10.45
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", requested=500, filled=500, price=10.10),
    ]
    _record_plan(recorder, fills)
    _write_fills(bridge_root, fills)
    n = importer.import_fills()
    assert n == 2

    positions = recorder.get_positions()
    assert "000001.SZ" not in positions  # 全部卖出后清仓
    assert positions["600000.SH"]["shares"] == 500


def test_simulate_fills_do_not_touch_positions(env):
    bridge_root, recorder, importer = env
    fills = [
        _fill(mode="SIMULATE", client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", requested=500, filled=500),
    ]
    _record_plan(recorder, fills)
    _write_fills(bridge_root, fills)
    importer.import_fills()
    assert recorder.get_positions() == {}
    # 但 fills 表中有记录（用于链路验证）
    fills = recorder.get_fills(BATCH_ID)
    assert len(fills) == 1
    assert fills[0]["mode"] == "SIMULATE"


def test_reimport_is_idempotent(env):
    bridge_root, recorder, importer = env
    fills = [
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", requested=500, filled=500),
    ]
    _record_plan(recorder, fills)
    _write_fills(bridge_root, fills)
    importer.import_fills()
    # 再写同一批次同内容（模拟重复投递），持仓不能翻倍
    _write_fills(bridge_root, fills)
    importer.import_fills()
    assert recorder.get_positions()["600000.SH"]["shares"] == 500


def test_non_terminal_and_rejected_do_not_change_positions(env):
    bridge_root, recorder, importer = env
    recorder.upsert_position("000001.SZ", 800, 10.0)
    fills = [
        _fill(status="ACCEPTED", filled=0),
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", status="REJECTED", requested=500, filled=0),
    ]
    _record_plan(recorder, fills)
    _write_fills(bridge_root, fills)
    importer.import_fills()
    positions = recorder.get_positions()
    assert positions["000001.SZ"]["shares"] == 800
    assert "600000.SH" not in positions


def test_partial_fill_updates_by_filled_qty(env):
    bridge_root, recorder, importer = env
    fills = [
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", status="PARTIAL", requested=500, filled=200),
    ]
    _record_plan(recorder, fills)
    _write_fills(bridge_root, fills)
    importer.import_fills()
    assert recorder.get_positions()["600000.SH"]["shares"] == 200


def test_cash_updated_by_live_fills_only(env):
    bridge_root, recorder, importer = env
    recorder.set_cash(100000.0)
    recorder.upsert_position("000001.SZ", 800, 10.0)
    live_fill = _fill()  # LIVE SELL 800 @10.45 -> +8360，另扣费用
    simulate_batch = "20260714_csi300_topk10_002"
    simulate_fill = _fill(
        client_order_id="20260714002001B", side="BUY", mode="SIMULATE",
        stock_code="600000.SH", requested=500, filled=500, price=10.10,
        batch_id=simulate_batch,
    )
    _record_plan(recorder, [live_fill])
    _record_plan(recorder, [simulate_fill], batch_id=simulate_batch)
    _write_fills(bridge_root, [live_fill])
    _write_fills(bridge_root, [simulate_fill], batch_id=simulate_batch)
    importer.import_fills()
    # 卖出 8360：佣金 max(8360*0.00025, 5)=5 + 过户费 0.0836 + 印花税 4.18
    sell_fee = order_total_fee("SELL", 8360.0, DEFAULT_FEES)
    assert sell_fee == pytest.approx(5 + 0.0836 + 4.18)
    expected = 100000.0 + 800 * 10.45 - sell_fee
    assert recorder.get_cash() == pytest.approx(expected)
    # 重复导入现金/费用均不重复累计
    _write_fills(bridge_root, [_fill()])
    importer.import_fills()
    assert recorder.get_cash() == pytest.approx(expected)
    fill_row = recorder.get_fills(BATCH_ID)[0]
    assert fill_row["applied_fee"] == pytest.approx(sell_fee)


def test_partial_then_full_fee_incremental(env):
    """部分成交后终态补齐：最低佣金全订单只收一次，费用按增量补扣。"""
    bridge_root, recorder, importer = env
    recorder.set_cash(100000.0)
    partial = _fill(client_order_id="20260714002B", side="BUY",
                    stock_code="600000.SH", status="PARTIAL",
                    requested=500, filled=200, price=10.0)
    _record_plan(recorder, [partial])
    _write_fills(bridge_root, [partial])
    importer.import_fills()
    fee_200 = order_total_fee("BUY", 2000.0, DEFAULT_FEES)  # 佣金触发最低 5 元
    assert recorder.get_cash() == pytest.approx(100000.0 - 2000.0 - fee_200)

    _write_fills(bridge_root, [
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", status="FILLED",
              requested=500, filled=500, price=10.0),
    ])
    importer.import_fills()
    fee_500 = order_total_fee("BUY", 5000.0, DEFAULT_FEES)
    assert recorder.get_cash() == pytest.approx(100000.0 - 5000.0 - fee_500)
    fill_row = recorder.get_fills(BATCH_ID)[0]
    assert fill_row["applied_fee"] == pytest.approx(fee_500)


def test_cash_flow_record_and_dedup(env):
    _, recorder, _ = env
    recorder.set_cash(100000.0)
    ok = recorder.record_cash_flow("2026-07-14", "DEPOSIT", 50000.0,
                                   note="追加资金", dedup_key="DEP_1")
    assert ok
    assert recorder.get_cash() == pytest.approx(150000.0)
    # 同 dedup_key 不重复入账
    assert not recorder.record_cash_flow("2026-07-14", "DEPOSIT", 50000.0,
                                         dedup_key="DEP_1")
    assert recorder.get_cash() == pytest.approx(150000.0)

    recorder.record_cash_flow("2026-07-14", "WITHDRAW", -20000.0)
    recorder.record_cash_flow("2026-07-14", "DIVIDEND", 380.0,
                              stock_code="600036.SH")
    assert recorder.get_cash() == pytest.approx(130380.0)
    # 外部出入金净额不含分红
    assert recorder.sum_external_flows("2026-07-14") == pytest.approx(30000.0)
    assert len(recorder.get_cash_flows()) == 3


@pytest.mark.parametrize(("flow_type", "amount", "note"), [
    ("DEPOSIT", -1.0, "bad sign"),
    ("WITHDRAW", 1.0, "bad sign"),
    ("CORRECTION", -1.0, ""),
    ("BONUS_SHARES", 0.0, "manual internal event"),
])
def test_manual_cash_flow_rejects_invalid_signs_and_internal_bonus(
    env, flow_type, amount, note,
):
    _, recorder, _ = env
    with pytest.raises(ValueError):
        recorder.record_cash_flow(
            "2026-07-14", flow_type, amount, note=note,
        )


def test_correction_is_investment_adjustment_not_external_flow(env):
    _, recorder, _ = env
    recorder.set_cash(1000.0)
    recorder.record_cash_flow(
        "2026-07-14", "CORRECTION", -10.0, note="broker reconciliation",
    )
    assert recorder.get_cash() == pytest.approx(990.0)
    assert recorder.sum_external_flows("2026-07-14") == 0.0


def test_apply_bonus_shares(env):
    _, recorder, _ = env
    recorder.upsert_position("600036.SH", 1000, 30.0)
    assert recorder.apply_bonus_shares("600036.SH", 300)
    pos = recorder.get_positions()["600036.SH"]
    assert pos["shares"] == 1300
    assert pos["avg_cost"] == pytest.approx(30.0 * 1000 / 1300)
    # 无持仓返回 False
    assert not recorder.apply_bonus_shares("000001.SZ", 100)


def test_sum_fees_by_date(env):
    bridge_root, recorder, importer = env
    recorder.set_cash(100000.0)
    recorder.upsert_position("000001.SZ", 800, 10.0)
    fills = [_fill()]
    _record_plan(recorder, fills)
    _write_fills(bridge_root, fills)
    importer.import_fills()
    sell_fee = order_total_fee("SELL", 8360.0, DEFAULT_FEES)
    assert recorder.sum_fees_by_date("2026-07-14") == pytest.approx(sell_fee)
    assert recorder.sum_fees_by_date("2026-07-13") == 0.0


def test_reprice_fees_refunds_lower_rate_and_is_idempotent(env):
    _, recorder, _ = env
    recorder.set_cash(200000.0)
    buy = _fill(
        side="BUY", stock_code="600000.SH", requested=1000,
        filled=1000, price=100.0,
    )
    _record_plan(recorder, [buy])
    recorder.apply_fill(FillEvent.from_dict(buy))

    old_fee = order_total_fee("BUY", 100000.0, DEFAULT_FEES)
    lower_fees = {**DEFAULT_FEES, "commission_rate": 0.00020}
    new_fee = order_total_fee("BUY", 100000.0, lower_fees)
    cash_before = recorder.get_cash()

    repricer = LiveRecorder(recorder.db_path, fees=lower_fees)
    adjustment = repricer.reprice_fees_by_date("2026-07-14")

    assert adjustment == pytest.approx(new_fee - old_fee)
    assert repricer.get_cash() == pytest.approx(cash_before + old_fee - new_fee)
    assert repricer.sum_fees_by_date("2026-07-14") == pytest.approx(new_fee)
    assert repricer.get_fills(BATCH_ID)[0]["applied_fee"] == pytest.approx(new_fee)

    cash_after = repricer.get_cash()
    assert repricer.reprice_fees_by_date("2026-07-14") == pytest.approx(0.0)
    assert repricer.get_cash() == pytest.approx(cash_after)


def _write_broker_snapshot(bridge_root: Path, rows: list, batch_id=BATCH_ID,
                           with_done=True):
    outbound = bridge_root / "outbound"
    outbound.mkdir(parents=True, exist_ok=True)
    (outbound / f"account_{batch_id}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8",
    )
    if with_done:
        (outbound / f"account_{batch_id}.done").write_text("done\n", encoding="utf-8")


def _snapshot_rows(cash=123456.78, positions=(("688223.SH", 244500),)):
    rows = [{
        "type": "account_snapshot", "batch_id": BATCH_ID,
        "trade_date": "2026-07-14", "account_id": "8881352838",
        "available_cash": cash, "total_asset": 9876543.21,
        "market_value": 9000000.0, "frozen_cash": 0.0,
        "ts": "2026-07-14T14:57:00",
    }]
    rows += [{
        "type": "broker_position", "batch_id": BATCH_ID,
        "trade_date": "2026-07-14", "stock_code": code, "shares": shares,
        "can_use_volume": 0, "avg_cost": 4.14, "market_value": shares * 4.14,
        "ts": "2026-07-14T14:57:00",
    } for code, shares in positions]
    return rows


def test_import_broker_snapshot_stores_and_archives(env):
    bridge_root, recorder, importer = env
    recorder.record_batch(BATCH_ID, "2026-07-14", "LIVE", 1)
    _write_broker_snapshot(bridge_root, _snapshot_rows())

    assert importer.import_broker_snapshots() == 1

    account = recorder.get_broker_account_snapshot("2026-07-14")
    assert account["available_cash"] == pytest.approx(123456.78)
    assert recorder.get_broker_positions("2026-07-14") == {"688223.SH": 244500}
    assert recorder.get_broker_position_market_values("2026-07-14") == {
        "688223.SH": pytest.approx(244500 * 4.14),
    }
    assert not list((bridge_root / "outbound").glob("account_*"))
    assert len(list((bridge_root / "archive").glob("account_*"))) == 2


def test_import_broker_snapshot_is_idempotent_and_overwrites(env):
    bridge_root, recorder, importer = env
    recorder.record_batch(BATCH_ID, "2026-07-14", "LIVE", 1)
    _write_broker_snapshot(bridge_root, _snapshot_rows())
    importer.import_broker_snapshots()

    # 同批次重新导入（例如手工补一份）应覆盖而不是累加持仓行
    _write_broker_snapshot(
        bridge_root,
        _snapshot_rows(cash=999.0, positions=(("600000.SH", 100),)),
    )
    assert importer.import_broker_snapshots() == 1

    assert recorder.get_broker_account_snapshot("2026-07-14")["available_cash"] \
        == pytest.approx(999.0)
    assert recorder.get_broker_positions("2026-07-14") == {"600000.SH": 100}


def test_latest_empty_broker_snapshot_does_not_reuse_older_positions(env):
    _, recorder, _ = env
    later_batch = "20260714_csi300_topk10_002"
    account = _snapshot_rows()[0]
    position = _snapshot_rows()[1]
    recorder.record_batch(BATCH_ID, "2026-07-14", "LIVE", 1)
    recorder.save_broker_snapshot(BATCH_ID, account, [position])
    recorder.record_batch(later_batch, "2026-07-14", "LIVE", 0)
    recorder.save_broker_snapshot(later_batch, account, [])

    assert recorder.get_broker_positions("2026-07-14") == {}
    assert recorder.get_broker_position_market_values("2026-07-14") == {}


def test_broker_snapshot_without_done_is_not_imported(env):
    bridge_root, recorder, importer = env
    recorder.record_batch(BATCH_ID, "2026-07-14", "LIVE", 1)
    _write_broker_snapshot(bridge_root, _snapshot_rows(), with_done=False)

    assert importer.import_broker_snapshots() == 0
    assert recorder.get_broker_account_snapshot("2026-07-14") is None
    assert (bridge_root / "outbound" / f"account_{BATCH_ID}.jsonl").exists()


def test_broker_snapshot_for_unknown_batch_is_rejected(env):
    bridge_root, _, importer = env
    _write_broker_snapshot(bridge_root, _snapshot_rows())

    with pytest.raises(SchemaError):
        importer.import_broker_snapshots()


def test_broker_snapshot_survives_missing_account_row(env):
    """ACCOUNT 查询为空时只落持仓，账户快照缺失不阻断导入。"""
    bridge_root, recorder, importer = env
    recorder.record_batch(BATCH_ID, "2026-07-14", "LIVE", 1)
    rows = [r for r in _snapshot_rows() if r["type"] != "account_snapshot"]
    _write_broker_snapshot(bridge_root, rows)

    assert importer.import_broker_snapshots() == 1
    assert recorder.get_broker_account_snapshot("2026-07-14") is None
    assert recorder.get_broker_positions("2026-07-14") == {"688223.SH": 244500}


def test_reconcile_counts(env):
    bridge_root, recorder, importer = env
    recorder.upsert_position("000001.SZ", 800, 10.0)
    fills = [
        _fill(),
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", status="REJECTED", requested=500, filled=0),
    ]
    _record_plan(recorder, fills, planned_orders=3)
    _write_fills(bridge_root, fills)
    importer.import_fills()
    result = importer.reconcile(BATCH_ID)
    assert result["planned"] == 3
    assert result["terminal"] == 2
    assert result["missing"] == 1
