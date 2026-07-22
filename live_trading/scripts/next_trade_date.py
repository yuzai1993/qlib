#!/usr/bin/env python3
"""Resolve the first open A-share trading day after a given date."""

import argparse
import os
from datetime import date, datetime, timedelta


def next_open_date(after_date: str, pro=None) -> str:
    """Return YYYY-MM-DD for the first Tushare open day after ``after_date``."""
    try:
        after = datetime.strptime(after_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid after_date: {after_date!r}") from exc

    if pro is None:
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError("TUSHARE_TOKEN unset; cannot resolve next trading day")
        import tushare as ts

        pro = ts.pro_api(token)

    start = after + timedelta(days=1)
    end = after + timedelta(days=14)
    try:
        frame = pro.trade_cal(
            exchange="",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            is_open="1",
        )
    except Exception as exc:
        raise RuntimeError(f"trade calendar query failed: {exc}") from exc

    open_dates = []
    for row in frame.to_dict("records") if frame is not None else []:
        if str(row.get("is_open")) != "1":
            continue
        raw = str(row.get("cal_date", ""))
        try:
            candidate = datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            continue
        if candidate > after:
            open_dates.append(candidate)
    if not open_dates:
        raise RuntimeError(
            f"no open trading day found in {start}..{end}; refuse to publish"
        )
    return min(open_dates).strftime("%Y-%m-%d")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--after", default=date.today().strftime("%Y-%m-%d"),
        help="find the first open day after YYYY-MM-DD (default: today)",
    )
    args = parser.parse_args()
    print(next_open_date(args.after))


if __name__ == "__main__":
    main()
