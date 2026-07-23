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
from live_trading.modules.signal_schema import FillEvent, SchemaError

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


def _record_plan(recorder, fills, batch_id=BATCH_ID, mode=None, planned_orders=None):
    """为回执测试写入对应的原始计划，模拟正常发布链路。"""
    mode = mode or fills[0]["mode"]
    recorder.record_batch(
        batch_id, "2026-07-14", mode,
        planned_orders if planned_orders is not None else len(fills),
    )
    recorder.record_orders(batch_id, [
        {
            "client_order_id": f["client_order_id"],
            "stock_code": f["stock_code"],
            "instrument_qlib": "",
            "side": f["side"],
            "quantity": f["requested_qty"],
            "price_type": "FIX",
            "limit_price": max(float(f["avg_price"]), 1.0),
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
    ({"requested_qty": 300, "filled_qty": 200}, "requested_qty"),
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
    _record_plan(recorder, [planned])
    recorder.apply_fill(FillEvent.from_dict(planned))
    final = dict(planned, status="FILLED", filled_qty=200, avg_price=11.0)
    recorder.apply_fill(FillEvent.from_dict(final))

    expected_fee = order_total_fee("BUY", 2200.0, DEFAULT_FEES)
    assert recorder.get_cash() == pytest.approx(100000.0 - 2200.0 - expected_fee)
    assert recorder.get_positions()["600000.SH"] == {
        "shares": 200,
        "avg_cost": pytest.approx(11.0),
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
