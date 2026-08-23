"""CohortLadderStrategy 接入层：下单序列是否符合阶梯规则。

用假交易所驱动，只验证策略自己负责的部分：到期日、重复持有加仓、卖不掉挂账重试、
按 1/h 分配预算。撮合细节由 Qlib 自己的 Exchange 负责，不在这里重复测。
"""

from __future__ import annotations

import pandas as pd
import pytest

from qlib.backtest.decision import Order
from qlib.contrib.strategy.signal_strategy import CohortLadderStrategy


class FakeExchange:
    """成交价恒为 10、手数 100、全部可交易的极简交易所。"""

    def __init__(self, *, untradable: set[str] | None = None, price: float = 10.0):
        self.untradable = untradable or set()
        self.price = price

    def is_stock_tradable(self, stock_id, start_time, end_time, direction=None):
        return stock_id not in self.untradable

    def check_order(self, order):
        return order.stock_id not in self.untradable

    def get_deal_price(self, stock_id, start_time, end_time, direction=None):
        return self.price

    def get_factor(self, stock_id, start_time, end_time):
        return 1.0

    def round_amount_by_trade_unit(self, amount, factor):
        return float(int(amount // 100) * 100)

    def deal_order(self, order, position=None, trade_account=None):
        order.deal_amount = order.amount
        if position is not None:
            position.amounts[order.stock_id] = (
                position.amounts.get(order.stock_id, 0.0) - order.amount
            )
            if position.amounts[order.stock_id] <= 0:
                position.amounts.pop(order.stock_id)
            position.cash += order.amount * self.price
        return order.amount * self.price, 0.0, self.price


class FakePosition:
    def __init__(self, amounts=None, cash=1_000_000.0):
        self.amounts = dict(amounts or {})
        self.cash = cash

    def get_stock_amount_dict(self):
        return dict(self.amounts)

    def get_cash(self):
        return self.cash

    def calculate_value(self):
        return self.cash + sum(self.amounts.values()) * 10.0


class FakeCalendar:
    def __init__(self):
        self.step = 0

    def get_trade_step(self):
        return self.step

    def get_step_time(self, step=None, shift=0):
        step = self.step if step is None else step
        base = pd.Timestamp("2020-01-02") + pd.Timedelta(days=step - shift)
        return base, base


class FakeSignal:
    def __init__(self, scores_by_step):
        self.scores_by_step = scores_by_step
        self.step = 0

    def get_signal(self, start_time, end_time):
        return self.scores_by_step[self.step]


def _scores(names):
    return pd.Series(
        [float(len(names) - i) for i in range(len(names))], index=names, dtype=float
    )


class FakeInfra:
    """只实现 BaseStrategy 用到的 get()。"""

    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, key):
        return self.mapping[key]


class FakeAccount:
    def __init__(self, position):
        self.current_position = position


def _build(
    scores_by_step,
    *,
    topk,
    horizon,
    untradable=None,
    position=None,
    force_sell_rank=None,
    refill_force_sell=False,
):
    strategy = CohortLadderStrategy(
        topk=topk,
        horizon=horizon,
        forbid_all_trade_at_limit=False,
        risk_degree=0.9,
        force_sell_rank=force_sell_rank,
        refill_force_sell=refill_force_sell,
        signal=pd.Series(dtype=float),
    )
    strategy._trade_exchange = FakeExchange(untradable=untradable)
    strategy.level_infra = FakeInfra({"trade_calendar": FakeCalendar()})
    strategy.common_infra = FakeInfra(
        {"trade_account": FakeAccount(position or FakePosition())}
    )
    strategy.signal = FakeSignal(scores_by_step)
    return strategy


def _run_day(strategy, step):
    strategy.trade_calendar.step = step
    strategy.signal.step = step
    decision = strategy.generate_trade_decision()
    orders = decision.order_list
    buys = {o.stock_id: o.amount for o in orders if o.direction == Order.BUY}
    sells = {o.stock_id: o.amount for o in orders if o.direction == Order.SELL}
    # 把成交回填到持仓，模拟执行器
    position = strategy.trade_position
    for code, amount in sells.items():
        position.amounts[code] = position.amounts.get(code, 0.0) - amount
        if position.amounts[code] <= 0:
            position.amounts.pop(code)
        position.cash += amount * 10.0
    for code, amount in buys.items():
        position.amounts[code] = position.amounts.get(code, 0.0) + amount
        position.cash -= amount * 10.0
    return buys, sells


def test_no_sell_before_horizon_then_first_cohort_exits_on_time():
    """持满 horizon 天才卖，第 horizon+1 天卖掉第一层。"""
    scores = [_scores([f"D{step}A", f"D{step}B"]) for step in range(5)]
    strategy = _build(scores, topk=2, horizon=3)

    for step in range(3):
        buys, sells = _run_day(strategy, step)
        assert sells == {}, f"step {step} 不该有卖单"
        assert set(buys) == {f"D{step}A", f"D{step}B"}

    _, sells = _run_day(strategy, 3)
    assert set(sells) == {"D0A", "D0B"}

    _, sells = _run_day(strategy, 4)
    assert set(sells) == {"D1A", "D1B"}


def test_persistently_top_ranked_name_is_bought_again_by_each_cohort():
    """连续上榜就自动加仓——这是阶梯与 25 槽 TopkDropout 的关键分歧点。"""
    scores = [_scores(["WINNER", f"OTHER{step}"]) for step in range(3)]
    strategy = _build(scores, topk=2, horizon=3)

    total = 0.0
    for step in range(3):
        buys, _ = _run_day(strategy, step)
        assert "WINNER" in buys, f"step {step} 应继续买入常驻强票"
        total += buys["WINNER"]

    assert strategy.trade_position.amounts["WINNER"] == pytest.approx(total)


def test_duplicate_holding_exits_in_tranches_not_all_at_once():
    scores = [_scores(["WINNER", f"OTHER{step}"]) for step in range(5)]
    strategy = _build(scores, topk=2, horizon=2)

    day0, _ = _run_day(strategy, 0)
    _run_day(strategy, 1)
    _, sells = _run_day(strategy, 2)

    assert sells["WINNER"] == pytest.approx(day0["WINNER"])
    assert strategy.trade_position.amounts["WINNER"] > 0


def test_unsellable_due_name_is_retried_the_next_day():
    """到期那天停牌卖不掉，次日必须重试，不能把这笔持仓从台账丢掉。"""
    scores = [_scores([f"D{step}A"]) for step in range(4)]
    strategy = _build(scores, topk=1, horizon=1)

    day0, _ = _run_day(strategy, 0)
    assert "D0A" in day0

    strategy.trade_exchange.untradable = {"D0A"}
    _, sells = _run_day(strategy, 1)
    assert "D0A" not in sells, "停牌当天卖不出去"

    strategy.trade_exchange.untradable = set()
    _, sells = _run_day(strategy, 2)
    assert sells["D0A"] == pytest.approx(day0["D0A"])


def test_each_cohort_spends_one_over_horizon_of_target_exposure():
    scores = [_scores(["A", "B"]) for _ in range(2)]
    strategy = _build(scores, topk=2, horizon=5)

    buys, _ = _run_day(strategy, 0)

    # 1,000,000 × 0.9 / 5 = 180,000，两只均分 90,000，价 10 → 9000 股
    assert buys == {"A": pytest.approx(9000.0), "B": pytest.approx(9000.0)}


def test_unbuyable_top_name_is_replaced_by_the_next_rank():
    scores = [_scores(["A", "B", "C"])]
    strategy = _build(scores, topk=2, horizon=5, untradable={"A"})

    buys, _ = _run_day(strategy, 0)

    assert set(buys) == {"B", "C"}


def test_ledger_restarts_when_the_backtest_replays_from_step_zero():
    scores = [_scores([f"D{step}A"]) for step in range(3)]
    strategy = _build(scores, topk=1, horizon=1)

    _run_day(strategy, 0)
    _run_day(strategy, 1)
    strategy.common_infra.mapping["trade_account"] = FakeAccount(FakePosition())
    _, sells = _run_day(strategy, 0)

    assert sells == {}, "复用实例重跑时账龄必须归零"


def test_rejects_invalid_topk_and_horizon():
    with pytest.raises(ValueError):
        CohortLadderStrategy(topk=0, horizon=5, signal=pd.Series(dtype=float))
    with pytest.raises(ValueError):
        CohortLadderStrategy(topk=5, horizon=0, signal=pd.Series(dtype=float))


def test_force_sell_exits_a_name_before_horizon_when_it_drops_out():
    """掉出 force_sell_rank 的票立刻清掉所有分层，不等到期。"""
    day0 = _scores(["A", "B", "C", "D"])
    day1 = pd.Series({"A": 4.0, "C": 3.0, "D": 2.0, "B": 1.0})
    strategy = _build([day0, day1], topk=2, horizon=5, force_sell_rank=2)

    day0_buys, _ = _run_day(strategy, 0)
    assert set(day0_buys) == {"A", "B"}

    _, sells = _run_day(strategy, 1)
    assert sells["B"] == pytest.approx(day0_buys["B"])
    assert "A" not in sells


def test_force_sell_exits_a_name_missing_from_scores():
    day0 = _scores(["A", "B", "C"])
    day1 = _scores(["A", "C", "D"])
    strategy = _build([day0, day1], topk=2, horizon=5, force_sell_rank=100)

    day0_buys, _ = _run_day(strategy, 0)
    _, sells = _run_day(strategy, 1)

    assert sells["B"] == pytest.approx(day0_buys["B"])


def test_force_sell_refill_buys_the_next_new_name():
    """强平 B 之后，当日 top2 仍是 A/C，再顺延补一只还没拿着的 D。"""
    day0 = _scores(["A", "B", "C", "D", "E"])
    day1 = pd.Series({"A": 5.0, "C": 4.0, "D": 3.0, "E": 2.0, "B": 1.0})
    strategy = _build(
        [day0, day1],
        topk=2,
        horizon=5,
        force_sell_rank=2,
        refill_force_sell=True,
    )

    _run_day(strategy, 0)
    buys, sells = _run_day(strategy, 1)

    assert "B" in sells
    assert set(buys) == {"A", "C", "D"}
    assert buys["D"] > 0
