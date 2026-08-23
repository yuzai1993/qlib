"""汇总 top25/d5/h5 阶梯回测，与评估主格 k5h5 及 top5 系列对齐比较。

阶梯动机：评估口径 `ann = mean(p) × 238 / h`（eval_ic_multi_pool.py）等价于
"k·h 个等额仓位、每日 k 进 k 出、每只持满 h 天"的算术年化。k=5、h=5 → 25 槽。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.scripts.strategy_stability_metrics import summarize_stability  # noqa: E402

RUNS = {
    "top25d5h5": "20260821_234251_phase_s_m0h20es_all_top25d5h5_ensemble",
    "top25d5h5f100": "20260821_234327_phase_s_m0h20es_all_top25d5h5f100_ensemble",
    "ladder_k5h5": "20260822_001249_phase_s_m0h20es_all_ladder_k5h5_ensemble",
}
YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]


def load_report(run_dir: Path) -> pd.DataFrame:
    csv = run_dir / "run_01" / "report_normal.csv"
    return pd.read_csv(csv, index_col=0, parse_dates=True)


def load_extras(run_dir: Path) -> dict:
    """报告生成器要 session_dir / figures / run_metrics，这里补齐。"""
    extras: dict = {
        "session": run_dir.name,
        "session_dir": str(run_dir.relative_to(ROOT)),
    }
    manifest = run_dir / "run_01" / "figures_manifest.json"
    if manifest.exists():
        extras["figures"] = json.loads(manifest.read_text())
    metrics = run_dir / "run_01" / "metrics.json"
    if metrics.exists():
        extras["run_metrics"] = json.loads(metrics.read_text())
    stats = run_dir / "run_01" / "universe_filter_stats.json"
    if stats.exists():
        extras["universe_filter"] = json.loads(stats.read_text())
    return extras


def main() -> None:
    out_root = ROOT / "backtest" / "result" / "phase_s_regime"
    summaries: dict[str, dict] = {}
    for name, run in RUNS.items():
        run_dir = ROOT / "backtest" / "result" / run
        summary = summarize_stability(load_report(run_dir))
        summary.update(load_extras(run_dir))
        dest = out_root / f"all_{name}"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "m0h20es.json").write_text(
            json.dumps({"ensemble": summary}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        summaries[name] = summary

    ev = json.loads(
        (ROOT / "backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json").read_text()
    )["pools"]["all"]["ensemble"]
    top5d1 = json.loads(
        (out_root / "all_top5d1" / "m0h20es.json").read_text()
    )["ensemble"]
    h5f100 = json.loads(
        (out_root / "all_top5d1h5f100" / "m0h20es.json").read_text()
    )["ensemble"]

    def pct(x):
        return f"{x * 100:+7.1f}%" if x is not None else "      —"

    columns = [
        ("top5d1", top5d1),
        ("h5f100", h5f100),
        ("t25d5h5", summaries["top25d5h5"]),
        ("t25f100", summaries["top25d5h5f100"]),
        ("真阶梯k5h5", summaries["ladder_k5h5"]),
    ]

    def eval_year(year: str):
        grid = ((ev.get("head_years") or {}).get(year, {}).get("5") or {}).get("5") or {}
        return grid.get("net_ann")

    print("算术年化（与评估 ×238/h 同口径）")
    header = f"{'年':<6}{'评估k5h5净':>12}" + "".join(f"{name:>12}" for name, _ in columns)
    print(header)
    for y in YEARS:
        row = [eval_year(y)] + [
            (s["years"].get(y) or {}).get("annualized_return_arith") for _, s in columns
        ]
        print(f"{y:<6}" + "".join(f"{pct(v):>12}" for v in row))
    print(f"{'全期':<6}" + "".join(
        f"{pct(v):>12}"
        for v in [ev["head"]["5"]["5"]["net_ann"]]
        + [s["full_period"]["annualized_return_arith"] for _, s in columns]
    ))

    print("\n绝对指标（累乘年化 / 夏普 / 最大回撤）")
    for name, s in columns:
        fp = s["full_period"]
        print(
            f"{name:<12}{pct(fp['annualized_return'])}"
            f"{fp['sharpe_ratio']:>8.2f}{pct(fp['max_drawdown'])}"
        )

    print("\n与评估主格的分年偏差 |BT − eval|")
    for name, s in columns:
        diffs = [
            abs(b - e)
            for y in YEARS
            if (e := eval_year(y)) is not None
            and (b := (s["years"].get(y) or {}).get("annualized_return_arith")) is not None
        ]
        print(f"{name:<12}均值 {sum(diffs) / len(diffs) * 100:5.1f}pp   "
              f"最大 {max(diffs) * 100:5.1f}pp")


if __name__ == "__main__":
    main()
