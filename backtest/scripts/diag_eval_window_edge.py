"""量化评估口径的「窗口末端前视」：平仓日落在回测窗口之外的评估日贡献了多少年化。

背景链条（见 LESSONS 2026-08-22）：真阶梯把入场只数/名次/退出规则/重复持有/槽位数
全部对齐主格后，2026 仍差 35pp。`diag_eval_holding_span.py` 排除了「行序位移 ≠ 市场日历」
（7240 个入选样本跨度全为 5 天），`diag_eval_2026_replication.py` 排除了「评估实现有误」
（逐年复现与 JSON 差 0.1~2.8pp）。

真因：评估在日 t 记入 t+1→t+6 收盘的收益。对窗口末尾 h+1=6 个评估日，平仓日落在
窗口结束之后（2026-08-03~08-21），回测在 2026-07-31 停止交易并按当日收盘估值，
这段收益永远兑现不了。2026 只有 139 个评估日，这 6 天权重约 4.3%，而它们的篮子收益
是 +25%~+35%（常态约 +0.9%），于是主导了全年。

本脚本对每一年重算两个口径：
- `ann`：评估现口径（全部评估日）；
- `ann_in_window`：只保留 pos(t)+h+1 <= pos(窗口末日) 的评估日，即收益能在窗口内兑现。
成本按保留日重算换手，net = ann − 238 × (换手_period/h) × 0.092%。
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


def _ann(port: pd.Series, trading_days: int) -> float | None:
    if port.empty:
        return None
    return float(port.mean()) * trading_days / H


def _cost(sets: dict, days: set | None, trading_days: int, cost_round_trip: float):
    import eval_ic_multi_pool as ev

    period = ev.topk_turnover(sets, K, H, days=days)
    if period is None:
        return None, None
    return float(period), trading_days * (period / H) * cost_round_trip


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred", required=True)
    parser.add_argument("--eval-json", required=True)
    parser.add_argument("--pool", default="all")
    parser.add_argument("--start", default="2020-08-03")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--st-daily", default="scripts/data_collector/tushare/st_daily.csv")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

    import eval_ic_multi_pool as ev
    from qlib.data import D

    td = ev.TRADING_DAYS_PER_YEAR
    crt = ev.COST_ROUND_TRIP

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
    port, sets = panel["port"], panel["sets"]

    # 窗口内可兑现的评估日：pos(t) + h + 1 <= pos(窗口末日)
    calendar = pd.DatetimeIndex(D.calendar(start_time=args.start, end_time=args.end))
    last_pos = len(calendar) - 1
    pos = {ts: i for i, ts in enumerate(calendar)}
    in_window = pd.Index(
        [d for d in port.index if pos.get(d) is not None and pos[d] + H + 1 <= last_pos]
    )
    dropped = port.index.difference(in_window)
    print(
        f"窗口末日 {calendar[-1].date()}；剔除平仓日越界的 {len(dropped)} 个评估日："
        + ", ".join(str(d.date()) for d in dropped)
    )

    doc = json.loads((EXP_ROOT / args.eval_json).read_text())
    ens = doc["pools"][args.pool]["ensemble"]
    hy = ens.get("head_years") or {}

    print(
        f"\n{'年':<6}{'天数':>5}{'剔除':>5}"
        f"{'评估净年化':>11}{'窗口内净年化':>13}{'差':>10}{'JSON净年化':>11}"
    )
    rows = []
    years = sorted({ts.year for ts in port.index})
    for year in years:
        full_days = pd.Index([d for d in port.index if d.year == year])
        keep_days = pd.Index([d for d in in_window if d.year == year])
        ann_full = _ann(port.reindex(full_days), td)
        ann_keep = _ann(port.reindex(keep_days), td)
        _, cost_full = _cost(sets, set(full_days), td, crt)
        _, cost_keep = _cost(sets, set(keep_days), td, crt)
        net_full = None if ann_full is None or cost_full is None else ann_full - cost_full
        net_keep = None if ann_keep is None or cost_keep is None else ann_keep - cost_keep
        cell = ((hy.get(str(year)) or {}).get(str(K)) or {}).get(str(H)) or {}
        json_net = cell.get("net_ann")
        rows.append(
            {
                "year": int(year),
                "n_days": len(full_days),
                "n_dropped": len(full_days) - len(keep_days),
                "ann": ann_full,
                "ann_in_window": ann_keep,
                "net_ann": net_full,
                "net_ann_in_window": net_keep,
                "json_net_ann": json_net,
            }
        )
        gap = "" if None in (net_full, net_keep) else f"{(net_keep - net_full) * 100:9.1f}pp"
        j = "      —" if json_net is None else f"{json_net * 100:10.1f}%"
        print(
            f"{year:<6}{len(full_days):5d}{len(full_days) - len(keep_days):5d}"
            f"{net_full * 100:10.1f}%{net_keep * 100:12.1f}%{gap}{j}"
        )

    ann_full = _ann(port, td)
    ann_keep = _ann(port.reindex(in_window), td)
    _, cost_full = _cost(sets, None, td, crt)
    _, cost_keep = _cost(sets, set(in_window), td, crt)
    net_full = ann_full - cost_full
    net_keep = ann_keep - cost_keep
    print(
        f"{'全期':<6}{len(port):5d}{len(dropped):5d}"
        f"{net_full * 100:10.1f}%{net_keep * 100:12.1f}%"
        f"{(net_keep - net_full) * 100:9.1f}pp"
        f"{ens['head'][str(K)][str(H)]['net_ann'] * 100:10.1f}%"
    )

    print("\n=== 被剔除评估日的篮子 5 日收益（对比该年常态）===")
    for d in dropped:
        year_days = [x for x in port.index if x.year == d.year]
        print(
            f"  {d.date()}  {port[d] * 100:+7.2f}%"
            f"   （{d.year} 年日均 {port.reindex(year_days).mean() * 100:+.3f}%）"
        )

    if args.out:
        out = EXP_ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "pred": args.pred,
                    "window": [args.start, args.end],
                    "k": K,
                    "horizon": H,
                    "window_last_day": str(calendar[-1].date()),
                    "dropped_days": [str(d.date()) for d in dropped],
                    "dropped_port": {str(d.date()): float(port[d]) for d in dropped},
                    "years": rows,
                    "full_period": {
                        "n_days": int(len(port)),
                        "ann": ann_full,
                        "ann_in_window": ann_keep,
                        "net_ann": net_full,
                        "net_ann_in_window": net_keep,
                    },
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\n写出 {out.relative_to(EXP_ROOT)}")


if __name__ == "__main__":
    main()
