"""预计算等权全A 日收益基准，供 Phase S 回测当 benchmark 用。

为什么要它：全A 池回测原先用 SH000300 当基准，而组合从全A 选股（以小盘为主），
基准张不开组合的因子暴露 —— 未被 benchmark 吸收的小盘因子收益全落进 CAPM 残差，
alpha 被系统性抬高，且低 CSI300-beta 的臂会机械地占便宜。本地无中证全指 SH000985，
故自建等权全A。它同时与 Phase M 头部口径的基准（当日全A 等权均值）对齐，
使两阶段可直接对排。

口径与 qlib 传 benchmark=list 时的内部算法一致（见 qlib/backtest/report.py
`PortfolioMetrics._cal_benchmark`）：逐日对池内个股 $close/Ref($close,1)-1 取均值
（NaN 即未上市/停牌，被 mean 跳过），再 fillna(0)。预计算成 CSV 只是避免每次回测
重算 5000+ 只，数值应当等价。

用法: python backtest/scripts/build_equal_weight_benchmark.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = EXP_ROOT / "backtest" / "configs" / "regime-adapt" / "bench_ew_all.csv"
# 与 run_regime_phase_s.ALL_A_INSTRUMENTS / 训练池同一口径：主板+创业板+科创板，剔 B 股/北交所
NAME_RULE_RE = "^(SH60|SH68|SZ00|SZ30)"
START = "2019-01-01"
END = "2026-08-01"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=START)
    p.add_argument("--end", default=END)
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()

    import qlib
    from qlib.data import D

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

    instruments = D.instruments(
        market="all",
        filter_pipe=[
            {
                "filter_type": "NameDFilter",
                "name_rule_re": NAME_RULE_RE,
                "filter_start_time": None,
                "filter_end_time": None,
            }
        ],
    )
    codes = D.list_instruments(
        instruments, start_time=args.start, end_time=args.end, as_list=True
    )
    print(f"池内标的数: {len(codes)}")

    feat = D.features(
        codes, ["$close/Ref($close,1)-1"], start_time=args.start, end_time=args.end
    )
    col = feat.columns[0]
    bench = feat.groupby(level="datetime", group_keys=False)[col].mean().fillna(0.0)
    bench = bench.sort_index()

    n_stocks = feat[col].groupby(level="datetime").count()
    print(f"交易日数: {len(bench)}  日均有效个股数: {n_stocks.mean():.0f}")
    print(f"年化收益: {bench.mean() * 250:.2%}  年化波动: {bench.std() * 250**0.5:.2%}")
    print(f"累计收益: {(1 + bench).prod() - 1:.2%}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    bench.rename("bench").to_frame().to_csv(out, index_label="datetime")
    print(f"已写出: {out}")


if __name__ == "__main__":
    main()
