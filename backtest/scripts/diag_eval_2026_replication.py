"""用评估自己的 daily_head_panel 复算主格分年年化，与 eval JSON 逐年对账。

起因：`diag_eval_holding_span.py` 复现主格公式时，2020-2025 与 eval JSON 差 1~3pp，
但 2026 差 38pp（复现 +3.8% 毛 vs JSON +41.9% 毛），且复现值贴合真阶梯回测的 +2.8%。
需要先排除「我手写排序/过滤与评估不一致」，再判断是 JSON 数字有问题还是复现有问题。

本脚本不手写任何选股逻辑，全部调用 eval_ic_multi_pool 的函数。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP_ROOT))
sys.path.insert(0, str(EXP_ROOT / "backtest" / "scripts"))

K = 5
H = 5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred", required=True)
    parser.add_argument("--eval-json", required=True)
    parser.add_argument("--pool", default="all")
    parser.add_argument("--start", default="2020-08-03")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--st-daily", default="scripts/data_collector/tushare/st_daily.csv")
    args = parser.parse_args()

    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

    import eval_ic_multi_pool as ev

    pred = pd.read_pickle(EXP_ROOT / args.pred)
    if isinstance(pred, pd.DataFrame):
        pred = pred.iloc[:, 0]
    pred.index = pred.index.set_names(["datetime", "instrument"])
    pred = pred.sort_index()
    pred = pred[
        (pred.index.get_level_values("datetime") >= args.start)
        & (pred.index.get_level_values("datetime") <= args.end)
    ]

    label = ev._fetch_label(
        args.pool, args.start, args.end, expression=ev._horizon_label_expr(H)
    )
    mask = pd.Series(True, index=label.index)
    if args.pool == "all":
        mask &= ev._stock_only_mask(label.index)
    mask &= ev._listing_age_mask(label.index, args.pool, 60, args.end)
    st_keep = ev._st_keep_mask(label.index, EXP_ROOT / args.st_daily, args.pool)
    if st_keep is not None:
        mask &= st_keep
    amt = ev.amount_mask(args.pool, args.start, args.end, ev.DEFAULT_MIN_AMOUNT)
    mask &= amt.reindex(label.index).fillna(False)
    label = label[mask]
    tradable = ev.entry_tradable_mask(args.pool, args.start, args.end)

    panel = ev.daily_head_panel(pred, label, [K], tradable=tradable)[K]
    port = panel["port"]
    bench = panel["bench"]
    print(f"复现 port 覆盖 {len(port)} 天：{port.index.min().date()} ~ {port.index.max().date()}")

    doc = json.loads((EXP_ROOT / args.eval_json).read_text())
    ens = doc["pools"][args.pool]["ensemble"]
    hy = ens.get("head_years") or {}

    print(f"\n{'年':<6}{'复现天数':>9}{'JSON天数':>9}{'复现毛年化':>11}{'JSON毛年化':>11}{'差':>9}")
    rows = []
    for year in sorted({ts.year for ts in port.index}):
        sub = port[port.index.year == year]
        mine = float(sub.mean()) * ev.TRADING_DAYS_PER_YEAR / H
        cell = ((hy.get(str(year)) or {}).get(str(K)) or {}).get(str(H)) or {}
        theirs = cell.get("ann")
        n_json = cell.get("n_days")
        rows.append((year, len(sub), n_json, mine, theirs))
        gap = "" if theirs is None else f"{(mine - theirs) * 100:8.1f}pp"
        t = "      —" if theirs is None else f"{theirs * 100:10.1f}%"
        print(f"{year:<6}{len(sub):9d}{(n_json or 0):9d}{mine * 100:10.1f}%{t}{gap}")

    full_mine = float(port.mean()) * ev.TRADING_DAYS_PER_YEAR / H
    full_theirs = ens["head"][str(K)][str(H)]["ann"]
    print(
        f"{'全期':<6}{len(port):9d}{ens['head'][str(K)][str(H)]['n_days']:9d}"
        f"{full_mine * 100:10.1f}%{full_theirs * 100:10.1f}%"
        f"{(full_mine - full_theirs) * 100:8.1f}pp"
    )

    # 2026 逐月拆解：定位差异是全年均匀还是集中在少数日子
    y26 = port[port.index.year == 2026]
    if len(y26):
        print("\n2026 逐月（复现）：月内日均篮子收益 / 天数 / 折年化")
        for month, chunk in y26.groupby(y26.index.month):
            ann = float(chunk.mean()) * ev.TRADING_DAYS_PER_YEAR / H
            print(
                f"  2026-{month:02d}  日均 {chunk.mean() * 100:+6.3f}%"
                f"  天数 {len(chunk):3d}  折年化 {ann * 100:+7.1f}%"
            )
        top_days = y26.sort_values(ascending=False).head(5)
        print("  最好 5 天：" + ", ".join(
            f"{d.date()} {v * 100:+.1f}%" for d, v in top_days.items()
        ))
        worst = y26.sort_values().head(5)
        print("  最差 5 天：" + ", ".join(
            f"{d.date()} {v * 100:+.1f}%" for d, v in worst.items()
        ))
        print(f"  基准（同池等权）2026 折年化 "
              f"{float(bench[bench.index.year == 2026].mean()) * ev.TRADING_DAYS_PER_YEAR / H * 100:+.1f}%")


if __name__ == "__main__":
    main()
