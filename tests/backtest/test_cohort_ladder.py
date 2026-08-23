"""真阶梯（cohort ladder）台账与选股逻辑。

主格 top-k × h 的评估年化 `mean(p) × 238/h` 恒等于「k·h 个等额仓位、每日买入当日
top-k、每只持满 h 天到期无条件卖出」这条阶梯组合的算术年化。这里测的就是这条阶梯的
簿记规则，关键是它与 TopkDropout 的两处根本区别：退出按持有天数而非打分排名；
同一只票可以被多个分层同时持有。
"""

from __future__ import annotations

import pandas as pd
import pytest

from qlib.contrib.strategy.cohort_ladder import (
    CohortLedger,
    cohort_budget,
    force_sell_names,
    ledger_sell_amounts,
    select_ladder_buys,
    select_ladder_refills,
)


def _scores(names: list[str]) -> pd.Series:
    return pd.Series(
        [float(len(names) - i) for i in range(len(names))],
        index=names,
        dtype=float,
    )


class TestSelectLadderBuys:
    def test_takes_top_k_by_score(self):
        scores = _scores(["A", "B", "C", "D"])
        assert select_ladder_buys(scores, k=2) == ("A", "B")

    def test_skips_unbuyable_and_substitutes_next_rank(self):
        """评估的候选池先剔 t+1 涨停再取 top-k，等价于往下顺延。"""
        scores = _scores(["A", "B", "C", "D"])
        picked = select_ladder_buys(
            scores, k=2, is_buyable=lambda name: name in {"B", "C", "D"}
        )
        assert picked == ("B", "C")

    def test_returns_fewer_than_k_when_pool_is_exhausted(self):
        scores = _scores(["A", "B", "C"])
        assert select_ladder_buys(scores, k=5, is_buyable=lambda name: name == "C") == (
            "C",
        )

    def test_stops_probing_once_k_names_are_found(self):
        """可买判定要惰性：全A 四千多只票不能每天全量查交易所。"""
        scores = _scores([f"S{i:03d}" for i in range(500)])
        probed: list[str] = []

        def is_buyable(name: str) -> bool:
            probed.append(name)
            return True

        select_ladder_buys(scores, k=5, is_buyable=is_buyable)

        assert probed == [f"S{i:03d}" for i in range(5)]

    def test_drops_non_finite_scores(self):
        scores = pd.Series({"A": float("nan"), "B": 2.0, "C": 1.0})
        assert select_ladder_buys(scores, k=2) == ("B", "C")

    def test_rejects_non_positive_k(self):
        with pytest.raises(ValueError):
            select_ladder_buys(_scores(["A"]), k=0)


class TestCohortBudget:
    def test_splits_target_exposure_evenly_across_horizon(self):
        assert cohort_budget(
            total_value=1_000_000.0, cash=1_000_000.0, risk_degree=0.9, horizon=5
        ) == pytest.approx(180_000.0)

    def test_caps_at_available_cash(self):
        """到期那层没卖掉时现金不够，买入必须缩到现金以内，不能透支。"""
        assert cohort_budget(
            total_value=1_000_000.0, cash=50_000.0, risk_degree=0.9, horizon=5
        ) == pytest.approx(50_000.0)

    def test_never_negative(self):
        assert cohort_budget(
            total_value=1_000_000.0, cash=-10.0, risk_degree=0.9, horizon=5
        ) == pytest.approx(0.0)


