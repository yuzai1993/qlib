from __future__ import annotations

import argparse
import bisect
import datetime as dt
import os
import sys
import time
from pathlib import Path

import pandas as pd

DAILY_COLUMNS = ("symbol", "date", "name", "source")
INTERVAL_COLUMNS = ("symbol", "start", "end", "name", "source")
NAMECHANGE_COLUMNS = (
    "ts_code",
    "name",
    "start_date",
    "end_date",
    "ann_date",
    "change_reason",
)
NAMECHANGE_FIELDS = ",".join(NAMECHANGE_COLUMNS)
_EXCHANGES = {"SH", "SZ", "BJ"}
_SOURCE_RANK = {"stock_st": 0, "namechange": 1}

STOCK_ST_START = "2017-01-03"
NAMECHANGE_START_YEAR = 1999
HERE = Path(__file__).resolve().parent
DEFAULT_DAILY_PATH = HERE / "st_daily.csv"
DEFAULT_RAW_PATH = HERE / "st_namechange_raw.csv"
DEFAULT_INTERVAL_PATH = HERE / "st_calendar.csv"
DEFAULT_QLIB_DIR = "~/.qlib/qlib_data/cn_data"


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


def _compact_day(value) -> str:
    return _norm_day(value).replace("-", "")


def _empty_daily() -> pd.DataFrame:
    return pd.DataFrame(columns=list(DAILY_COLUMNS))


def expand_namechange(raw, calendar, delist) -> pd.DataFrame:
    cal = [_norm_day(d) for d in calendar]
    if raw is None or raw.empty or not cal:
        return _empty_daily()
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
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "name": name,
                    "source": "namechange",
                }
            )
    return pd.DataFrame(rows, columns=list(DAILY_COLUMNS))


