"""真阶梯下单意图：买清单不去重、预算含预估卖出所得、零股卖单只在清仓时合法。"""

import pandas as pd
import pytest

from live_trading.modules.cohort_order_manager import CohortOrderManager
from live_trading.modules.cohort_store import CohortState

CONFIG = {
    "strategy": {
        "class": "CohortLadderStrategy",
        "topk": 3,
        "horizon": 5,
        "risk_degree": 0.90,
    },
    "exchange": {"trade_unit": 100},
    "fees": {
        "commission_rate": 0.00020,
        "min_commission": 5.0,
        "stamp_duty_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
        "dividend_tax_rate": 0.20,
    },
}


def _scores(mapping):
    return pd.Series(mapping, dtype=float)


def _due_layer(shares):
    """构造一个层数已满、最老层为 shares 的账本状态。"""
    return CohortState(
        layers=tuple(
            (f"2026-08-1{i}", dict(shares) if i == 0 else {}) for i in range(5)
        ),
        pending={},
    )


def test_buys_top_k_without_dedup_against_existing_layers():
    # SH600000 已被两层持有，仍应再次入选（连续上榜自动加仓）
    state = CohortState(
        layers=(
            ("2026-08-18", {"SH600000": 100}),
            ("2026-08-19", {"SH600000": 100}),
        ),
        pending={},
    )
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores(
            {"SH600000": 3.0, "SZ000001": 2.0, "SH600519": 1.0, "SH601318": 0.5}
        ),
        cohort_state=state,
        broker_positions={"SH600000": 200},
        cash=1_000_000.0,
        close_prices={
            "SH600000": 10.0, "SZ000001": 20.0, "SH600519": 30.0, "SH601318": 5.0,
        },
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    assert [o["stock_code"] for o in buys] == ["SH600000", "SZ000001", "SH600519"]


def test_each_buy_carries_one_third_of_the_daily_layer_budget():
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SH600000": 3.0, "SZ000001": 2.0, "SH600519": 1.0}),
        cohort_state=CohortState(),
        broker_positions={},
        cash=1_000_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0, "SH600519": 30.0},
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    # 预算 = 1_000_000 × 0.90 / 5 = 180_000，三等分 = 60_000
    assert len(buys) == 3
    for order in buys:
        assert order["quantity"] == 0          # BUY 由券商按 target_value 定量
        assert order["target_value"] == pytest.approx(60_000.0)


def test_budget_includes_estimated_sell_proceeds():
    # 到期层 1000 股 @ 20 元 = 20_000 元毛收入
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SH600000": 3.0, "SH600519": 2.0, "SH601318": 1.0}),
        cohort_state=_due_layer({"SZ000001": 1000}),
        broker_positions={"SZ000001": 1000},
        cash=100_000.0,
        close_prices={
            "SZ000001": 20.0, "SH600000": 10.0, "SH600519": 30.0, "SH601318": 5.0,
        },
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    # 目标 180_000 > 快照现金 100_000；加上卖出所得后现金约 119_986 元，
    # 预算 = min(180_000, 119_986) 被现金卡住，故必须显著高于 100_000
    assert sum(o["target_value"] for o in buys) > 119_000
    assert sum(o["target_value"] for o in buys) < 120_000


def test_budget_without_due_layer_falls_back_to_snapshot_cash():
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SH600000": 3.0, "SZ000001": 2.0, "SH600519": 1.0}),
        cohort_state=CohortState(),
        broker_positions={},
        cash=90_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0, "SH600519": 30.0},
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    assert sum(o["target_value"] for o in buys) == pytest.approx(90_000.0)


def test_due_layer_becomes_sell_orders_capped_by_broker_position():
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=_due_layer({"SH600000": 500}),
        broker_positions={"SH600000": 300},  # 券商只有 300 股
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    sells = [o for o in orders if o["side"] == "SELL"]
    assert len(sells) == 1
    assert sells[0]["stock_code"] == "SH600000"
    assert sells[0]["quantity"] == 300
    assert sells[0]["target_value"] == 0.0
    assert sells[0]["reason"] == "cohort_due"


def test_pending_remnant_is_retried_as_sell():
    state = CohortState(layers=(("2026-08-19", {}),), pending={"SH600000": 200})
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=state,
        broker_positions={"SH600000": 200},
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    sells = [o for o in orders if o["side"] == "SELL"]
    assert [(o["stock_code"], o["quantity"]) for o in sells] == [("SH600000", 200)]


def test_odd_lot_sell_allowed_only_when_it_clears_the_position():
    # 送股后 pending 有 120 股，券商也正好 120 股 → 整笔卖出合法
    state = CohortState(layers=(("2026-08-19", {}),), pending={"SH600000": 120})
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=state,
        broker_positions={"SH600000": 120},
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    sells = [o for o in orders if o["side"] == "SELL"]
    assert [(o["stock_code"], o["quantity"]) for o in sells] == [("SH600000", 120)]


def test_odd_lot_sell_rounds_down_when_position_remains():
    # 台账要卖 120 股，但券商还有 500 股 → 只能卖整百
    state = CohortState(layers=(("2026-08-19", {}),), pending={"SH600000": 120})
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=state,
        broker_positions={"SH600000": 500},
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    sells = [o for o in orders if o["side"] == "SELL"]
    assert [(o["stock_code"], o["quantity"]) for o in sells] == [("SH600000", 100)]


def test_sub_lot_sell_below_one_lot_is_dropped_when_position_remains():
    state = CohortState(layers=(("2026-08-19", {}),), pending={"SH600000": 40})
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=state,
        broker_positions={"SH600000": 500},
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    assert [o for o in orders if o["side"] == "SELL"] == []


def test_names_missing_a_close_price_are_not_buyable():
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SH600000": 3.0, "SZ000001": 2.0}),
        cohort_state=CohortState(),
        broker_positions={},
        cash=1_000_000.0,
        close_prices={"SH600000": 10.0},  # SZ000001 无价
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    assert [o["stock_code"] for o in buys] == ["SH600000"]


def test_universe_filtered_names_are_never_bought():
    """universe_gate 把剔除的票置 NaN，排序时必须整只丢掉而不是当成最低分。"""
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores(
            {"SH600000": float("nan"), "SZ000001": 2.0, "SH600519": 1.0}
        ),
        cohort_state=CohortState(),
        broker_positions={},
        cash=1_000_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0, "SH600519": 30.0},
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    assert [o["stock_code"] for o in buys] == ["SZ000001", "SH600519"]


def test_sells_come_before_buys():
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=_due_layer({"SH600000": 500}),
        broker_positions={"SH600000": 500},
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    sides = [o["side"] for o in orders]
    assert sides.index("SELL") < sides.index("BUY")
