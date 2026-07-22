import pandas as pd
import pytest

from qlib.contrib.strategy.topk_dropout import (
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
