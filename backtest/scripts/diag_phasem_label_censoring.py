"""复刻 Phase M 头部口径，并度量「标签 NaN 静默剔除」对头部收益的抬升。

Phase M 的 `daily_head_panel` 在排序前先对 (pred, label) 做 dropna，因此任何在
未来 h 日内停牌/退市（前瞻标签取不到）的股票，会连同它的亏损一起从候选池和基准里
消失。实盘不会消失：钱已经买进去了。

本脚本用同一套过滤（stock_only + 上市>=60 + 日频 ST + 成交额>=1000万 + 剔 t+1 涨停）
分两种排序口径对比：
- `phase_m`：先 dropna 再取 top-k（现行官方口径）
- `no_censor`：先取 top-k（只要求 pred 与过滤通过），再看这些标的里有多少条标签是 NaN

用法：
    /opt/anaconda3/envs/qlib/bin/python backtest/scripts/diag_phasem_label_censoring.py \
        --pred backtest/result/phase_s_regime/all_top5d1/m0h20es_ensemble_pred.pkl \
        --k 5 --h 5
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

import qlib  # noqa: E402
from qlib.constant import REG_CN  # noqa: E402

import eval_ic_multi_pool as ev  # noqa: E402

TRADING_DAYS = ev.TRADING_DAYS_PER_YEAR


def load_pred(path: Path) -> pd.Series:
    obj = pd.read_pickle(path)
    if isinstance(obj, pd.DataFrame):
        obj = obj["score"] if "score" in obj.columns else obj.iloc[:, 0]
    s = pd.Series(obj).dropna()
    s.index = s.index.set_names(["datetime", "instrument"])
    return s.sort_index()


def build_universe(start: str, end: str, min_listing_days: int, min_amount: float) -> pd.Series:
    label = ev._fetch_label("all", start, end, expression=ev._horizon_label_expr(1))
    mask = pd.Series(True, index=label.index)
    mask &= ev._stock_only_mask(label.index)
    mask &= ev._listing_age_mask(label.index, "all", min_listing_days, end)
    st_keep = ev._st_keep_mask(label.index, ev.DEFAULT_ST_DAILY, "all")
    if st_keep is not None:
        mask &= st_keep
    if min_amount > 0:
        mask &= ev.amount_mask("all", start, end, min_amount).reindex(label.index).fillna(False)
    return mask


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True, type=Path)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--start", default="2020-08-03")
    ap.add_argument("--end", default="2026-07-31")
    ap.add_argument("--min-listing-days", type=int, default=60)
    ap.add_argument("--min-amount", type=float, default=1e7)
    ap.add_argument("--names", nargs="*", default=["SZ300010"])
    args = ap.parse_args()

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

    pred = load_pred(EXP_ROOT / args.pred if not args.pred.is_absolute() else args.pred)
    k, h = int(args.k), int(args.h)

    mask = build_universe(args.start, args.end, args.min_listing_days, args.min_amount)
    label = ev._fetch_label("all", args.start, args.end, expression=ev._horizon_label_expr(h))
    tradable = ev.entry_tradable_mask("all", args.start, args.end)

    # 候选池 = 过滤通过 ∩ t+1 可成交 ∩ 有 pred（不要求标签非空）
    idx = mask[mask].index
    cand = pd.DataFrame({"pred": pred.reindex(idx), "label": label.reindex(idx)})
    cand = cand[tradable.reindex(idx).fillna(False).to_numpy()]
    cand = cand[cand["pred"].notna()]

    dt = cand.index.get_level_values("datetime")
    rank = cand.groupby(level="datetime")["pred"].rank(ascending=False, method="first")
    picks = cand[rank <= k].copy()
    picks["year"] = picks.index.get_level_values("datetime").year

    print(f"pred={args.pred}  k={k} h={h}")
    print(f"候选池样本 {len(cand):,}  其中标签 NaN {int(cand['label'].isna().sum()):,} "
          f"({cand['label'].isna().mean():.3%})")
    print()
    print("=== 无删失口径下的 top-k 选股：有多少被现行 dropna 静默剔除 ===")
    rows = []
    for year, grp in picks.groupby("year"):
        n = len(grp)
        nan = int(grp["label"].isna().sum())
        kept = grp["label"].dropna()
        rows.append(
            {
                "year": int(year),
                "n_picks": n,
                "n_label_nan": nan,
                "pct_censored": nan / n if n else np.nan,
                "ann_abs_kept": kept.mean() * TRADING_DAYS / h if len(kept) else np.nan,
            }
        )
    df = pd.DataFrame(rows).set_index("year")
    df["pct_censored"] = (df["pct_censored"] * 100).round(2)
    df["ann_abs_kept"] = (df["ann_abs_kept"] * 100).round(1)
    print(df.to_string())
    print()

    # 现行官方口径复刻：先 dropna 再排序
    panel = ev.daily_head_panel(
        pred, label[mask], [k], min_count=20, tradable=tradable
    )
    port = panel[k]["port"]
    bench = panel[k]["bench"]
    excess = panel[k]["excess"]
    print("=== 现行 Phase M 口径复刻（先 dropna 再 top-k）===")
    out = []
    for year in sorted(set(port.index.year)):
        m = port.index.year == year
        out.append(
            {
                "year": int(year),
                "n_days": int(m.sum()),
                "ann_abs": port[m].mean() * TRADING_DAYS / h * 100,
                "ann_bench": bench[m].mean() * TRADING_DAYS / h * 100,
                "ann_excess": excess[m].mean() * TRADING_DAYS / h * 100,
            }
        )
    print(pd.DataFrame(out).set_index("year").round(1).to_string())
    print()

    for name in args.names:
        sub = cand.xs(name, level="instrument", drop_level=False)
        if sub.empty:
            print(f"{name}: 不在候选池")
            continue
        r = rank.xs(name, level="instrument", drop_level=False)
        inpick = sub[r.reindex(sub.index) <= k]
        print(f"=== {name} ===")
        print(f"  在候选池天数 {len(sub)}  进 top{k} 天数 {len(inpick)}  "
              f"其中标签 NaN {int(inpick['label'].isna().sum())}")
        tail = sub.tail(12)
        print("  最后 12 个候选日 (pred / h%d 标签):" % h)
        for (d, _), row in tail.iterrows():
            lab = "NaN" if pd.isna(row["label"]) else f"{row['label']:+.1%}"
            print(f"    {pd.Timestamp(d).date()}  pred {row['pred']:+.3f}  label {lab}")


if __name__ == "__main__":
    main()
