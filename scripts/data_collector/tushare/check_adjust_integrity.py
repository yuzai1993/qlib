"""前复权回溯完整性巡检。

原理：
  前复权价比值 close[t]/close[t-1] - 1 应与 $change（Tushare pct_chg）在所有
  交易日近似相等（含除权日：复权因子恰好抵消除权跳空）。
  若「除权日」($factor 发生变动的日子) 上出现大偏差，说明该股票的历史
  前复权价没有被增量更新正确回溯重写。

用法：
  python scripts/data_collector/tushare/check_adjust_integrity.py \\
      --instruments csi300 --start 2024-01-01 --tol 0.002

退出码：除权日偏差行数 > 0 时返回 1（可接入 cron 告警）。
"""
import argparse
import sys

import pandas as pd

import qlib
from qlib.constant import REG_CN
from qlib.data import D


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="~/.qlib/qlib_data/cn_data")
    parser.add_argument("--instruments", default="csi300")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--tol", type=float, default=0.002)
    args = parser.parse_args()

    qlib.init(provider_uri=args.provider, region=REG_CN, kernels=1)
    inst = D.instruments(args.instruments)
    df = D.features(
        inst, ["$close", "$change", "$factor"],
        start_time=args.start, end_time=args.end,
    ).dropna(subset=["$close"])

    ratio = df["$close"].groupby(level="instrument").pct_change()
    diff = (ratio - df["$change"]).abs()
    checked = int(diff.notna().sum())
    bad_mask = diff > args.tol

    # 除权日 = $factor 相对前一日发生变化的日子
    factor_chg = (
        df["$factor"].groupby(level="instrument").pct_change().abs() > 1e-8
    )

    # 排除成分股空窗：上一观测日必须是上一交易日，否则 pct_change 跨空窗无意义
    # （股票调出指数再调入时，前后 close/factor 锚点不同，会假阳性）
    cal = list(D.calendar(start_time=args.start, end_time=args.end))
    prev_cal = {pd.Timestamp(cal[i]): pd.Timestamp(cal[i - 1]) for i in range(1, len(cal))}
    dt = pd.DatetimeIndex(df.index.get_level_values("datetime"))
    prev_obs = (
        pd.Series(dt, index=df.index)
        .groupby(level="instrument")
        .shift(1)
    )
    expected_prev = dt.map(prev_cal)
    contiguous = prev_obs.notna() & (prev_obs.values == pd.DatetimeIndex(expected_prev).values)

    bad_exdiv = df[bad_mask & factor_chg & contiguous]

    print(f"检查区间: {args.start} ~ {args.end or '最新'}, 股票池: {args.instruments}")
    print(f"有效样本: {checked} 行")
    print(f"比值-涨跌幅偏差 > {args.tol}: {int(bad_mask.sum())} 行 "
          f"({bad_mask.sum() / max(checked, 1):.4%})")
    print(f"其中发生在除权日（已排除成分股空窗）: {len(bad_exdiv)} 行")
    skipped_gap = int((bad_mask & factor_chg & ~contiguous).sum())
    if skipped_gap:
        print(f"（另有 {skipped_gap} 行因成分股空窗跳过，属预期误报）")

    if len(bad_exdiv) > 0:
        print("\n!!! 除权日偏差明细（前 20 行）—— 前复权回溯可能失效：")
        detail = bad_exdiv[["$close", "$change", "$factor"]].copy()
        detail["ratio_ret"] = ratio[bad_mask & factor_chg & contiguous]
        print(detail.head(20).to_string())
        return 1

    print("OK: 未发现除权日回溯异常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
