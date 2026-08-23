import numpy as np
import pandas as pd
import pytest

from qlib.contrib.strategy.topk_dropout import (
    calculate_topk_buy_value,
    cap_buy_to_free_slots,
    select_daily_topk,
    select_topk_dropout,
    stable_rank_scores,
)


def _scores(count=14):
    instruments = [f"SH600{i:03d}" for i in range(count)]
    return pd.Series(range(count, 0, -1), index=instruments, dtype=float)


def test_stable_rank_scores_breaks_ties_by_instrument():
    first = pd.Series(
        [1.0, 1.0, 1.0],
        index=["SZ000002", "SH600001", "SH600000"],
    )
    second = first.iloc[::-1]

    expected = ["SH600000", "SH600001", "SZ000002"]
    assert stable_rank_scores(first).index.tolist() == expected
    assert stable_rank_scores(second).index.tolist() == expected


def test_tied_boundary_selection_ignores_signal_and_position_order():
    scores = pd.Series(
        [1.0, 1.0, 1.0],
        index=["SZ000002", "SH600001", "SH600000"],
    )

    first = select_topk_dropout(
        scores, ["SZ000002", "SH600001"], topk=2, n_drop=1,
    )
    second = select_topk_dropout(
        scores.iloc[::-1], ["SH600001", "SZ000002"], topk=2, n_drop=1,
    )

    assert first == second
    assert first.sell == ("SZ000002",)
    assert first.buy == ("SH600000",)


@pytest.mark.parametrize(
    "held,sell,buy",
    [
        (
            lambda s: list(s.index[:8]) + list(s.index[10:12]),
            lambda s: tuple(s.index[10:12]),
            lambda s: tuple(s.index[8:10]),
        ),
        (
            lambda s: list(s.index[:7]) + list(s.index[10:12]),
            lambda s: tuple(s.index[10:12]),
            lambda s: tuple(s.index[7:10]),
        ),
        (
            lambda s: list(s.index[:9]) + list(s.index[10:12]),
            lambda s: tuple(s.index[10:12]),
            lambda s: (s.index[9],),
        ),
        (
            lambda s: list(s.index[:8]) + list(s.index[10:14]),
            lambda s: tuple(s.index[12:14]),
            lambda s: (),
        ),
    ],
    ids=["ten", "nine", "eleven", "twelve"],
)
def test_selection_converges_to_topk(held, sell, buy):
    scores = _scores()

    selection = select_topk_dropout(scores, held(scores), topk=10, n_drop=2)

    assert set(selection.sell) == set(sell(scores))
    assert selection.buy == buy(scores)


def test_empty_effective_scores_fail_closed():
    selection = select_topk_dropout(
        pd.Series({"SH600000": float("nan")}),
        ["SH600000"],
        topk=10,
        n_drop=2,
    )

    assert selection.sell == ()
    assert selection.buy == ()


def test_stable_rank_excludes_all_non_finite_scores():
    scores = pd.Series(
        [np.inf, 2.0, -np.inf, np.nan],
        index=["SH600000", "SH600001", "SH600002", "SH600003"],
    )

    assert stable_rank_scores(scores).index.tolist() == ["SH600001"]


def test_duplicate_instruments_are_rejected():
    scores = pd.Series([1.0, 2.0], index=["SH600000", "SH600000"])

    with pytest.raises(ValueError, match="duplicate"):
        stable_rank_scores(scores)


def test_staged_initialization_buys_two_unheld_and_never_sells():
    scores = _scores(40)

    selection = select_topk_dropout(
        scores,
        [scores.index[0], scores.index[5]],
        topk=30,
        n_drop=2,
        initial_buy_count=2,
    )

    assert selection.sell == ()
    assert selection.buy == (scores.index[1], scores.index[2])


def test_staged_initialization_only_fills_remaining_slot():
    scores = _scores(40)

    selection = select_topk_dropout(
        scores,
        list(scores.index[:29]),
        topk=30,
        n_drop=2,
        initial_buy_count=2,
    )

    assert selection.sell == ()
    assert selection.buy == (scores.index[29],)


