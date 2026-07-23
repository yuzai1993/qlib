"""Pure, deterministic TopkDropout instrument selection."""

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class TopkSelection:
    sell: tuple[str, ...]
    buy: tuple[str, ...]


def stable_rank_scores(scores: pd.Series) -> pd.Series:
    """Rank valid scores by value descending and instrument ascending."""
    if not isinstance(scores, pd.Series):
        raise TypeError("scores must be a pandas Series")
    if scores.index.has_duplicates:
        raise ValueError("scores contain duplicate instruments")
    clean = scores.dropna()
    if clean.empty:
        return clean
    frame = clean.rename("score").to_frame()
    frame["_instrument_key"] = [str(value) for value in frame.index]
    frame = frame.sort_values(
        ["score", "_instrument_key"],
        ascending=[False, True],
        kind="mergesort",
    )
    return frame["score"]


def _rank_instruments(
    instruments: Iterable[str], ranked_scores: pd.Series,
) -> list[str]:
    instruments = list(instruments)
    if len(instruments) != len(set(instruments)):
        raise ValueError("current positions contain duplicate instruments")
    frame = ranked_scores.reindex(instruments).rename("score").to_frame()
    frame["_instrument_key"] = [str(value) for value in frame.index]
    frame = frame.sort_values(
        ["score", "_instrument_key"],
        ascending=[False, True],
        kind="mergesort",
        na_position="last",
    )
    return frame.index.tolist()


def select_topk_dropout(
    scores: pd.Series,
    current_stock_list: Iterable[str],
    *,
    topk: int,
    n_drop: int,
) -> TopkSelection:
    """Return deterministic sell/buy symbols for top/bottom TopkDropout."""
    if topk < 0 or n_drop < 0:
        raise ValueError("topk and n_drop must be non-negative")

    ranked_scores = stable_rank_scores(scores)
    if ranked_scores.empty:
        return TopkSelection(sell=(), buy=())

    current = list(current_stock_list)
    last = _rank_instruments(current, ranked_scores)
    held = set(last)
    position_delta = topk - len(last)

    today_count = max(n_drop + position_delta, 0)
    today = [
        instrument for instrument in ranked_scores.index
        if instrument not in held
    ][:today_count]

    combined = _rank_instruments([*last, *today], ranked_scores)
    bottom = set(combined[-n_drop:]) if n_drop > 0 else set()
    sell = tuple(instrument for instrument in last if instrument in bottom)

    buy_count = max(len(sell) + position_delta, 0)
    buy = tuple(today[:buy_count])
    return TopkSelection(sell=sell, buy=buy)
