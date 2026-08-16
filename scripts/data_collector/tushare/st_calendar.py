from __future__ import annotations

import bisect

import pandas as pd

DAILY_COLUMNS = ("symbol", "date", "name", "source")
INTERVAL_COLUMNS = ("symbol", "start", "end", "name", "source")
_EXCHANGES = {"SH", "SZ", "BJ"}
_SOURCE_RANK = {"stock_st": 0, "namechange": 1}


def ts_code_to_qlib(ts_code: str) -> str:
    code, _, exch = ts_code.strip().upper().partition(".")
    if exch not in _EXCHANGES or len(code) != 6 or not code.isdigit():
        raise ValueError(f"unsupported ts_code: {ts_code!r}")
    return f"{exch}{code}"


def is_st_name(name: str) -> bool:
    text = str(name).strip().upper()
    return "ST" in text or "退" in text


def _norm_day(value) -> str:
    return pd.Timestamp(str(value)).strftime("%Y-%m-%d")


def expand_namechange(raw, calendar, delist) -> pd.DataFrame:
    cal = [_norm_day(d) for d in calendar]
    if raw is None or raw.empty or not cal:
        return pd.DataFrame(columns=list(DAILY_COLUMNS))
    rows = []
    for rec in raw.itertuples():
        name = str(rec.name)
        if not is_st_name(name):
            continue
        start = max(_norm_day(rec.start_date), cal[0])
        raw_end = getattr(rec, "end_date", None)
        if pd.isna(raw_end) or raw_end in (None, "", "None"):
            fallback = delist.get(str(rec.ts_code))
            end = _norm_day(fallback) if fallback else cal[-1]
        else:
            end = _norm_day(raw_end)
        end = min(end, cal[-1])
        if start > end:
            continue
        lo = bisect.bisect_left(cal, start)
        hi = bisect.bisect_right(cal, end)
        symbol = ts_code_to_qlib(str(rec.ts_code))
        for day in cal[lo:hi]:
            rows.append({"symbol": symbol, "date": day,
                         "name": name, "source": "namechange"})
    return pd.DataFrame(rows, columns=list(DAILY_COLUMNS))


def merge_daily(*frames) -> pd.DataFrame:
    parts = [f for f in frames if f is not None and not f.empty]
    if not parts:
        return pd.DataFrame(columns=list(DAILY_COLUMNS))
    both = pd.concat(parts, ignore_index=True)[list(DAILY_COLUMNS)]
    both["_rank"] = both["source"].map(_SOURCE_RANK).fillna(9)
    both = both.sort_values(["symbol", "date", "_rank"])
    both = both.drop_duplicates(["symbol", "date"], keep="first")
    return both.drop(columns="_rank").sort_values(["date", "symbol"]).reset_index(drop=True)


def st_symbols_on(daily, as_of) -> set[str]:
    if daily is None or daily.empty:
        return set()
    return set(daily.loc[daily["date"] == _norm_day(as_of), "symbol"].astype(str))


def compress_intervals(daily, calendar) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame(columns=list(INTERVAL_COLUMNS))
    cal = [_norm_day(d) for d in calendar]
    cal_pos = {day: i for i, day in enumerate(cal)}
    rows = []
    ordered = daily.copy()
    ordered["date"] = ordered["date"].map(_norm_day)
    for symbol, group in ordered.groupby("symbol", sort=True):
        group = group.sort_values("date")
        start = end = last_name = last_source = None
        last_pos = None
        for rec in group.itertuples():
            day = rec.date
            pos = cal_pos[day]
            if last_pos is None or pos != last_pos + 1:
                if start is not None:
                    rows.append(
                        {
                            "symbol": symbol,
                            "start": start,
                            "end": end,
                            "name": last_name,
                            "source": last_source,
                        }
                    )
                start = day
            end = day
            last_name = rec.name
            last_source = rec.source
            last_pos = pos
        if start is not None:
            rows.append(
                {
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                    "name": last_name,
                    "source": last_source,
                }
            )
    return pd.DataFrame(rows, columns=list(INTERVAL_COLUMNS))
