"""Compute 60-day rolling size / low-vol style premiums on CSI1000.

Streams year-by-year to stay within 16GB host memory.

Definitions (long-short quintile, equal-weight, next-day return):
- Size proxy: Log(Mean($volume*$vwap, 20)+1). Local cn_data has no market-cap
  field; same liquidity-size proxy as analyze_score_style_exposure.py.
- Low-vol: trailing 60-day Std of daily returns.
- Daily premium = mean(Q1 fwd ret) - mean(Q5 fwd ret)
  Size: Q1=small (low dollar-vol), Q5=large
  Low-vol: Q1=low-vol, Q5=high-vol
- 60-day rolling premium = sum of last 60 daily premiums (≈3-month cumulative
  style return), reported in percent.

Period: 2016-01-02 ~ 2026-07-16 (train+valid+test window used by B5).
"""

from __future__ import annotations

import gc
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))

OUT = Path("/tmp/style_premiums_csi1000.json")
PLOT_START = "2016-01-02"
END = "2026-07-16"


def daily_ls(day: pd.DataFrame, factor: str) -> float:
    g = day.dropna(subset=[factor, "fwd_ret"])
    if len(g) < 50:
        return np.nan
    r = g[factor].rank(pct=True, method="average")
    q1 = g.loc[r <= 0.2, "fwd_ret"]
    q5 = g.loc[r >= 0.8, "fwd_ret"]
    if len(q1) < 10 or len(q5) < 10:
        return np.nan
    return float(q1.mean() - q5.mean())


def premiums_for_window(instruments, load_start: str, load_end: str, keep_start: str, keep_end: str):
    from qlib.data import D

    fields = [
        "$close/Ref($close,1)-1",
        "Log(Mean($volume*$vwap, 20)+1)",
        "Std($close/Ref($close,1)-1, 60)",
    ]
    df = D.features(instruments, fields, start_time=load_start, end_time=load_end)
    df.columns = ["ret", "size_proxy", "vol"]
    df = df.swaplevel().sort_index()
    df["fwd_ret"] = df.groupby(level="instrument")["ret"].shift(-1)
    df = df.dropna(subset=["size_proxy", "vol", "fwd_ret"])

    dates = df.index.get_level_values("datetime").unique().sort_values()
    keep_s, keep_e = pd.Timestamp(keep_start), pd.Timestamp(keep_end)
    rows = []
    for dt in dates:
        if dt < keep_s or dt > keep_e:
            continue
        day = df.loc[dt]
        if isinstance(day, pd.Series):
            continue
        rows.append(
            {
                "date": pd.Timestamp(dt).strftime("%Y-%m-%d"),
                "size": daily_ls(day, "size_proxy"),
                "lowvol": daily_ls(day, "vol"),
            }
        )
    del df
    gc.collect()
    return rows


def main() -> None:
    import qlib
    from qlib.config import C
    from qlib.data import D

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn", kernels=1)
    try:
        C["kernels"] = 1
    except Exception:
        pass

    instruments = D.instruments("csi1000")
    all_rows = []
    # Warm-up ~120 calendar days before each year for vol60 / size20 / fwd_ret
    windows = [
        ("2015-07-01", "2016-12-31", "2016-01-02", "2016-12-31"),
        ("2016-09-01", "2017-12-31", "2017-01-01", "2017-12-31"),
        ("2017-09-01", "2018-12-31", "2018-01-01", "2018-12-31"),
        ("2018-09-01", "2019-12-31", "2019-01-01", "2019-12-31"),
        ("2019-09-01", "2020-12-31", "2020-01-01", "2020-12-31"),
        ("2020-09-01", "2021-12-31", "2021-01-01", "2021-12-31"),
        ("2021-09-01", "2022-12-31", "2022-01-01", "2022-12-31"),
        ("2022-09-01", "2023-12-31", "2023-01-01", "2023-12-31"),
        ("2023-09-01", "2024-12-31", "2024-01-01", "2024-12-31"),
        ("2024-09-01", "2025-12-31", "2025-01-01", "2025-12-31"),
        ("2025-09-01", END, "2026-01-01", END),
    ]
    for load_s, load_e, keep_s, keep_e in windows:
        print(f"window load {load_s}~{load_e} keep {keep_s}~{keep_e}", flush=True)
        all_rows.extend(premiums_for_window(instruments, load_s, load_e, keep_s, keep_e))
        print(f"  rows so far: {len(all_rows)}", flush=True)

    prem = pd.DataFrame(all_rows).drop_duplicates("date").set_index("date").astype(float)
    prem = prem.sort_index().loc[PLOT_START:]
    roll = prem.rolling(60, min_periods=40).sum() * 100
    roll.index = pd.to_datetime(roll.index)
    monthly = roll.resample("ME").last().dropna(how="all")

    def ser(col: str) -> list:
        return [None if pd.isna(x) else round(float(x), 3) for x in monthly[col]]

    test = roll.loc["2021-07-16":]
    payload = {
        "definition": {
            "universe": "csi1000",
            "size_proxy": "Log(Mean($volume*$vwap, 20)+1) — liquidity-size proxy (no mcap in cn_data)",
            "lowvol": "Std(daily_ret, 60)",
            "daily_premium": "EW Q1 - EW Q5 next-day return (size: small-large; lowvol: low-high)",
            "rolling": "60-trading-day sum of daily premiums, in percent",
            "period": f"{PLOT_START} ~ {END}",
            "segments": {
                "train": ["2016-01-02", "2020-01-10"],
                "valid": ["2020-01-13", "2021-07-15"],
                "test": ["2021-07-16", "2026-07-16"],
            },
        },
        "monthly": {
            "dates": [d.strftime("%Y-%m") for d in monthly.index],
            "size_pct": ser("size"),
            "lowvol_pct": ser("lowvol"),
        },
        "summary": {
            "test_size_mean": round(float(test["size"].mean()), 3),
            "test_lowvol_mean": round(float(test["lowvol"].mean()), 3),
            "full_size_mean": round(float(roll["size"].mean()), 3),
            "full_lowvol_mean": round(float(roll["lowvol"].mean()), 3),
            "test_size_frac_positive": round(float((test["size"] > 0).mean()), 3),
            "test_lowvol_frac_positive": round(float((test["lowvol"] > 0).mean()), 3),
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written", OUT)
    print(json.dumps(payload["summary"], indent=2))
    print("n_monthly", len(monthly))


if __name__ == "__main__":
    main()
