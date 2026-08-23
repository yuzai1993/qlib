"""诊断 Phase M 头部评估与执行层回测的分年缺口来源。

只读已有产物（positions pickle + ensemble pred + report_normal.csv），不取 Qlib 行情。

对每个会话逐年输出：
- 实际持仓只数 / 现金占比 / 单票最大权重（集中度）
- 持仓平均年龄（对照 Phase M h=5 的 5 天）
- 当日持仓与当日 pred top-k 的重叠（信号漂移）
- 单票贡献的尾部（集中度伤害）
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
CASH_KEYS = {"cash", "now_account_value", "cash_delay"}


def load_positions(session_dir: Path) -> dict[pd.Timestamp, dict[str, Any]]:
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
    out: dict[pd.Timestamp, dict[str, Any]] = {}
    for day, pos in raw.items():
        holdings = getattr(pos, "position", pos)
        names = {k: v for k, v in holdings.items() if k not in CASH_KEYS}
        out[pd.Timestamp(day)] = {
            "value": holdings.get("now_account_value"),
            "cash": holdings.get("cash"),
            "names": names,
        }
    return out


def load_pred(path: Path) -> pd.Series:
    obj = pd.read_pickle(path)
    if isinstance(obj, pd.DataFrame):
        obj = obj["score"] if "score" in obj.columns else obj.iloc[:, 0]
    return pd.Series(obj).dropna()


def daily_topk(pred: pd.Series, k: int) -> dict[pd.Timestamp, set[str]]:
    out: dict[pd.Timestamp, set[str]] = {}
    for day, grp in pred.groupby(level=0):
        names = grp.droplevel(0).nlargest(k).index
        out[pd.Timestamp(day)] = set(str(n) for n in names)
    return out


def diagnose(
    session_dir: Path,
    pred_path: Optional[Path],
    topk: int,
    years: list[int],
) -> pd.DataFrame:
    pos = load_positions(session_dir)
    days = sorted(pos)
    tops = daily_topk(load_pred(pred_path), topk) if pred_path else {}

    age: dict[str, int] = {}
    rows = []
    for i, day in enumerate(days):
        rec = pos[day]
        names = rec["names"]
        value = rec["value"]
        held = set(names)
        for name in held:
            age[name] = age.get(name, 0) + 1
        for gone in [n for n in age if n not in held]:
            age.pop(gone)

        weights = []
        if value:
            for name, item in names.items():
                price = item.get("price")
                if price is None or pd.isna(price):
                    continue
                weights.append(float(item["amount"]) * float(price) / float(value))
        top_today = tops.get(day, set())
        rows.append(
            {
                "date": day,
                "year": day.year,
                "n_hold": len(names),
                "cash_ratio": (float(rec["cash"]) / float(value)) if value and rec["cash"] is not None else np.nan,
                "max_weight": max(weights) if weights else np.nan,
                "mean_weight": float(np.mean(weights)) if weights else np.nan,
                "mean_age": float(np.mean(list(age.values()))) if age else np.nan,
                "max_age": max(age.values()) if age else np.nan,
                "overlap_topk": (len(held & top_today) / topk) if top_today else np.nan,
            }
        )
    df = pd.DataFrame(rows).set_index("date")
    return df[df["year"].isin(years)]


def name_contrib(session_dir: Path, year: int) -> pd.Series:
    pos = load_positions(session_dir)
    days = sorted(pos)
    contrib: dict[str, float] = {}
    for prev, cur in zip(days, days[1:]):
        if cur.year != year:
            continue
        base = pos[prev]["value"]
        if not base:
            continue
        for name, item in pos[prev]["names"].items():
            nxt = pos[cur]["names"].get(name, {}).get("price")
            if nxt is None or pd.isna(nxt):
                continue
            pnl = float(item["amount"]) * (float(nxt) - float(item["price"])) / float(base)
            contrib[name] = contrib.get(name, 0.0) + pnl
    return pd.Series(contrib).sort_values()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True, help="会话目录（含 run_01）")
    ap.add_argument("--pred", default=None, help="同一份 ensemble pred.pkl")
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--years", type=int, nargs="+", default=[2025, 2026])
    ap.add_argument("--tail", type=int, default=6, help="打印单票亏损前 N 名")
    args = ap.parse_args()

    session = EXP_ROOT / args.session
    pred = EXP_ROOT / args.pred if args.pred else None
    df = diagnose(session, pred, args.topk, args.years)

    print(f"会话 {args.session}  topk={args.topk}")
    cols = ["n_hold", "cash_ratio", "max_weight", "mean_weight", "mean_age", "max_age", "overlap_topk"]
    print(df.groupby("year")[cols].mean().round(3).to_string())
    print()
    for year in args.years:
        s = name_contrib(session, year)
        if s.empty:
            continue
        neg = s[s < 0].sum()
        print(f"{year} 单票贡献合计 {s.sum():+.1%}  负贡献合计 {neg:+.1%}  标的数 {len(s)}")
        print("  最差:", ", ".join(f"{n} {v:+.1%}" for n, v in s.head(args.tail).items()))


if __name__ == "__main__":
    main()
