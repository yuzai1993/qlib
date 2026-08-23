"""汇总 hold5 / f100 2×2 消融，对照 BT v2（top5d1）与 BT v3（h5f100）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.scripts.strategy_stability_metrics import summarize_stability  # noqa: E402

OUT_ROOT = ROOT / "backtest" / "result" / "phase_s_regime"
KNOWN = {
    "top5d1": OUT_ROOT / "all_top5d1" / "m0h20es.json",
    "h5f100": OUT_ROOT / "all_top5d1h5f100" / "m0h20es.json",
}
YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]


def extras(run_dir: Path) -> dict:
    out: dict = {
        "session": run_dir.name,
        "session_dir": str(run_dir.relative_to(ROOT)),
    }
    manifest = run_dir / "run_01" / "figures_manifest.json"
    if manifest.exists():
        out["figures"] = json.loads(manifest.read_text())
    metrics = run_dir / "run_01" / "metrics.json"
    if metrics.exists():
        out["run_metrics"] = json.loads(metrics.read_text())
    stats = run_dir / "run_01" / "universe_filter_stats.json"
    if stats.exists():
        out["universe_filter"] = json.loads(stats.read_text())
    return out


def load_ensemble(path: Path) -> dict:
    return json.loads(path.read_text())["ensemble"]


def collect_run(name: str, session: str) -> dict:
    run_dir = ROOT / "backtest" / "result" / session
    csv = run_dir / "run_01" / "report_normal.csv"
    if not csv.is_file():
        raise FileNotFoundError(csv)
    summary = summarize_stability(pd.read_csv(csv, index_col=0, parse_dates=True))
    summary.update(extras(run_dir))
    dest = OUT_ROOT / f"all_{name}"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "m0h20es.json").write_text(
        json.dumps({"ensemble": summary}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return summary


def pct(x) -> str:
    return f"{x * 100:+7.1f}%" if x is not None else "      —"


def main() -> None:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--h5-session", required=True)
    p.add_argument("--f100-session", required=True)
    args = p.parse_args()

    cells = {
        "top5d1": load_ensemble(KNOWN["top5d1"]),
        "hold5": collect_run("top5d1h5", args.h5_session),
        "f100": collect_run("top5d1f100", args.f100_session),
        "h5f100": load_ensemble(KNOWN["h5f100"]),
    }

    print("全期（累乘年化 / 算术年化 / 夏普 / 回撤 / 换手）")
    for name, rec in cells.items():
        fp = rec["full_period"]
        print(
            f"  {name:<8} CAGR={pct(fp['annualized_return'])}"
            f"  算术={pct(fp['annualized_return_arith'])}"
            f"  夏普={fp['sharpe_ratio']:.2f}"
            f"  回撤={pct(fp['max_drawdown'])}"
            f"  换手={fp['annualized_one_way_turnover']:.1f}"
        )

    print("\n分年算术年化")
    header = f"{'年':<6}" + "".join(f"{k:>10}" for k in cells)
    print(header)
    for year in YEARS:
        row = f"{year:<6}"
        for rec in cells.values():
            y = (rec.get("years") or {}).get(year) or {}
            row += f"{pct(y.get('annualized_return_arith')):>10}"
        print(row)

    v2 = cells["top5d1"]["full_period"]["annualized_return"]
    combo = cells["h5f100"]["full_period"]["annualized_return"]
    h5 = cells["hold5"]["full_period"]["annualized_return"]
    f100 = cells["f100"]["full_period"]["annualized_return"]
    print(
        "\n相对 top5d1 的 CAGR 增量："
        f" hold5 {pct(h5 - v2)}  f100 {pct(f100 - v2)}  组合 {pct(combo - v2)}"
        f"  交互 {pct(combo - v2 - (h5 - v2) - (f100 - v2))}"
    )


if __name__ == "__main__":
    main()
