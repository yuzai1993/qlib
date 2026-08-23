"""统计低价股买入在 Phase S 组合里的笔数占比与收益贡献。

用于判断"设置买入价下限"是不是划算的风控：剔除的笔数要少、剔除掉的贡献要显著为负，
且这个结论要在全期成立而不只在某一年成立（否则是事后拟合）。

用法：
    python backtest/scripts/diag_low_price_exposure.py [结果 JSON]
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
THRESHOLDS = [1.0, 2.0, 3.0, 5.0]


def _buys(session_dir: Path) -> pd.DataFrame:
    link = json.loads((session_dir / "run_01" / "mlruns_link.json").read_text())
    art = ROOT / link["backtest_artifacts"] / "artifacts"
    with (art / "portfolio_analysis" / "positions_normal_1day.pkl").open("rb") as fh:
        pos = pickle.load(fh)

    dates = sorted(pd.Timestamp(d) for d in pos)
    frames = {}
    for d in dates:
        holding = getattr(pos[d], "position", pos[d])
        frames[d] = {
            "value": holding.get("now_account_value"),
            "names": {k: v for k, v in holding.items() if k not in CASH_KEYS},
        }

    entry: dict[str, tuple[pd.Timestamp, float]] = {}
    contrib: dict[str, float] = {}
    for prev, cur in zip(dates, dates[1:]):
        for name, rec in frames[cur]["names"].items():
            if name not in frames[prev]["names"]:
                entry.setdefault(name, (cur, float(rec["price"])))
        v0 = frames[prev]["value"]
        if not v0 or pd.isna(v0):
            continue
        for name, rec in frames[prev]["names"].items():
            p1 = frames[cur]["names"].get(name, {}).get("price")
            if p1 is None or pd.isna(p1):
                continue
            contrib[name] = contrib.get(name, 0.0) + float(rec["amount"]) * (
                float(p1) - float(rec["price"])
            ) / v0

    return pd.DataFrame(
        [
            {"name": n, "buy_date": entry[n][0], "buy_price": entry[n][1], "contrib": c}
            for n, c in contrib.items()
            if n in entry
        ]
    )


def main() -> None:
    res_json = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JSON
    doc = json.loads(res_json.read_text())

    parts = []
    for seed, entry in doc["seeds"].items():
        df = _buys(ROOT / entry["session_dir"])
        df["seed"] = seed
        parts.append(df)
    all_buys = pd.concat(parts, ignore_index=True)
    all_buys["year"] = all_buys["buy_date"].dt.year

    print(f"结果文件: {res_json}")
    print(f"五种子合计买入 {len(all_buys)} 笔\n")

    print("=== 全期：按买入价下限剔除的代价与收益 ===")
    print(f"{'下限(元)':>9}{'剔除笔数':>10}{'占比':>9}{'剔除掉的贡献':>15}{'保留笔均贡献':>15}")
    for thr in THRESHOLDS:
        cut = all_buys[all_buys.buy_price < thr]
        keep = all_buys[all_buys.buy_price >= thr]
        print(
            f"{thr:>9.1f}{len(cut):>10d}{len(cut) / len(all_buys):>9.1%}"
            f"{cut.contrib.sum():>15.1%}{keep.contrib.mean():>15.3%}"
        )

    print("\n=== 逐年：买入价 < 3 元 的笔数占比 与 其贡献 ===")
    print(f"{'年':>6}{'低价笔数':>10}{'占比':>9}{'低价贡献':>12}{'其余贡献':>12}")
    for year, grp in all_buys.groupby("year"):
        cut = grp[grp.buy_price < 3.0]
        keep = grp[grp.buy_price >= 3.0]
        print(
            f"{year:>6}{len(cut):>10d}{len(cut) / len(grp):>9.1%}"
            f"{cut.contrib.sum():>12.1%}{keep.contrib.sum():>12.1%}"
        )


if __name__ == "__main__":
    main()
