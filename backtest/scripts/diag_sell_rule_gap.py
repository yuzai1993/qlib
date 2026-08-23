"""度量「卖最差」与「满 h 天到期卖」之间的收益差。

Phase M 头部口径隐含的持仓规则是：t 日入场，持满 h 个交易日必卖。
`TopkDropoutStrategy` 的规则是：每天卖掉当前持仓里打分最差的 n_drop 只。
两者选出的标的集合几乎相同（实测近 5 日 top5 并集覆盖 93%），但**持有期不同**：
打分掉下去的被提前卖出（在低点兑现），打分还在的被继续持有（超过 h 天）。

本脚本对每一笔真实入场，对比：
- actual：真实持有期的收益（入场收盘 → 出场收盘）
- ideal ：同一入场日持满 h 个交易日的收益

并按仓位权重折算成对组合年度收益的影响。
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP_ROOT))

CASH_KEYS = {"cash", "now_account_value", "cash_delay"}


def load_amounts(session_dir: Path) -> pd.DataFrame:
    link = json.loads((session_dir / "run_01" / "mlruns_link.json").read_text())
    path = (
        EXP_ROOT
        / link["backtest_artifacts"]
        / "artifacts"
        / "portfolio_analysis"
        / "positions_normal_1day.pkl"
    )
    with open(path, "rb") as fh:
        raw = pickle.load(fh)
    rows = {}
    for day, pos in raw.items():
        held = getattr(pos, "position", pos)
        rows[pd.Timestamp(day)] = {
            k: float(v["amount"]) for k, v in held.items() if k not in CASH_KEYS
        }
    return pd.DataFrame(rows).T.sort_index().fillna(0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--years", type=int, nargs="+", default=[2025, 2026])
    ap.add_argument("--weight", type=float, default=None, help="单票权重，默认 1/持仓数")
    args = ap.parse_args()

    import qlib
    from qlib.constant import REG_CN
    from qlib.data import D

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

    amounts = load_amounts(EXP_ROOT / args.session)
    days = list(amounts.index)
    pos_of = {d: i for i, d in enumerate(days)}
    names = list(amounts.columns)

    close = D.features(names, ["$close"], start_time=str(days[0].date()), end_time="2026-08-14")
    close.index = close.index.set_names(["instrument", "datetime"])
    close = close.iloc[:, 0].unstack(0)
    cal = list(close.index)
    cal_pos = {d: i for i, d in enumerate(cal)}

    held = amounts > 0
    entries = []
    for name in names:
        col = held[name].to_numpy()
        for i in range(len(col)):
            if col[i] and (i == 0 or not col[i - 1]):
                j = i
                while j + 1 < len(col) and col[j + 1]:
                    j += 1
                entries.append((name, days[i], days[min(j + 1, len(days) - 1)], j - i + 1))

    def ret(name: str, d0: pd.Timestamp, d1: pd.Timestamp) -> float:
        try:
            p0, p1 = close.at[d0, name], close.at[d1, name]
        except KeyError:
            return np.nan
        if pd.isna(p0) or pd.isna(p1) or p0 == 0:
            return np.nan
        return float(p1) / float(p0) - 1.0

    n_hold = int(round(held.sum(axis=1).mean()))
    weight = args.weight if args.weight else 1.0 / n_hold

    print(f"会话 {args.session}  平均持仓 {n_hold} 只  单票权重 {weight:.1%}  h={args.h}")
    rows = []
    for year in args.years:
        recs = []
        for name, d_in, d_out, dur in entries:
            if d_in.year != year:
                continue
            i = cal_pos.get(d_in)
            if i is None or i + args.h >= len(cal):
                continue
            a = ret(name, d_in, d_out)
            b = ret(name, d_in, cal[i + args.h])
            if np.isnan(a) or np.isnan(b):
                continue
            recs.append((dur, a, b))
        if not recs:
            continue
        dur = np.array([r[0] for r in recs], dtype=float)
        act = np.array([r[1] for r in recs])
        idl = np.array([r[2] for r in recs])
        rows.append(
            {
                "year": year,
                "笔数": len(recs),
                "平均持有天": dur.mean(),
                "提前卖出占比": float((dur < args.h).mean()),
                "超期持有占比": float((dur > args.h).mean()),
                "actual均值": act.mean(),
                "ideal均值": idl.mean(),
                "每笔差": act.mean() - idl.mean(),
                "组合年化影响": (act.mean() - idl.mean()) * weight * len(recs),
            }
        )
    df = pd.DataFrame(rows).set_index("year")
    for c in ("提前卖出占比", "超期持有占比", "actual均值", "ideal均值", "每笔差", "组合年化影响"):
        df[c] = (df[c] * 100).round(2)
    df["平均持有天"] = df["平均持有天"].round(2)
    print(df.to_string())

    print("\n按真实持有天数分组（2026）：")
    recs = [
        (dur, ret(n, i, o), ret(n, i, cal[cal_pos[i] + args.h]))
        for n, i, o, dur in entries
        if i.year == 2026 and cal_pos.get(i) is not None and cal_pos[i] + args.h < len(cal)
    ]
    g = pd.DataFrame(recs, columns=["dur", "actual", "ideal"]).dropna()
    g["bucket"] = pd.cut(g["dur"], [0, 2, 4, 5, 7, 100], labels=["1-2", "3-4", "5", "6-7", "8+"])
    out = g.groupby("bucket", observed=True).agg(
        笔数=("actual", "size"),
        actual=("actual", "mean"),
        ideal=("ideal", "mean"),
    )
    out["差"] = out["actual"] - out["ideal"]
    print((out.assign(**{c: (out[c] * 100).round(2) for c in ("actual", "ideal", "差")})).to_string())


if __name__ == "__main__":
    main()