def test_staged_initialization_starts_dropout_only_when_already_full():
    scores = _scores(40)
    held = list(scores.index[:28]) + list(scores.index[32:34])

    selection = select_topk_dropout(
        scores,
        held,
        topk=30,
        n_drop=2,
        initial_buy_count=2,
    )

    assert set(selection.sell) == set(scores.index[32:34])
    assert selection.buy == tuple(scores.index[28:30])


def test_default_initialization_keeps_legacy_full_fill():
    scores = _scores(40)

    selection = select_topk_dropout(scores, [], topk=30, n_drop=2)

    assert selection.sell == ()
    assert selection.buy == tuple(scores.index[:30])


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_staged_initialization_requires_positive_integer(value):
    with pytest.raises((TypeError, ValueError), match="initial_buy_count"):
        select_topk_dropout(
            _scores(40),
            [],
            topk=30,
            n_drop=2,
            initial_buy_count=value,
        )


def test_staged_buy_value_uses_total_portfolio_slot():
    value = calculate_topk_buy_value(
        cash=400_000.0,
        total_value=500_000.0,
        buy_count=2,
        risk_degree=0.95,
        topk=30,
        staged=True,
    )

    assert value == pytest.approx(15_833.333333333334)


def test_legacy_buy_value_still_splits_available_cash():
    value = calculate_topk_buy_value(
        cash=10_000.0,
        total_value=20_000.0,
        buy_count=2,
        risk_degree=0.95,
        topk=10,
        staged=False,
    )

    assert value == pytest.approx(4_750.0)


def test_daily_topk_replaces_any_name_outside_today_topk():
    scores = _scores(8)
    # 持有 top3 + 一只掉出 top4 的 6；卖掉 6 后缺 1 个名额，买入第 4 名
    held = list(scores.index[:3]) + [scores.index[6]]
    selection = select_daily_topk(scores, held, topk=4)
    assert selection.sell == (scores.index[6],)
    assert selection.buy == (scores.index[3],)


def test_daily_topk_trims_extra_holding_already_in_topk():
    scores = _scores(8)
    held = list(scores.index[:4]) + [scores.index[6]]
    selection = select_daily_topk(scores, held, topk=4)
    assert selection.sell == (scores.index[6],)
    assert selection.buy == ()


def test_daily_topk_keeps_unchanged_when_already_topk():
    scores = _scores(8)
    held = list(scores.index[:4])
    selection = select_daily_topk(scores, held, topk=4)
    assert selection.sell == ()
    assert selection.buy == ()


def test_daily_topk_is_not_capped_by_n_drop():
    """对照：n_drop=1 的 Dropout 一天只能换 1 只；daily topk 一次换完。"""
    scores = _scores(8)
    held = list(scores.index[4:8])  # 最差 4 只
    daily = select_daily_topk(scores, held, topk=4)
    dropout = select_topk_dropout(scores, held, topk=4, n_drop=1)
    assert set(daily.sell) == set(held)
    assert daily.buy == tuple(scores.index[:4])
    assert len(dropout.sell) == 1
    assert len(dropout.buy) == 1


# --- 停牌/不可成交持仓：防止组合自锁死循环 ---


def test_unsellable_holding_does_not_consume_drop_slot():
    """最差持仓当日不可卖时，n_drop 名额应让给次差的可卖持仓，而不是空转。"""
    scores = _scores(8)
    held = list(scores.index[:3]) + [scores.index[6], scores.index[7]]
    sellable = [*scores.index[:3], scores.index[6]]

    stuck = select_topk_dropout(scores, held, topk=5, n_drop=1)
    fixed = select_topk_dropout(scores, held, topk=5, n_drop=1, sellable=sellable)

    # 旧行为把卖单浪费在卖不掉的 index[7] 上
    assert stuck.sell == (scores.index[7],)
    assert fixed.sell == (scores.index[6],)
    assert fixed.buy == (scores.index[3],)


