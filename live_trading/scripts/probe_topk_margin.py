"""统计全测试期 rank-k 与 rank-(k+1) 的分数间距，用来界定选股会不会被信号偏差翻掉。

尺度取仿射残差（diagnose_ladder_gap.py 量出的 1.69e-05），不是原始分数差
1.33e-03。live 与回测的差异几乎是逐日的仿射变换，而仿射保序——真正能改变
top-k 的只有那点非仿射残差。用原始分数差当阈值会把风险高估约 80 倍。
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

BT_V4_PRED = (
    PROJECT_ROOT / "backtest" / "result"
    / "20260822_233132_phase_s_m0h20rankices_all_ladder_k3h5_ensemble"
    / "external_pred.pkl"
)

# 实测残差、6 倍保守上界、以及被误用的原始分数差。
THRESHOLDS = (1.69e-05, 1.0e-04, 1.33e-03)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument("--start", default="2021-07-16")
    parser.add_argument("--end", default="2026-07-16")
    args = parser.parse_args()

    pred = pd.read_pickle(BT_V4_PRED)["score"]
    dates = pred.index.get_level_values("datetime").unique()
    dates = dates[(dates >= pd.Timestamp(args.start))
                  & (dates <= pd.Timestamp(args.end))]

    margins = []
    for date in dates:
        day = pred.xs(date, level="datetime").dropna()
        if len(day) <= args.topk:
            continue
        top = np.partition(day.values, -(args.topk + 1))[-(args.topk + 1):]
        top.sort()
        margins.append(top[-1 - (args.topk - 1)] - top[-1 - args.topk])
    margins = np.asarray(margins)

    print("days: %d (%s .. %s), topk=%d"
          % (len(margins), dates[0].date(), dates[-1].date(), args.topk))
    print("margin percentiles: p1=%.3g p5=%.3g p25=%.3g p50=%.3g"
          % tuple(np.percentile(margins, [1, 5, 25, 50])))
    for threshold in THRESHOLDS:
        at_risk = int((margins < threshold).sum())
        print("margin < %-9.3g : %4d days (%.3f%%)"
              % (threshold, at_risk, 100.0 * at_risk / len(margins)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
