"""检查 csindex_v2 指数成分在 qlib features 中的覆盖情况。

输出:
  - 完全缺失 features 目录的股票
  - 已有 features 但 close 末交易日早于全局日历末交易日的股票（疑似漏跑）
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pandas as pd

QLIB_DIR = Path("~/.qlib/qlib_data/cn_data").expanduser().resolve()
INST_DIR = QLIB_DIR / "instruments"
FEAT_DIR = QLIB_DIR / "features"
CAL_PATH = QLIB_DIR / "calendars" / "day.txt"
INDICES = ("csi300", "csi500", "csi1000", "csi2000")


def load_calendar() -> list[str]:
    return [l.strip() for l in CAL_PATH.open() if l.strip()]


def load_index_symbols() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for idx in INDICES:
        df = pd.read_csv(
            INST_DIR / f"{idx}.txt",
            sep="\t",
            header=None,
            names=["symbol", "start", "end"],
            dtype=str,
        )
        out[idx] = set(df["symbol"])
    return out


def last_valid_close_date(symbol: str, calendar: list[str]) -> str | None:
    """读 close.day.bin，返回最后一个非 NaN 收盘价对应的交易日。"""
    path = FEAT_DIR / symbol.lower() / "close.day.bin"
    if not path.exists():
        return None
    raw = np.fromfile(path, dtype="<f")
    if len(raw) < 2:
        return None
    start_idx = int(raw[0])
    values = raw[1:]
    # 找最后一个非 nan
    valid = np.where(~np.isnan(values))[0]
    if len(valid) == 0:
        return None
    cal_i = start_idx + int(valid[-1])
    if cal_i < 0 or cal_i >= len(calendar):
        return None
    return calendar[cal_i]


def main() -> None:
    calendar = load_calendar()
    last_cal = calendar[-1]
    by_index = load_index_symbols()
    all_syms = set().union(*by_index.values())

    feat_dirs = {p.name for p in FEAT_DIR.iterdir() if p.is_dir()}
    missing_dirs: list[str] = []
    stale: list[tuple[str, str]] = []  # (symbol, last_date)

    for sym in sorted(all_syms):
        if sym.lower() not in feat_dirs:
            missing_dirs.append(sym)
            continue
        last = last_valid_close_date(sym, calendar)
        if last is None:
            missing_dirs.append(sym)
        elif last < last_cal:
            stale.append((sym, last))

    print(f"calendar: {calendar[0]} ~ {last_cal} ({len(calendar)} days)")
    print(f"index union symbols: {len(all_syms)}")
    for idx, syms in by_index.items():
        miss = [s for s in syms if s in missing_dirs or s in {x[0] for x in stale}]
        print(f"  {idx}: {len(syms)} unique, incomplete={len(miss)}")

    print(f"\nmissing feature dirs: {len(missing_dirs)}")
    print(
        "  by prefix:",
        {p: sum(1 for s in missing_dirs if s.startswith(p)) for p in ("SH", "SZ", "BJ")},
    )
    print("  sample:", missing_dirs[:20])

    print(f"\nstale (last close < {last_cal}): {len(stale)}")
    if stale:
        # 按末日期聚合
        by_end: dict[str, int] = {}
        for _, d in stale:
            by_end[d] = by_end.get(d, 0) + 1
        top = sorted(by_end.items(), key=lambda x: -x[1])[:15]
        print("  top last_dates:", top)
        print("  sample:", stale[:15])

    out = QLIB_DIR / "instruments" / "_coverage_gaps.csv"
    rows = [{"symbol": s, "issue": "missing_dir", "last_date": ""} for s in missing_dirs]
    rows += [{"symbol": s, "issue": "stale", "last_date": d} for s, d in stale]
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"\nwrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
