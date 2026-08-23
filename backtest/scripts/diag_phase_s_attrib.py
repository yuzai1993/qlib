"""把 Phase S 某年的组合收益拆到个股，定位亏损来源。

逐日贡献按 amount_{t-1} * (price_t - price_{t-1}) / account_value_{t-1} 计算，
忽略当日盘中调仓与交易成本，用于判断"是谁把这一年打没了"而非精确复现净值。

用法：
    python backtest/scripts/diag_phase_s_2026_attrib.py [seed] [year] [结果 JSON]
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = ROOT / "backtest" / "result" / "phase_s_regime" / "all_top5d1" / "m0h20.json"
CASH_KEYS = {"cash", "now_account_value", "cash_delay"}


def _positions(session_dir: Path) -> dict:
    link = json.loads((session_dir / "run_01" / "mlruns_link.json").read_text())
    art = ROOT / link["backtest_artifacts"] / "artifacts"
    with (art / "portfolio_analysis" / "positions_normal_1day.pkl").open("rb") as fh:
        return pickle.load(fh)


def main() -> None:
    seed = sys.argv[1] if len(sys.argv) > 1 else "3000"
    year = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    res_json = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_JSON

    doc = json.loads(res_json.read_text())
    entry = doc["seeds"][seed]
    pos = _positions(ROOT / entry["session_dir"])

    dates = sorted(pd.Timestamp(d) for d in pos)
    frames = {}
    for d in dates:
        holding = getattr(pos[d], "position", pos[d])
        frames[d] = {
            "value": holding.get("now_account_value", float("nan")),
            "names": {k: v for k, v in holding.items() if k not in CASH_KEYS},
        }

    contrib: dict[str, float] = {}
    days: dict[str, int] = {}
    rows = []
    for prev, cur in zip(dates, dates[1:]):
        if cur.year != year:
            continue
        v0 = frames[prev]["value"]
        if not v0 or pd.isna(v0):
            continue
        day_total = 0.0
        for name, rec in frames[prev]["names"].items():
            p1 = frames[cur]["names"].get(name, {}).get("price")
            if p1 is None or pd.isna(p1):
                continue
            pnl = float(rec["amount"]) * (float(p1) - float(rec["price"])) / v0
            contrib[name] = contrib.get(name, 0.0) + pnl
            days[name] = days.get(name, 0) + 1
            day_total += pnl
        rows.append({"datetime": cur, "ret": day_total})

    daily = pd.DataFrame(rows).set_index("datetime")["ret"]
    print(f"结果文件: {res_json}")
    print(f"seed {seed}  {year} 年  会话 {entry['session_dir']}")
    print(f"归因覆盖 {len(daily)} 天，累计贡献 {daily.sum():.2%}\n")

    ser = pd.Series(contrib).sort_values()
    print("=== 亏损前 12 名 ===")
    print(f"{'标的':>12} {'累计贡献':>10} {'持有天':>8}")
    for name, val in ser.head(12).items():
        print(f"{name:>12} {val:>10.2%} {days[name]:>8d}")
    print("\n=== 盈利前 8 名 ===")
    for name, val in ser.tail(8)[::-1].items():
        print(f"{name:>12} {val:>10.2%} {days[name]:>8d}")

    neg = ser[ser < 0].sum()
    print(f"\n负贡献合计 {neg:.2%}   正贡献合计 {ser[ser > 0].sum():.2%}")
    print(f"最差 3 只合计 {ser.head(3).sum():.2%}（占负贡献 {ser.head(3).sum() / neg:.0%}）")

    print("\n=== 单日跌幅最大的 8 天 ===")
    for ts, r in daily.nsmallest(8).items():
        held = frames[ts]["names"]
        prev_idx = dates[dates.index(ts) - 1]
        worst = []
        for name, rec in frames[prev_idx]["names"].items():
            p1 = held.get(name, {}).get("price")
            if p1 is None or pd.isna(p1):
                continue
            c = float(rec["amount"]) * (float(p1) - float(rec["price"])) / frames[prev_idx]["value"]
            worst.append((c, name))
        worst.sort()
        detail = "  ".join(f"{n}{c:+.1%}" for c, n in worst[:3])
        print(f"  {ts.date()} {r:>7.2%}   {detail}")


if __name__ == "__main__":
    main()
