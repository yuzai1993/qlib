"""OrderPlanner：买卖意图 → 可执行 SignalOrder 列表。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.order_planner import OrderPlanner, PlanError


def _planner(**overrides):
    cfg = {
        "buy_slippage": 0.01,
        "sell_slippage": 0.01,
        "max_orders_per_day": 20,
        "trade_unit": 100,
    }
    cfg.update(overrides)
    return OrderPlanner(cfg)


BATCH_ID = "20260714_csi300_topk10_001"
TRADE_DATE = "2026-07-14"


def test_sell_before_buy_with_priority_and_seq():
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_value": 15_000.0},
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 800},
    ]
    prev_close = {"SH600000": 10.00, "SZ000001": 20.00}
    orders = _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE, batch_seq=1)

    assert [o.side for o in orders] == ["SELL", "BUY"]
    sell, buy = orders
    assert sell.priority < buy.priority
    assert sell.client_order_id == "20260714001001S"
    assert buy.client_order_id == "20260714001002B"
    assert sell.stock_code == "000001.SZ"
    assert buy.stock_code == "600000.SH"
    assert sell.instrument_qlib == "SZ000001"
    assert sell.quantity == 800
    assert sell.target_value == 0.0
    assert buy.quantity == 0
    assert buy.target_value == pytest.approx(15_000.0)
    assert {sell.price_type, buy.price_type} == {"CLOSE_AUCTION_LIMIT"}
    assert sell.limit_price == buy.limit_price == 0.0


def test_same_day_batches_have_distinct_client_order_ids():
    intents = [{"instrument": "SH600000", "direction": "BUY", "target_value": 10_000.0}]
    prev_close = {"SH600000": 10.0}
    first = _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE, batch_seq=1)
    second = _planner().plan(
        intents, prev_close, "20260714_csi300_topk10_002", TRADE_DATE, batch_seq=2,
    )
    assert first[0].client_order_id != second[0].client_order_id


def test_after_hours_close_orders_do_not_use_previous_close_or_slippage():
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_value": 10_000.0},
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 100},
    ]
    orders = _planner().plan(intents, {}, BATCH_ID, TRADE_DATE)
    sell = next(o for o in orders if o.side == "SELL")
    buy = next(o for o in orders if o.side == "BUY")
    assert sell.limit_price == buy.limit_price == 0.0
    assert sell.price_type == buy.price_type == "CLOSE_AUCTION_LIMIT"


def test_planner_emits_its_configured_signal_price_type():
    orders = _planner(
        execution_session="AFTER_HOURS_FIXED_PRICE",
        signal_price_type="AFTER_HOURS_CLOSE",
    ).plan(
        [{"instrument": "SH600000", "direction": "BUY", "target_value": 10_000.0}],
        {}, BATCH_ID, TRADE_DATE,
    )

    assert [order.price_type for order in orders] == ["AFTER_HOURS_CLOSE"]


def test_planner_rejects_signal_type_mismatched_to_execution_session():
    with pytest.raises(PlanError, match="signal_price_type"):
        _planner(
            execution_session="CLOSE_AUCTION",
            signal_price_type="AFTER_HOURS_CLOSE",
        )


def test_sell_quantity_rounded_down_to_lot_and_zero_dropped():
    intents = [
        {"instrument": "SH600000", "direction": "SELL", "target_shares": 150},
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 99},
    ]
    prev_close = {"SH600000": 10.0, "SZ000001": 10.0}
    orders = _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE)
    assert len(orders) == 1
    assert orders[0].quantity == 100


def test_buy_target_value_does_not_require_previous_close():
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_value": 12_345.0},
        {"instrument": "SZ000001", "direction": "BUY", "target_value": 9_876.0},
    ]
    orders = _planner().plan(intents, {}, BATCH_ID, TRADE_DATE)
    assert [o.target_value for o in orders] == [12_345.0, 9_876.0]
    assert all(o.quantity == 0 for o in orders)


def test_same_code_same_side_merged():
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_value": 12_000.0},
        {"instrument": "SH600000", "direction": "BUY", "target_value": 3_000.0},
    ]
    prev_close = {"SH600000": 10.0}
    orders = _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE)
    assert len(orders) == 1
    assert orders[0].quantity == 0
    assert orders[0].target_value == pytest.approx(15_000.0)


def test_max_orders_exceeded_raises():
    intents = [
        {"instrument": f"SH60000{i}", "direction": "BUY", "target_value": 10_000.0}
        for i in range(10)
    ]
    prev_close = {f"SH60000{i}": 10.0 for i in range(10)}
    with pytest.raises(PlanError):
        _planner(max_orders_per_day=5).plan(intents, prev_close, BATCH_ID, TRADE_DATE)


def test_output_passes_schema_validation():
    from live_trading.modules.signal_schema import validate_order
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_value": 15_000.0},
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 800},
    ]
    prev_close = {"SH600000": 10.00, "SZ000001": 20.00}
    for o in _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE):
        validate_order(o)


def test_per_intent_reason_overrides_the_batch_default():
    """真阶梯一张批次里两种成因：到期卖 cohort_due、当日买 cohort_layer。
    批次级的单一 reason 装不下。"""
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_value": 15_000.0,
         "reason": "cohort_layer"},
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 800,
         "reason": "cohort_due"},
    ]
    orders = _planner().plan(intents, {}, BATCH_ID, TRADE_DATE)

    assert [o.reason for o in orders] == ["cohort_due", "cohort_layer"]


def test_intents_without_a_reason_still_take_the_batch_default():
    intents = [
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 800},
    ]
    orders = _planner().plan(intents, {}, BATCH_ID, TRADE_DATE)

    assert orders[0].reason == "topk_dropout"


def test_a_final_share_count_is_not_rounded_down_again():
    """账本可能因送股持有零股，清仓卖单就是零股。再取整一次会剩下 50 股
    永远卖不掉，而那一层已经在账本里被标记到期。"""
    intents = [
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 150,
         "shares_are_final": True},
    ]
    orders = _planner().plan(intents, {}, BATCH_ID, TRADE_DATE)

    assert orders[0].quantity == 150


def test_a_share_count_that_is_not_final_is_still_rounded_down():
    intents = [
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 150},
    ]
    orders = _planner().plan(intents, {}, BATCH_ID, TRADE_DATE)

    assert orders[0].quantity == 100