def test_unsellable_holding_still_occupies_topk_slot():
    """不可卖的持仓仍占一个仓位，不能因此多买一只导致持仓超 topk。"""
    scores = _scores(8)
    held = list(scores.index[:3]) + [scores.index[6], scores.index[7]]
    sellable = [*scores.index[:3], scores.index[6]]

    sel = select_topk_dropout(scores, held, topk=5, n_drop=1, sellable=sellable)

    assert len(held) - len(sel.sell) + len(sel.buy) == 5


def test_all_holdings_unsellable_yields_no_orders():
    scores = _scores(8)
    held = list(scores.index[:5])

    sel = select_topk_dropout(scores, held, topk=5, n_drop=1, sellable=[])

    assert sel.sell == ()
    assert sel.buy == ()


def test_over_topk_holding_shrinks_when_worst_is_unsellable():
    """回归 2026 死锁：持仓 6 只且最差不可卖时，必须仍能卖出可成交的最差持仓。"""
    scores = _scores(8)
    held = list(scores.index[:4]) + [scores.index[6], scores.index[7]]
    sellable = [*scores.index[:4], scores.index[6]]

    sel = select_topk_dropout(scores, held, topk=5, n_drop=1, sellable=sellable)

    assert sel.sell == (scores.index[6],)
    assert sel.buy == ()
    assert len(held) - len(sel.sell) == 5


def test_force_sell_rank_dumps_name_outside_threshold_without_using_n_drop():
    """掉出前 100 必卖；未满持仓天数的前 100 名仍不能卖，n_drop 留给可卖持仓。"""
    scores = _scores(120)
    outside = scores.index[110]
    mid = scores.index[50]
    held = list(scores.index[:3]) + [mid, outside]
    sellable = list(scores.index[:3]) + [mid]

    without = select_topk_dropout(
        scores, held, topk=5, n_drop=1, sellable=sellable,
    )
    forced = select_topk_dropout(
        scores, held, topk=5, n_drop=1, sellable=sellable, force_sell_rank=100,
    )

    assert without.sell == (mid,)
    assert outside not in without.sell
    assert set(forced.sell) == {outside, mid}
    assert forced.buy == (scores.index[3], scores.index[4])
    assert len(held) - len(forced.sell) + len(forced.buy) == 5


def test_force_sell_missing_score_is_treated_as_outside_rank():
    scores = _scores(10)
    ghost = "SZ399999"
    held = list(scores.index[:4]) + [ghost]
    sel = select_topk_dropout(
        scores, held, topk=5, n_drop=1, force_sell_rank=100,
    )
    assert ghost in sel.sell


def test_force_sell_rank_none_keeps_legacy_dropout():
    scores = _scores(120)
    outside = scores.index[110]
    held = list(scores.index[:4]) + [outside]
    legacy = select_topk_dropout(scores, held, topk=5, n_drop=1)
    assert legacy.sell == (outside,)
    assert legacy.buy == (scores.index[4],)


def test_sellable_defaults_to_all_holdings():
    scores = _scores(8)
    held = list(scores.index[:3]) + [scores.index[6], scores.index[7]]

    assert select_topk_dropout(scores, held, topk=5, n_drop=1) == select_topk_dropout(
        scores, held, topk=5, n_drop=1, sellable=held
    )


def test_daily_topk_keeps_unsellable_holding_and_caps_buys():
    scores = _scores(8)
    held = list(scores.index[:2]) + [scores.index[6], scores.index[7]]
    sellable = [*scores.index[:2], scores.index[6]]

    sel = select_daily_topk(scores, held, topk=4, sellable=sellable)

    assert sel.sell == (scores.index[6],)
    assert sel.buy == (scores.index[2],)
    assert len(held) - len(sel.sell) + len(sel.buy) == 4


@pytest.mark.parametrize(
    "buy,held_count,expected",
    [
        (("A", "B"), 4, ("A",)),
        (("A",), 5, ()),
        (("A", "B"), 3, ("A", "B")),
        ((), 3, ()),
        (("A",), 6, ()),
    ],
)
def test_cap_buy_to_free_slots(buy, held_count, expected):
    assert cap_buy_to_free_slots(buy, held_count=held_count, topk=5) == expected
