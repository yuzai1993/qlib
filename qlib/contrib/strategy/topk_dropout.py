"""Pure, deterministic TopkDropout instrument selection."""

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

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


def cap_buy_to_free_slots(
    buy: Sequence[str], *, held_count: int, topk: int,
) -> tuple[str, ...]:
    """Truncate a buy list to the slots still free after sells actually executed.

    Planned sells can fail at execution time (suspended name, hold_thresh), so the
    buy leg must be re-derived from the realized position count; otherwise holdings
    grow past ``topk`` and the next day's ``topk - len(held)`` goes negative, which
    zeroes out the buy budget and freezes the portfolio for good.
    """
    return tuple(buy)[: max(topk - held_count, 0)]


def select_topk_dropout(
    scores: pd.Series,
    current_stock_list: Iterable[str],
    *,
    topk: int,
    n_drop: int,
    initial_buy_count: int | None = None,
    sellable: Optional[Iterable[str]] = None,
    force_sell_rank: int | None = None,
) -> TopkSelection:
    """Return deterministic sell/buy symbols for top/bottom TopkDropout.

    ``sellable`` lists the holdings that can actually be sold today. Names outside
    it (suspended, or under ``hold_thresh``) still occupy a ``topk`` slot but are
    excluded from the drop candidates, so a stuck name cannot burn the daily
    ``n_drop`` budget. ``None`` means every holding is sellable.

    ``force_sell_rank`` (1-based): holdings ranked worse than this, or missing a
    finite score, are always sold and do **not** consume ``n_drop``.
    """
    if topk < 0 or n_drop < 0:
        raise ValueError("topk and n_drop must be non-negative")
    if force_sell_rank is not None and force_sell_rank < 1:
        raise ValueError("force_sell_rank must be a positive integer or None")
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

    force: list[str] = []
    if force_sell_rank is not None:
        rank_pos = {inst: i + 1 for i, inst in enumerate(ranked_scores.index)}
        force = [
            inst
            for inst in last
            if rank_pos.get(inst, force_sell_rank + 1) > force_sell_rank
        ]
    force_set = set(force)

    today_count = max(n_drop + position_delta + len(force), 0)
    today = [
        instrument for instrument in ranked_scores.index
        if instrument not in held
    ][:today_count]

    droppable = last if sellable is None else [i for i in last if i in set(sellable)]
    droppable = [i for i in droppable if i not in force_set]
    combined = _rank_instruments([*droppable, *today], ranked_scores)
    bottom = set(combined[-n_drop:]) if n_drop > 0 else set()
    regular = [instrument for instrument in droppable if instrument in bottom]
    sell = tuple([*force, *regular])

    buy_count = max(len(sell) + position_delta, 0)
    buy = tuple(today[:buy_count])
    return TopkSelection(sell=sell, buy=buy)


def select_daily_topk(
    scores: pd.Series,
    current_stock_list: Iterable[str],
    *,
    topk: int,
    sellable: Optional[Iterable[str]] = None,
) -> TopkSelection:
    """每日把持仓换成当日分数最高的 topk（无 n_drop / hold 缓冲）。

    与 TopkDropout 不同：只要某只股票跌出当日 topk 就卖、新进 topk 就买，
    不限制每日替换只数。`sellable` 语义同 `select_topk_dropout`。
    """
    if topk < 0:
        raise ValueError("topk must be non-negative")
    ranked_scores = stable_rank_scores(scores)
    target = list(ranked_scores.index[:topk])
    target_set = set(target)
    held = list(current_stock_list)
    sellable_set = set(held) if sellable is None else set(sellable)
    sell = tuple(
        instrument for instrument in held
        if instrument not in target_set and instrument in sellable_set
    )
    held_set = set(held)
    buy = tuple(instrument for instrument in target if instrument not in held_set)
    return TopkSelection(
        sell=sell,
        buy=cap_buy_to_free_slots(buy, held_count=len(held) - len(sell), topk=topk),
    )
