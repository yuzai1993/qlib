"""查评估窗口末端若干日的头部标的与其价格路径，判断巨额标签是真行情还是数据边缘伪影。

起因：主格 top5×h5 在 2026 的 +42% 毛年化中，约 77% 来自 2026-07-29/30/31 三天
（篮子 5 日收益 +25.6% / +35.4% / +32.9%）。这三天的建仓日在回测窗口末端，回测无论如何
都兑现不了；且量级异常，需要核对复权价路径与 $factor 是否在数据末尾发生跳变。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP_ROOT))
sys.path.insert(0, str(EXP_ROOT / "backtest" / "scripts"))

K = 5
H = 5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred", required=True)
    parser.add_argument("--pool", default="all")
    parser.add_argument("--start", default="2020-08-03")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--days", type=int, default=6, help="检查窗口末尾几个评估日")
    parser.add_argument("--st-daily", default="scripts/data_collector/tushare/st_daily.csv")
    args = parser.parse_args()

    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

    import eval_ic_multi_pool as ev
    from qlib.data import D

    calendar = pd.DatetimeIndex(D.calendar(start_time="2026-06-01"))
    print(f"数据日历末端：{[str(d.date()) for d in calendar[-12:]]}")
    print(f"数据最后一个交易日 = {calendar[-1].date()}")

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
    sets = panel["sets"]
    port = panel["port"]
    days = sorted(sets)[-args.days :]

    names = sorted({c for d in days for c in sets[d]})
    px = D.features(
        names,
        ["$close", "$factor", "$volume", "Ref($close, -1)", f"Ref($close, -{H + 1})"],
        start_time="2026-07-01",
        end_time=args.end,
    )
    px.index = px.index.set_names(["instrument", "datetime"])
    px.columns = ["close", "factor", "volume", "close_entry", "close_exit"]

    print(f"\n=== 窗口末端 {args.days} 个评估日的 top{K} ===")
    for d in days:
        print(f"\n{d.date()}  篮子 5 日收益 = {port[d] * 100:+.2f}%")
        for code in sorted(sets[d]):
            try:
                row = px.loc[(code, d)]
            except KeyError:
                print(f"  {code}  <无价格行>")
                continue
            r = row["close_exit"] / row["close_entry"] - 1.0
            print(
                f"  {code}  复权close={row['close']:9.3f}  factor={row['factor']:.6f}"
                f"  建仓close={row['close_entry']:9.3f}  平仓close={row['close_exit']:9.3f}"
                f"  收益={r * 100:+8.2f}%"
            )

    print("\n=== 这些标的 2026-07 的复权 close / factor 路径（查末端跳变）===")
    for code in names:
        sub = px.loc[code].tail(10)
        print(f"\n{code}")
        for dt, row in sub.iterrows():
            print(
                f"  {dt.date()}  close={row['close']:10.3f}  factor={row['factor']:.6f}"
                f"  vol={row['volume']:12.0f}"
            )


if __name__ == "__main__":
    main()
