import pandas as pd
import pytest

from qlib.contrib.strategy.topk_dropout import (
    calculate_topk_buy_value,
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
