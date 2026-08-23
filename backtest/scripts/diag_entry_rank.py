"""回测实际买入的标的，在当日信号里排第几名。

已确认信号时点是「t 日 pred → t+1 收盘成交」。若实际入场的名次远离 top-k，
说明执行层拿到的候选池与 Phase M 评估池不一致（过滤口径、不可成交替补等），
而不是模型问题。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(EXP_ROOT))
sys.path.insert(0, str(SCRIPTS))

from diag_sell_rule_gap import load_amounts  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--years", type=int, nargs="+", default=[2025, 2026])
    ap.add_argument("--config", default="backtest/configs/regime-adapt/phase-s/bt_m0h20es_all_top5d1_ensemble.yaml")
    args = ap.parse_args()

    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

    from config_loader import load_config
    from universe_filter import filter_pred, parse_universe_filter

    pred = pd.read_pickle(EXP_ROOT / args.pred)
    if isinstance(pred, pd.Series):
        pred = pred.to_frame("score")
    pred.columns = ["score"]

    cfg = load_config(args.config)
    spec = parse_universe_filter(cfg["universe_filter"])
    filtered, stats = filter_pred(pred, spec)
    keep = getattr(stats, "keep_rate", None)
    print(f"回测池过滤: 保留 {keep:.3%}" if keep is not None else f"回测池过滤: {stats}")

    raw_rank = pred.groupby(level="datetime")["score"].rank(ascending=False, method="first")
    flt_rank = filtered.groupby(level="datetime")["score"].rank(ascending=False, method="first")

    amounts = load_amounts(EXP_ROOT / args.session)
    days = list(amounts.index)
    held = amounts > 0
    prev_day = {d: days[i - 1] for i, d in enumerate(days) if i > 0}

    rows = []
    for name in amounts.columns:
        col = held[name].to_numpy()
        for i in range(1, len(col)):
            if col[i] and not col[i - 1]:
                d_in = days[i]
                d_sig = prev_day.get(d_in)
                if d_sig is None:
                    continue
                rows.append(
                    {
                        "year": d_in.year,
                        "raw": raw_rank.get((d_sig, name), np.nan),
                        "flt": flt_rank.get((d_sig, name), np.nan),
                    }
                )
    df = pd.DataFrame(rows)
    print(f"\n入场笔数 {len(df)}")
    for year in args.years:
        sub = df[df["year"] == year]
        if sub.empty:
            continue
        for col, lab in (("raw", "全池未过滤"), ("flt", "回测过滤池")):
            r = sub[col].dropna()
            miss = len(sub) - len(r)
            print(
                f"{year} {lab}: 中位名次 {r.median():.0f}  "
                f"top5内 {(r <= 5).mean():.1%}  top20内 {(r <= 20).mean():.1%}  "
                f"top100内 {(r <= 100).mean():.1%}  最差 {r.max():.0f}  池外/缺失 {miss}"
            )


if __name__ == "__main__":
    main()