class TestCohortLedger:
    def test_nothing_is_due_before_horizon_is_filled(self):
        ledger = CohortLedger(horizon=3)
        for _ in range(3):
            assert ledger.due() == {}
            ledger.settle({})
            ledger.add({"A": 100.0})
        assert ledger.cohort_count == 3

    def test_oldest_cohort_is_due_on_the_day_after_horizon(self):
        ledger = CohortLedger(horizon=3)
        for name in ["A", "B", "C"]:
            ledger.settle({})
            ledger.add({name: 100.0})

        assert ledger.due() == {"A": 100.0}
        ledger.settle({"A": 100.0})
        ledger.add({"D": 100.0})

        assert ledger.cohort_count == 3
        assert ledger.holdings() == {"B": 100.0, "C": 100.0, "D": 100.0}

    def test_same_name_across_cohorts_aggregates_and_exits_in_tranches(self):
        ledger = CohortLedger(horizon=2)
        ledger.add({"X": 100.0})
        ledger.add({"X": 300.0})

        assert ledger.holdings() == {"X": 400.0}
        assert ledger.due() == {"X": 100.0}

        ledger.settle({"X": 100.0})
        assert ledger.holdings() == {"X": 300.0}

    def test_unsold_due_amount_carries_to_the_next_day(self):
        """到期卖不掉（停牌/跌停）必须挂账重试，不能凭空消失。"""
        ledger = CohortLedger(horizon=1)
        ledger.add({"A": 100.0, "B": 50.0})

        assert ledger.due() == {"A": 100.0, "B": 50.0}
        ledger.settle({"A": 40.0})
        ledger.add({"C": 10.0})

        assert ledger.holdings() == {"A": 60.0, "B": 50.0, "C": 10.0}
        assert ledger.due() == {"A": 60.0, "B": 50.0, "C": 10.0}

    def test_reconcile_trims_ledger_down_to_the_real_position(self):
        """买单可能整单落空，台账要按真实持仓收敛，否则会卖出不存在的股数。"""
        ledger = CohortLedger(horizon=2)
        ledger.add({"A": 100.0})
        ledger.add({"A": 100.0, "B": 200.0})

        ledger.reconcile({"A": 150.0})

        assert ledger.holdings() == {"A": 150.0}
        assert ledger.due() == {"A": 100.0}

    def test_reconcile_trims_newest_cohort_first(self):
        ledger = CohortLedger(horizon=3)
        ledger.add({"A": 100.0})
        ledger.add({"A": 100.0})
        ledger.add({"A": 100.0})

        ledger.reconcile({"A": 120.0})

        assert ledger.due() == {"A": 100.0}
        assert ledger.holdings() == {"A": 120.0}

    def test_reconcile_clears_pending_when_position_is_gone(self):
        ledger = CohortLedger(horizon=1)
        ledger.add({"A": 100.0})
        ledger.settle({})
        assert ledger.due() == {"A": 100.0}

        ledger.reconcile({})

        assert ledger.due() == {}
        assert ledger.holdings() == {}

    def test_rejects_non_positive_horizon(self):
        with pytest.raises(ValueError):
            CohortLedger(horizon=0)

    def test_extract_pulls_a_name_from_every_cohort(self):
        ledger = CohortLedger(horizon=5)
        ledger.add({"A": 100.0, "B": 50.0})
        ledger.add({"A": 80.0})
        pulled = ledger.extract(["A"])

        assert pulled == {"A": 180.0}
        assert ledger.holdings() == {"B": 50.0}
        assert "A" not in ledger.due()


class TestSelectLadderRefills:
    def test_takes_next_names_after_excluded_ones(self):
        scores = _scores(["A", "B", "C", "D", "E"])
        assert select_ladder_refills(scores, n=2, exclude=["A", "B"]) == ("C", "D")

    def test_skips_unbuyable_and_still_held_names(self):
        scores = _scores(["A", "B", "C", "D"])
        picked = select_ladder_refills(
            scores,
            n=1,
            exclude=["A"],
            is_buyable=lambda name: name != "B",
        )
        assert picked == ("C",)

    def test_returns_empty_when_n_is_zero(self):
        assert select_ladder_refills(_scores(["A", "B"]), n=0, exclude=["A"]) == ()

    def test_rejects_negative_n(self):
        with pytest.raises(ValueError):
            select_ladder_refills(_scores(["A"]), n=-1, exclude=[])


class TestForceSellNames:
    def test_flags_holdings_worse_than_the_cutoff(self):
        scores = _scores(["A", "B", "C", "D"])
        assert force_sell_names(scores, ["A", "D"], force_sell_rank=2) == ("D",)

    def test_flags_holdings_missing_from_the_score_panel(self):
        scores = _scores(["A", "B"])
        assert force_sell_names(scores, ["A", "GONE"], force_sell_rank=100) == ("GONE",)

    def test_none_cutoff_sells_nobody(self):
        scores = _scores(["A", "B"])
        assert force_sell_names(scores, ["A", "B"], force_sell_rank=None) == ()

    def test_rejects_non_positive_cutoff(self):
        with pytest.raises(ValueError):
            force_sell_names(_scores(["A"]), ["A"], force_sell_rank=0)


class TestLedgerSellAmounts:
    def test_clamps_to_the_real_position(self):
        assert ledger_sell_amounts({"A": 100.0}, {"A": 60.0}) == {"A": 60.0}

    def test_drops_names_absent_from_the_position(self):
        assert ledger_sell_amounts({"A": 100.0}, {"B": 60.0}) == {}


