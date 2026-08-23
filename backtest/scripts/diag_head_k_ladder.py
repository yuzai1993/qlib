"""按 k 阶梯复刻 Phase M 头部口径，定位「每天买几只」对分年收益的影响。

动机：`TopkDropoutStrategy(topk=T, n_drop=D)` 每天新建仓的是「当日 top-T 里尚未持有
的最高分 D 只」，所以 top5d1 的入场流是**每天 1 只**，而 Phase M 主格 top5×h5 的入场流
是**每天等权 5 只**。两者不是同一个组合，不能互相预期。

本脚本在同一套过滤下输出 year × k 的绝对/超额年化，用于确认这一点。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(EXP_ROOT))
sys.path.insert(0, str(SCRIPTS))

import qlib  # noqa: E402
from qlib.constant import REG_CN  # noqa: E402

import eval_ic_multi_pool as ev  # noqa: E402
from diag_phasem_label_censoring import build_universe, load_pred  # noqa: E402

TD = ev.TRADING_DAYS_PER_YEAR


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True, type=Path)
    ap.add_argument("--ks", type=int, nargs="+", default=[1, 2, 3, 5, 15, 25])
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--start", default="2020-08-03")
    ap.add_argument("--end", default="2026-07-31")
    ap.add_argument(
        "--tradable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否按 t+1 可成交（剔涨停封板/零量）过滤候选池；关掉即不使用 t+1 信息",
    )
    args = ap.parse_args()

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

    pred = load_pred(EXP_ROOT / args.pred if not args.pred.is_absolute() else args.pred)
    h = int(args.h)
    mask = build_universe(args.start, args.end, 60, 1e7)
    label = ev._fetch_label("all", args.start, args.end, expression=ev._horizon_label_expr(h))
    tradable = ev.entry_tradable_mask("all", args.start, args.end) if args.tradable else None

    panel = ev.daily_head_panel(pred, label[mask], args.ks, min_count=20, tradable=tradable)

    tag = "剔t+1涨停" if args.tradable else "不用t+1信息"
    print(f"pred={args.pred}  h={h}  过滤=上市>=60 + 日频ST + 成交额>=1000万 + {tag}")
    for kind in ("ann_abs", "ann_excess"):
        rows = {}
        for k in args.ks:
            series = panel[k]["port" if kind == "ann_abs" else "excess"]
            per = {}
            for year in sorted(set(series.index.year)):
                m = series.index.year == year
                per[int(year)] = series[m].mean() * TD / h * 100
            cost = TD * (ev.topk_turnover(panel[k]["sets"], k, h) or 0) / h * ev.COST_ROUND_TRIP * 100
            per["全期"] = series.mean() * TD / h * 100
            per["成本"] = cost
            rows[f"k={k}"] = per
        print(f"\n=== {kind}（%，未扣费；最后一列为年化成本）===")
        print(pd.DataFrame(rows).T.round(1).to_string())


if __name__ == "__main__":
    main()
