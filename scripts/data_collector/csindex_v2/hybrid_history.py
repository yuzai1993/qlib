"""Training-only hybrid CSI500/CSI1000 history construction.

The pre-2015 history is approximate.  The official suffix starting on
2015-11-30 is supplied by the existing ``csindex_v2`` instruments builder.
"""

from __future__ import annotations

import bisect

import pandas as pd


CUTOVER = "2015-11-30"
PREFIX_END = "2015-11-27"


def ts_code_to_symbol(ts_code: str) -> str | None:
    """Convert a Tushare stock code to Qlib's exchange-prefixed symbol."""
    code, dot, exchange = str(ts_code).upper().partition(".")
    if dot != "." or len(code) != 6 or not code.isdigit():
        return None
    prefix = {"SH": "SH", "SZ": "SZ", "BJ": "BJ"}.get(exchange)
    return f"{prefix}{code}" if prefix else None


def select_csi1000_proxy(
    total_mv: pd.DataFrame,
    excluded: set[str],
    limit: int = 1000,
) -> set[str]:
    """Select the largest eligible stocks after excluding CSI300/CSI500."""
    frame = total_mv[["ts_code", "total_mv"]].copy()
    frame["symbol"] = frame["ts_code"].map(ts_code_to_symbol)
    frame["total_mv"] = pd.to_numeric(frame["total_mv"], errors="coerce")
    frame = frame[
        frame["symbol"].notna()
        & frame["total_mv"].gt(0)
        & ~frame["symbol"].isin(excluded)
    ]
    frame = frame.sort_values(
        ["total_mv", "symbol"],
        ascending=[False, True],
        kind="mergesort",
    )
    return set(frame.head(limit)["symbol"])


def _previous_trading_day(date: str, calendar: list[str]) -> str:
    position = bisect.bisect_left(calendar, date)
    if position == 0:
        raise ValueError(f"{date} 前没有可用交易日")
    return calendar[position - 1]


def rosters_to_closed_intervals(
    rosters: list[tuple[str, set[str]]],
    calendar: list[str],
    final_end: str,
    source: str,
) -> pd.DataFrame:
    """Convert dated rosters to inclusive, non-overlapping Qlib intervals."""
    ordered = sorted((date, set(members)) for date, members in rosters)
    open_since: dict[str, str] = {}
    previous_members: set[str] = set()
    rows: list[dict[str, str]] = []

    for date, members in ordered:
        for symbol in sorted(previous_members - members):
            rows.append(
                {
                    "symbol": symbol,
                    "start": open_since.pop(symbol),
                    "end": _previous_trading_day(date, calendar),
                    "source": source,
                }
            )
        for symbol in sorted(members - previous_members):
            open_since[symbol] = date
        previous_members = members

    for symbol, start in sorted(open_since.items()):
        rows.append(
            {
                "symbol": symbol,
                "start": start,
                "end": final_end,
                "source": source,
            }
        )

    columns = ["symbol", "start", "end", "source"]
    return (
        pd.DataFrame(rows, columns=columns)
        .sort_values(["symbol", "start"])
        .reset_index(drop=True)
    )


def active_members(intervals: pd.DataFrame, date: str) -> set[str]:
    """Return members active on ``date`` using inclusive interval semantics."""
    active = intervals[
        (intervals["start"] <= date) & (intervals["end"] >= date)
    ]
    return set(active["symbol"])
