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
        {"instrument": "SH600000", "direction": "BUY", "target_shares": 500},
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 800},
    ]
    prev_close = {"SH600000": 10.00, "SZ000001": 20.00}
    orders = _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE)

    assert [o.side for o in orders] == ["SELL", "BUY"]
    sell, buy = orders
    assert sell.priority < buy.priority
    assert sell.client_order_id == "20260714001S"
    assert buy.client_order_id == "20260714002B"
    assert sell.stock_code == "000001.SZ"
    assert buy.stock_code == "600000.SH"
    assert sell.instrument_qlib == "SZ000001"


def test_limit_price_with_slippage():
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_shares": 100},
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 100},
    ]
    prev_close = {"SH600000": 10.00, "SZ000001": 20.00}
    orders = _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE)
    sell = next(o for o in orders if o.side == "SELL")
    buy = next(o for o in orders if o.side == "BUY")
    assert sell.limit_price == pytest.approx(19.80)  # 20 * (1 - 0.01)
    assert buy.limit_price == pytest.approx(10.10)   # 10 * (1 + 0.01)


def test_quantity_rounded_down_to_lot_and_zero_dropped():
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_shares": 150},
        {"instrument": "SZ000001", "direction": "BUY", "target_shares": 99},
    ]
    prev_close = {"SH600000": 10.0, "SZ000001": 10.0}
    orders = _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE)
    assert len(orders) == 1
    assert orders[0].quantity == 100


def test_missing_or_invalid_price_dropped():
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_shares": 200},
        {"instrument": "SZ000001", "direction": "BUY", "target_shares": 200},
    ]
    prev_close = {"SH600000": 0.0}  # SZ000001 缺失，SH600000 非法
    orders = _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE)
    assert orders == []


def test_same_code_same_side_merged():
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_shares": 200},
        {"instrument": "SH600000", "direction": "BUY", "target_shares": 300},
    ]
    prev_close = {"SH600000": 10.0}
    orders = _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE)
    assert len(orders) == 1
    assert orders[0].quantity == 500


def test_max_orders_exceeded_raises():
    intents = [
        {"instrument": f"SH60000{i}", "direction": "BUY", "target_shares": 100}
        for i in range(10)
    ]
    prev_close = {f"SH60000{i}": 10.0 for i in range(10)}
    with pytest.raises(PlanError):
        _planner(max_orders_per_day=5).plan(intents, prev_close, BATCH_ID, TRADE_DATE)


def test_output_passes_schema_validation():
    from live_trading.modules.signal_schema import validate_order
    intents = [
        {"instrument": "SH600000", "direction": "BUY", "target_shares": 500},
        {"instrument": "SZ000001", "direction": "SELL", "target_shares": 800},
    ]
    prev_close = {"SH600000": 10.00, "SZ000001": 20.00}
    for o in _planner().plan(intents, prev_close, BATCH_ID, TRADE_DATE):
        validate_order(o)
