"""Pure, deterministic TopkDropout instrument selection."""

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TopkSelection:
    sell: tuple[str, ...]
    buy: tuple[str, ...]


def calculate_topk_buy_value(
    *,
    cash: float,
    total_value: float,
    buy_count: int,
    risk_degree: float,
    topk: int,
    staged: bool,
) -> float:
    """Return one buy order's gross value under legacy or staged sizing."""
    if buy_count <= 0:
        return 0.0
    if staged:
        if topk <= 0:
            return 0.0
        return total_value * risk_degree / topk
    return cash * risk_degree / buy_count


def stable_rank_scores(scores: pd.Series) -> pd.Series:
    """Rank valid scores by value descending and instrument ascending."""
    if not isinstance(scores, pd.Series):
        raise TypeError("scores must be a pandas Series")
    if scores.index.has_duplicates:
        raise ValueError("scores contain duplicate instruments")
    clean = scores[np.isfinite(scores)]
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
    initial_buy_count: int | None = None,
) -> TopkSelection:
    """Return deterministic sell/buy symbols for top/bottom TopkDropout."""
    if topk < 0 or n_drop < 0:
        raise ValueError("topk and n_drop must be non-negative")
    if initial_buy_count is not None and (
        isinstance(initial_buy_count, bool)
        or not isinstance(initial_buy_count, int)
        or initial_buy_count <= 0
    ):
        raise ValueError("initial_buy_count must be a positive integer or None")

    ranked_scores = stable_rank_scores(scores)
    if ranked_scores.empty:
        return TopkSelection(sell=(), buy=())

    current = list(current_stock_list)
    last = _rank_instruments(current, ranked_scores)
    held = set(last)
    position_delta = topk - len(last)

    if initial_buy_count is not None and position_delta > 0:
        buy = tuple(
            instrument for instrument in ranked_scores.index
            if instrument not in held
        )[:min(initial_buy_count, position_delta)]
        return TopkSelection(sell=(), buy=buy)

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