class TestLedgerState:
    """跨进程持久化用的序列化：发布与回执导入是两个进程。"""

    def test_to_state_preserves_layer_order_and_empty_layers(self):
        ledger = CohortLedger(horizon=3)
        ledger.add({"SH600000": 100.0})
        ledger.add({})  # 全部买单落空的空层，必须占位
        ledger.add({"SZ000001": 200.0, "SH600000": 300.0})

        state = ledger.to_state()

        assert state["horizon"] == 3
        assert state["cohorts"] == [
            {"SH600000": 100.0},
            {},
            {"SZ000001": 200.0, "SH600000": 300.0},
        ]
        assert state["pending"] == {}

    def test_to_state_includes_pending_remnant(self):
        ledger = CohortLedger(horizon=1)
        ledger.add({"SH600000": 500.0})
        ledger.settle({"SH600000": 200.0})  # 到期只卖掉 200，300 转入 pending

        state = ledger.to_state()

        assert state["pending"] == {"SH600000": 300.0}
        assert state["cohorts"] == []

    def test_from_state_round_trips_and_due_is_unchanged(self):
        original = CohortLedger(horizon=3)
        original.add({"SH600000": 100.0})
        original.add({})
        original.add({"SZ000001": 200.0})
        original.settle({})  # 已有 3 层，弹出最老层进 pending
        original.add({"SH600519": 400.0})

        restored = CohortLedger.from_state(original.to_state())

        assert restored.horizon == original.horizon
        assert restored.to_state() == original.to_state()
        assert restored.due() == original.due()
        assert restored.holdings() == original.holdings()

    def test_from_state_rejects_more_layers_than_horizon(self):
        state = {"horizon": 2, "cohorts": [{}, {}, {}], "pending": {}}

        with pytest.raises(ValueError, match="exceeds horizon"):
            CohortLedger.from_state(state)

    def test_from_state_drops_zero_amounts(self):
        state = {
            "horizon": 2,
            "cohorts": [{"SH600000": 0.0, "SZ000001": 100.0}],
            "pending": {"SH600519": 0.0},
        }

        ledger = CohortLedger.from_state(state)

        assert ledger.to_state()["cohorts"] == [{"SZ000001": 100.0}]
        assert ledger.to_state()["pending"] == {}


class TestAbsorbBrokerExcess:
    """`reconcile` 只削台账多出的部分；反向缺口（送股 / 手工买卖）由本方法吸收。

    实盘专用，回测里策略是持仓的唯一作用者，不会发生。
    """

    def test_prorates_into_existing_layers(self):
        ledger = CohortLedger(horizon=3)
        ledger.add({"SH600000": 100.0})
        ledger.add({"SH600000": 300.0})
        # 台账合计 400 股；券商 520 股（3:10 送股得 120 股）

        absorbed = ledger.absorb_broker_excess({"SH600000": 520.0})

        assert absorbed == {"SH600000": 120.0}
        # 120 按 100:300 等比例 → 30 / 90
        assert ledger.to_state()["cohorts"] == [
            {"SH600000": 130.0},
            {"SH600000": 390.0},
        ]
        assert ledger.holdings() == {"SH600000": 520.0}

    def test_remainder_goes_to_the_largest_fractional_share(self):
        ledger = CohortLedger(horizon=3)
        ledger.add({"SH600000": 100.0})
        ledger.add({"SH600000": 200.0})
        # 台账 300 股，券商 310 股，excess=10；等比例 3.33 / 6.67
        # 最大余额法：floor 得 3 / 6，余 1 补给小数部分更大的那层

        absorbed = ledger.absorb_broker_excess({"SH600000": 310.0})

        assert absorbed == {"SH600000": 10.0}
        assert ledger.holdings() == {"SH600000": 310.0}
        assert ledger.to_state()["cohorts"] == [
            {"SH600000": 103.0},
            {"SH600000": 207.0},
        ]

    def test_without_layers_goes_to_pending(self):
        ledger = CohortLedger(horizon=3)
        ledger.add({"SH600000": 100.0})
        # SZ000001 在阶梯里完全没有分层（运维手工买入）

        absorbed = ledger.absorb_broker_excess(
            {"SH600000": 100.0, "SZ000001": 500.0}
        )

        assert absorbed == {"SZ000001": 500.0}
        assert ledger.to_state()["pending"] == {"SZ000001": 500.0}
        # pending 的语义就是脱离账龄、次日全量进 due
        assert ledger.due()["SZ000001"] == 500.0

    def test_is_noop_when_ledger_matches_or_exceeds(self):
        ledger = CohortLedger(horizon=3)
        ledger.add({"SH600000": 100.0})
        ledger.add({"SZ000001": 200.0})
        before = ledger.to_state()

        # 相等 / 券商更少（该 reconcile 管）/ 券商为 0，三种都不该动
        absorbed = ledger.absorb_broker_excess({
            "SH600000": 100.0, "SZ000001": 50.0, "SH600519": 0.0,
        })

        assert absorbed == {}
        assert ledger.to_state() == before

    def test_only_layers_holding_that_name_participate(self):
        ledger = CohortLedger(horizon=3)
        ledger.add({"SH600000": 100.0})
        ledger.add({"SZ000001": 900.0})  # 不持有 SH600000 的层不参与分配

        absorbed = ledger.absorb_broker_excess(
            {"SH600000": 150.0, "SZ000001": 900.0}
        )

        assert absorbed == {"SH600000": 50.0}
        assert ledger.to_state()["cohorts"] == [
            {"SH600000": 150.0},
            {"SZ000001": 900.0},
        ]

    def test_absorbed_total_exactly_matches_the_gap(self):
        """并入总量必须与账实差完全相等，不允许留零头。"""
        ledger = CohortLedger(horizon=4)
        ledger.add({"SH600000": 111.0})
        ledger.add({"SH600000": 222.0})
        ledger.add({"SH600000": 333.0})

        ledger.absorb_broker_excess({"SH600000": 666.0 + 97.0})

        assert ledger.holdings() == {"SH600000": 763.0}
