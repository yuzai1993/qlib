"""诊断评估口径的「名义 h 天」与真实占用交易日之差，以及标签删失。

起因：真阶梯（`CohortLadderStrategy`）把入场只数、入场名次、退出规则、重复持有、
有效槽位数全部对齐主格后，2026 仍差 35pp（见 LESSONS 2026-08-22 条）。缺口不在组合
构造，只能在评估自身的收益计算与真实可成交性之间。

本脚本查两条：

1. **时间口径**：标签 `Ref($close, -6)/Ref($close, -1) - 1` 的 `Ref` 是在**个股自己的
   行序**上位移，不是市场日历。停牌股的「5 日收益」可能实际跨了 20 个交易日，却仍按
   `×238/5` 年化，等于把收益按 4 倍速记账。按真实占用交易日重算年化，看能吃掉多少缺口。
2. **标签删失**：`daily_head_panel` 先 `dropna()`，剩余行数不足 6 的（退市 / 长期停牌 /
   样本末端）整条消失，评估从未见过；回测却真拿着这些票。统计被删失的比例。

结论（2026-08-22 实测）：两条都不是主因。7240 个入选样本的真实占用交易日**全为 5 天**，
qlib cn_data 每只股票逐交易日建行，行序位移与市场日历一致；标签删失各年 ≤0.8%。
真因是窗口末端前视，见 `diag_eval_window_edge.py`。

注意：本脚本按 `span` 非空过滤，会连带丢掉窗口末尾每股最后 6 行，因此**不要**用它算
年化——那正好抹掉 2026-07 末端那几个巨额篮子日。年化一律走 diag_eval_window_edge.py。

只读数据，不写 registry。
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

HORIZON = 5
TRADING_DAYS = 238


def _load_pred(path: Path) -> pd.Series:
    pred = pd.read_pickle(path)
    if isinstance(pred, pd.DataFrame):
        pred = pred.iloc[:, 0]
    pred.index = pred.index.set_names(["datetime", "instrument"])
    return pred.sort_index()


def _market_positions(start: str, end: str) -> dict[pd.Timestamp, int]:
    from qlib.data import D

    calendar = pd.DatetimeIndex(D.calendar(start_time=start, end_time=end))
    return {ts: i for i, ts in enumerate(calendar)}


def _span_table(pool: str, start: str, end: str, horizon: int) -> pd.DataFrame:
    """每个 (datetime, instrument) 的标签、建仓日、平仓日与真实占用交易日数。

    建仓日 = 该股行序的下一行（对应 `Ref($close,-1)`），
    平仓日 = 再往后 horizon 行（对应 `Ref($close,-(horizon+1))`）。
    """
    from qlib.data import D

    frame = D.features(
        D.instruments(pool),
        ["$close", f"Ref($close, -{horizon + 1})/Ref($close, -1) - 1"],
        start_time=start,
        end_time=end,
    )
    frame.index = frame.index.set_names(["instrument", "datetime"])
    frame.columns = ["close", "label"]
    frame = frame.sort_index()

    dates = frame.index.get_level_values("datetime")
    frame["_dt"] = dates
    grouped = frame.groupby(level="instrument", sort=False)["_dt"]
    frame["entry_dt"] = grouped.shift(-1)
    frame["exit_dt"] = grouped.shift(-(horizon + 1))
    frame = frame.drop(columns=["_dt", "close"])

    pos = _market_positions(start, end)
    frame["span"] = [
        (pos[b] - pos[a]) if (pd.notna(a) and pd.notna(b) and a in pos and b in pos) else np.nan
        for a, b in zip(frame["entry_dt"], frame["exit_dt"])
    ]
    return frame.swaplevel().sort_index()


def _eval_universe_mask(index: pd.MultiIndex, pool: str, end: str, st_daily: Path) -> pd.Series:
    """复现 evaluate_multi_horizon 的过滤：非基金/非ST/上市>=60日/成交额>=1000万。"""
    import eval_ic_multi_pool as ev

    mask = pd.Series(True, index=index)
    if pool == "all":
        mask &= ev._stock_only_mask(index)
    mask &= ev._listing_age_mask(index, pool, 60, end)
    st_keep = ev._st_keep_mask(index, Path(st_daily), pool)
    if st_keep is not None:
        mask &= st_keep
    return mask


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred", required=True)
    parser.add_argument("--pool", default="all")
    parser.add_argument("--start", default="2020-08-03")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--st-daily", default="scripts/data_collector/tushare/st_daily.csv")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

    import eval_ic_multi_pool as ev

    pred = _load_pred(EXP_ROOT / args.pred)
    pred = pred[
        (pred.index.get_level_values("datetime") >= args.start)
        & (pred.index.get_level_values("datetime") <= args.end)
    ]

    spans = _span_table(args.pool, args.start, args.end, HORIZON)
    frame = pd.concat({"pred": pred}, axis=1).join(spans, how="left")

    keep = _eval_universe_mask(frame.index, args.pool, args.end, args.st_daily)
    amt = ev.amount_mask(args.pool, args.start, args.end, ev.DEFAULT_MIN_AMOUNT)
    keep &= amt.reindex(frame.index).fillna(False)
    tradable = ev.entry_tradable_mask(args.pool, args.start, args.end)
    keep &= tradable.reindex(frame.index).fillna(False)
    pooled = frame[keep.to_numpy()]
    print(f"评估宇宙保留 {keep.mean() * 100:.2f}%（含 t+1 可成交）", flush=True)

    # ---- 1. 标签删失：评估 dropna 之前 / 之后的 top-k 差异 ----
    print("\n=== 1. 标签删失（评估看不到、回测真拿着的票）===")
    print(f"{'年':<6}{'含NaN的top5占比':>16}{'被删失后顶上来的票':>20}")
    censor_rows = []
    for year, chunk in pooled.groupby(pooled.index.get_level_values("datetime").year):
        by_day = chunk.groupby(level="datetime")
        raw_rank = by_day["pred"].rank(ascending=False, method="first")
        raw_top = chunk[raw_rank <= args.k]
        nan_share = raw_top["label"].isna().mean()
        clean = chunk.dropna(subset=["label"])
        clean_rank = clean.groupby(level="datetime")["pred"].rank(
            ascending=False, method="first"
        )
        clean_top = clean[clean_rank <= args.k]
        promoted = len(clean_top) - (len(raw_top) - int(raw_top["label"].isna().sum()))
        censor_rows.append(
            {
                "year": int(year),
                "raw_top_nan_share": float(nan_share),
                "promoted": int(promoted),
                "clean_top_n": int(len(clean_top)),
            }
        )
        print(f"{year:<6}{nan_share * 100:15.3f}%{promoted:20d}")

    # ---- 2. 时间口径：名义 5 天 vs 真实占用交易日 ----
    clean = pooled.dropna(subset=["label", "span"])
    rank = clean.groupby(level="datetime")["pred"].rank(ascending=False, method="first")
    top = clean[rank <= args.k].copy()

    print(f"\n=== 2. 名义 h={HORIZON} vs 真实占用交易日（top{args.k} 入选样本）===")
    print(
        f"{'年':<6}{'样本':>7}{'中位':>6}{'均值':>7}{'p95':>6}{'最大':>6}{'>5天占比':>10}"
    )
    span_rows = []
    for year, chunk in top.groupby(top.index.get_level_values("datetime").year):
        span = chunk["span"].to_numpy(dtype=float)
        row = {
            "year": int(year),
            "n": int(len(chunk)),
            "span_median": float(np.median(span)),
            "span_mean": float(span.mean()),
            "span_p95": float(np.percentile(span, 95)),
            "span_max": float(span.max()),
            "share_over_h": float((span > HORIZON).mean()),
        }
        span_rows.append(row)
        print(
            f"{year:<6}{len(chunk):7d}{np.median(span):6.0f}{span.mean():7.2f}"
            f"{np.percentile(span, 95):6.0f}{span.max():6.0f}"
            f"{(span > HORIZON).mean() * 100:9.2f}%"
        )

    span = top["span"].to_numpy(dtype=float)
    print(
        f"{'全期':<6}{len(top):7d}{np.median(span):6.0f}{span.mean():7.2f}"
        f"{np.percentile(span, 95):6.0f}{span.max():6.0f}"
        f"{(span > HORIZON).mean() * 100:9.2f}%"
    )

    # ---- 3. 超期样本的收益贡献 ----
    print(f"\n=== 3. 按真实跨度分档的每笔收益（top{args.k}）===")
    bucket_rows = []
    buckets = [(5, 5), (6, 7), (8, 10), (11, 20), (21, 10_000)]
    for year in sorted(top.index.get_level_values("datetime").year.unique()):
        chunk = top[top.index.get_level_values("datetime").year == year]
        parts = []
        for lo, hi in buckets:
            sel = chunk[(chunk["span"] >= lo) & (chunk["span"] <= hi)]
            if sel.empty:
                parts.append("      —")
                continue
            parts.append(f"{sel['label'].mean() * 100:+6.2f}%")
            bucket_rows.append(
                {
                    "year": int(year),
                    "span_lo": lo,
                    "span_hi": hi,
                    "n": int(len(sel)),
                    "mean_ret": float(sel["label"].mean()),
                }
            )
        counts = [
            int(((chunk["span"] >= lo) & (chunk["span"] <= hi)).sum()) for lo, hi in buckets
        ]
        print(
            f"{year}  " + "  ".join(parts) + "   笔数 " + "/".join(str(c) for c in counts)
        )
    print("        =5天   6-7天   8-10天  11-20天   21+天")

    if args.out:
        out = EXP_ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "pred": args.pred,
                    "window": [args.start, args.end],
                    "k": args.k,
                    "horizon": HORIZON,
                    "censoring": censor_rows,
                    "span": span_rows,
                    "span_buckets": bucket_rows,
                    "full_period": {
                        "span_mean": float(span.mean()),
                        "share_over_h": float((span > HORIZON).mean()),
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