def merge_daily(*frames) -> pd.DataFrame:
    parts = [f for f in frames if f is not None and not f.empty]
    if not parts:
        return _empty_daily()
    both = pd.concat(parts, ignore_index=True)[list(DAILY_COLUMNS)]
    both["_rank"] = both["source"].map(_SOURCE_RANK).fillna(9)
    both = both.sort_values(["symbol", "date", "_rank"], kind="mergesort")
    both = both.drop_duplicates(["symbol", "date"], keep="first")
    return (
        both.drop(columns="_rank")
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


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


def _call_with_retry(func, *args, retry: int = 5, retry_sleep: int = 3, **kwargs):
    last = None
    for attempt in range(1, retry + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last = exc
            if attempt == retry:
                raise
            time.sleep(retry_sleep)
    raise last


def _to_daily_from_stock_st(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return _empty_daily()
    rows = []
    for rec in raw.itertuples():
        try:
            symbol = ts_code_to_qlib(str(rec.ts_code))
        except ValueError:
            continue
        rows.append(
            {
                "symbol": symbol,
                "date": _norm_day(rec.trade_date),
                "name": str(rec.name),
                "source": "stock_st",
            }
        )
    return pd.DataFrame(rows, columns=list(DAILY_COLUMNS))


def fetch_stock_st(pro, trade_date: str) -> pd.DataFrame:
    compact = _compact_day(trade_date)
    raw = _call_with_retry(pro.stock_st, trade_date=compact)
    if raw is None:
        raw = pd.DataFrame()
    if len(raw) >= 1000:
        raise ValueError(
            f"stock_st({compact}) returned {len(raw)} rows; "
            "1000-row page limit, refuse range/truncated query"
        )
    return _to_daily_from_stock_st(raw)


def fetch_namechange(pro, years: range) -> pd.DataFrame:
    parts = []
    for year in years:
        raw = _call_with_retry(
            pro.namechange,
            start_date=f"{year}0101",
            end_date=f"{year}1231",
            fields=NAMECHANGE_FIELDS,
        )
        if raw is None:
            raw = pd.DataFrame()
        if len(raw) >= 10000:
            raise ValueError(
                f"namechange({year}) returned {len(raw)} rows; "
                "10000-row page limit, refuse unsharded dump"
            )
        if not raw.empty:
            parts.append(raw)
    full = _call_with_retry(pro.namechange)
    if full is not None and not full.empty:
        parts.append(full)
    if not parts:
        return pd.DataFrame(columns=list(NAMECHANGE_COLUMNS))
    out = pd.concat(parts, ignore_index=True)
    keep = [c for c in NAMECHANGE_COLUMNS if c in out.columns]
    out = out[keep]
    return out.drop_duplicates(["ts_code", "name", "start_date"]).reset_index(drop=True)


def fetch_delist_dates(pro) -> dict[str, str]:
    raw = _call_with_retry(
        pro.stock_basic,
        exchange="",
        list_status="D",
        fields="ts_code,name,delist_date",
    )
    if raw is None or raw.empty:
        return {}
    out: dict[str, str] = {}
    for rec in raw.itertuples():
        value = getattr(rec, "delist_date", None)
        if pd.isna(value) or value in (None, "", "None"):
            continue
        out[str(rec.ts_code)] = _compact_day(value)
    return out


def load_daily(path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    missing = [c for c in DAILY_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"st_daily missing columns: {missing}")
    return df[list(DAILY_COLUMNS)]


def load_trade_calendar(qlib_dir) -> list[str]:
    root = Path(qlib_dir).expanduser()
    for name in ("day.txt", "day_future.txt"):
        path = root / "calendars" / name
        if not path.is_file():
            continue
        days = []
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            days.append(_norm_day(text[:10]))
        if days:
            return days
    raise FileNotFoundError(f"no trade calendar under {root}/calendars")


def _calendar_slice(calendar: list[str], start: str, end: str) -> list[str]:
    start = _norm_day(start)
    end = _norm_day(end)
    return [d for d in calendar if start <= d <= end]


def _write_outputs(daily: pd.DataFrame, raw: pd.DataFrame, calendar: list[str],
                   daily_path: Path, raw_path: Path, interval_path: Path) -> dict:
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    interval_path.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(daily_path, index=False)
    raw.to_csv(raw_path, index=False)
    compress_intervals(daily, calendar).to_csv(interval_path, index=False)
    n_nc = int((daily["source"] == "namechange").sum()) if not daily.empty else 0
    return {
        "n_rows": int(len(daily)),
        "n_symbols": int(daily["symbol"].nunique()) if not daily.empty else 0,
        "max_date": None if daily.empty else str(daily["date"].max()),
        "n_from_namechange": n_nc,
    }


def update(
    *,
    pro,
    qlib_dir,
    dates=None,
    backfill: bool = False,
    daily_path=None,
    raw_path=None,
    interval_path=None,
) -> dict:
    daily_path = Path(daily_path or DEFAULT_DAILY_PATH)
    raw_path = Path(raw_path or DEFAULT_RAW_PATH)
    interval_path = Path(interval_path or DEFAULT_INTERVAL_PATH)
    calendar = load_trade_calendar(qlib_dir)
    today = pd.Timestamp.today().normalize().strftime("%Y-%m-%d")
    this_year = dt.date.today().year

    if dates is not None:
        st_days = [_norm_day(d) for d in dates]
    elif backfill:
        st_days = _calendar_slice(calendar, STOCK_ST_START, calendar[-1])
    else:
        if not daily_path.is_file():
            raise SystemExit(
                f"{daily_path} missing; run with --backfill first"
            )
        cached = load_daily(daily_path)
        max_date = str(cached["date"].max()) if not cached.empty else None
        if max_date is None:
            raise SystemExit(
                f"{daily_path} is empty; run with --backfill first"
            )
        nxt = next((d for d in calendar if d > max_date), None)
        end = min(today, calendar[-1])
        st_days = _calendar_slice(calendar, nxt, end) if nxt and nxt <= end else []

    st_frames = []
    for i, day in enumerate(st_days, 1):
        if i == 1 or i % 100 == 0 or i == len(st_days):
            print(f"stock_st {i}/{len(st_days)} {day}", flush=True)
        frame = fetch_stock_st(pro, day)
        if frame.empty:
            if backfill:
                continue
            raise ValueError(f"stock_st returned 0 rows for trade_date={day}")
        st_frames.append(frame)

    if backfill:
        nc_years = range(NAMECHANGE_START_YEAR, this_year + 1)
        old_daily = _empty_daily()
    else:
        nc_years = range(this_year - 1, this_year + 1)
        old_daily = load_daily(daily_path) if daily_path.is_file() else _empty_daily()

    raw = fetch_namechange(pro, nc_years)
    delist = fetch_delist_dates(pro)
    nc_daily = expand_namechange(raw, calendar, delist)
    daily = merge_daily(*st_frames, nc_daily, old_daily)
    return _write_outputs(daily, raw, calendar, daily_path, raw_path, interval_path)


def _make_pro():
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise SystemExit("TUSHARE_TOKEN is not set")
    import tushare as ts  # pylint: disable=import-outside-toplevel

    return ts.pro_api(token)


def _audit(daily_path: Path, as_of: str | None = None) -> None:
    if not daily_path.is_file():
        raise SystemExit(f"ST daily index missing: {daily_path}")
    daily = load_daily(daily_path)
    if daily.empty:
        print("st_daily is empty")
        return
    as_of = _norm_day(as_of or daily["date"].max())
    hit = st_symbols_on(daily, as_of)
    by_src = daily["source"].value_counts().to_dict()
    print(f"as_of={as_of} n_symbols={len(hit)} sources={by_src}")
    after = daily[daily["date"] >= STOCK_ST_START]
    if after.empty:
        print("no rows on/after 2017-01-03")
        return
    n_st = int((after["source"] == "stock_st").sum())
    n_nc = int((after["source"] == "namechange").sum())
    total = n_st + n_nc
    agree = (n_st / total) if total else 0.0
    print(
        f"post-2017 rows: stock_st={n_st} namechange={n_nc} "
        f"stock_st_share={agree:.4f} (quality signal, not a gate)"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build/update the daily ST index")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_up = sub.add_parser("update", help="fetch and rewrite st_daily.csv")
    p_up.add_argument("--backfill", action="store_true")
    p_up.add_argument("--dates", nargs="+", default=None)
    p_up.add_argument("--qlib-dir", default=DEFAULT_QLIB_DIR)
    p_up.add_argument("--daily-path", default=str(DEFAULT_DAILY_PATH))
    p_up.add_argument("--raw-path", default=str(DEFAULT_RAW_PATH))
    p_up.add_argument("--interval-path", default=str(DEFAULT_INTERVAL_PATH))

    p_au = sub.add_parser("audit", help="print as_of coverage and source mix")
    p_au.add_argument("--date", default=None)
    p_au.add_argument("--daily-path", default=str(DEFAULT_DAILY_PATH))

    args = parser.parse_args(argv)
    if args.cmd == "audit":
        _audit(Path(args.daily_path), args.date)
        return
    stats = update(
        pro=_make_pro(),
        qlib_dir=args.qlib_dir,
        dates=args.dates,
        backfill=args.backfill,
        daily_path=args.daily_path,
        raw_path=args.raw_path,
        interval_path=args.interval_path,
    )
    print(stats)


if __name__ == "__main__":
    main(sys.argv[1:])
