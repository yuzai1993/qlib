"""把 regime-adapt 各臂的 Phase S（B4-S 实盘策略）回测结果写入 registry.jsonl。

Phase S 判据按 EXPERIMENT_STANDARD：看扣费绝对收益的夏普/年化/回撤，以及
Alpha/Beta/基准涨幅。逐年 alpha/beta 一并落盘（`years` 字段）。
幂等：已登记的 exp_id 跳过。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Optional

import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy_stability_metrics import summarize_period  # noqa: E402

RES_DIR = EXP_ROOT / "backtest" / "result" / "phase_s_regime"
REG = EXP_ROOT / "backtest" / "experiments" / "registry.jsonl"

# 2024-09 断点：用户观察到 alpha 从此消失；整年平均会稀释掉它
SUB_PERIODS = {
    "pre_202409": ("2020-08-03", "2024-08-31"),
    "post_202409": ("2024-09-01", "2026-07-31"),
}

# (subdir or "", arm) -> (exp_id, baseline_ref, note)
# 空 subdir = 上一轮 CSI1000 B4-S（已登记则跳过）
ARMS = {
    ("", "m0h5"): (
        "regime-adapt/m0-h5-phase-s",
        "regime-adapt/b6m-phase-s",
        "M0 H5 + B4-S · CSI1000（上一轮）",
    ),
    ("", "m3h5"): (
        "regime-adapt/m3-h5-phase-s",
        "regime-adapt/m0-h5-phase-s",
        "M3 H5 + B4-S · CSI1000（上一轮）",
    ),
    ("", "m0h10"): ("regime-adapt/m0-h10-phase-s", "regime-adapt/m0-h5-phase-s", "M0 H10 + B4-S · CSI1000"),
    ("", "m0h40"): ("regime-adapt/m0-h40-phase-s", "regime-adapt/b6m-phase-s", "M0 H40 + B4-S · CSI1000"),
    ("", "b6m"): ("regime-adapt/b6m-phase-s", "self", "B6-M + B4-S · CSI1000"),
    ("all_b4s", "m0h5"): (
        "regime-adapt/m0-h5-phase-s-all-b4s",
        "regime-adapt/b6m-phase-s-all-b4s",
        "M0 H5 + B4-S · 全A",
    ),
    ("all_b4s", "m3h5"): (
        "regime-adapt/m3-h5-phase-s-all-b4s",
        "regime-adapt/m0-h5-phase-s-all-b4s",
        "M3 H5 + B4-S · 全A",
    ),
    ("all_b4s", "m0h10"): (
        "regime-adapt/m0-h10-phase-s-all-b4s",
        "regime-adapt/m0-h5-phase-s-all-b4s",
        "M0 H10 + B4-S · 全A",
    ),
    ("all_b4s", "m0h40"): (
        "regime-adapt/m0-h40-phase-s-all-b4s",
        "regime-adapt/b6m-phase-s-all-b4s",
        "M0 H40 + B4-S · 全A",
    ),
    ("all_b4s", "b6m"): (
        "regime-adapt/b6m-phase-s-all-b4s",
        "self",
        "B6-M + B4-S · 全A（模型在 csi1000 训练，推理全A）",
    ),
    ("all_daily_topk", "m0h5"): (
        "regime-adapt/m0-h5-phase-s-all-daily-topk",
        "regime-adapt/m0-h5-phase-s-all-b4s",
        "M0 H5 + 每日Topk · 全A",
    ),
    ("all_daily_topk", "m3h5"): (
        "regime-adapt/m3-h5-phase-s-all-daily-topk",
        "regime-adapt/m3-h5-phase-s-all-b4s",
        "M3 H5 + 每日Topk · 全A",
    ),
    ("all_daily_topk", "m0h10"): (
        "regime-adapt/m0-h10-phase-s-all-daily-topk",
        "regime-adapt/m0-h10-phase-s-all-b4s",
        "M0 H10 + 每日Topk · 全A",
    ),
    ("all_daily_topk", "m0h40"): (
        "regime-adapt/m0-h40-phase-s-all-daily-topk",
        "regime-adapt/m0-h40-phase-s-all-b4s",
        "M0 H40 + 每日Topk · 全A",
    ),
    ("all_daily_topk", "b6m"): (
        "regime-adapt/b6m-phase-s-all-daily-topk",
        "regime-adapt/b6m-phase-s-all-b4s",
        "B6-M + 每日Topk · 全A",
    ),
}

FULL_KEYS = (
    "annualized_return",
    "sharpe_ratio",
    "alpha",
    "beta",
    "max_drawdown",
    "calmar_ratio",
    "annualized_volatility",
    "annualized_one_way_turnover",
    "cumulative_return",
    "benchmark_annualized_return",
    "benchmark_cumulative_return",
    "n_days",
)


def agg(recs: list[dict], block: str, key: str) -> dict[str, Optional[float]]:
    vals = [(r.get(block) or {}).get(key) for r in recs]
    vals = [float(v) for v in vals if v is not None]
    if not vals:
        return {"mean": None, "std": None}
    return {
        "mean": mean(vals),
        "std": (stdev(vals) if len(vals) > 1 else 0.0),
    }


def sub_period_metrics(recs: list[dict]) -> dict[str, dict]:
    """从各 session 的 report_normal.csv 现算前后段指标。"""
    per_seg: dict[str, list[dict]] = {name: [] for name in SUB_PERIODS}
    for rec in recs:
        csv = EXP_ROOT / rec["session_dir"] / "run_01" / "report_normal.csv"
        if not csv.is_file():
            continue
        df = pd.read_csv(csv, index_col=0, parse_dates=True)
        for name, (start, end) in SUB_PERIODS.items():
            seg = df.loc[start:end]
            if len(seg) > 20:
                per_seg[name].append({"seg": summarize_period(seg)})
    keys = (
        "annualized_return",
        "sharpe_ratio",
        "alpha",
        "beta",
        "max_drawdown",
        "calmar_ratio",
        "benchmark_annualized_return",
        "n_days",
    )
    return {
        name: {k: agg(rows, "seg", k) for k in keys}
        for name, rows in per_seg.items()
        if rows
    }


def existing_exp_ids() -> set[str]:
    if not REG.exists():
        return set()
    out = set()
    for line in REG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.add(json.loads(line).get("exp_id"))
    return out


def main() -> None:
    already = existing_exp_ids()
    rows: list[dict[str, Any]] = []
    for (subdir, arm), (exp_id, baseline_ref, note) in ARMS.items():
        path = RES_DIR / subdir / f"{arm}.json" if subdir else RES_DIR / f"{arm}.json"
        rel = f"backtest/result/phase_s_regime/{subdir}/{arm}.json" if subdir else f"backtest/result/phase_s_regime/{arm}.json"
        if not path.exists():
            print("skip missing", rel)
            continue
        if exp_id in already:
            print("skip already registered", exp_id)
            continue
        doc = json.loads(path.read_text())
        seeds = doc.get("seeds") or {}
        if not seeds:
            print("skip empty", arm)
            continue
        recs = list(seeds.values())
        years = sorted({y for r in recs for y in (r.get("years") or {})})
        pool = doc.get("pool") or ("csi1000" if not subdir else subdir.split("_")[0])
        rows.append(
            {
                "exp_id": exp_id,
                "direction": "regime-adapt",
                "phase": "S",
                "date": date.today().isoformat(),
                "state": "completed",
                "arm": arm,
                "pool": pool,
                "strategy_id": doc.get("strategy_id"),
                "seeds": sorted(int(s) for s in seeds),
                "strategy": doc.get("strategy"),
                "backtest_window": doc.get("backtest_window"),
                "fees": doc.get("fees"),
                "eval_protocol": (
                    f"{doc.get('strategy')} | pool={pool} | "
                    f"benchmark={doc.get('benchmark', 'SH000852')} | 扣费绝对收益 | "
                    "CAPM alpha/beta（rf=0, 250 交易日）| 逐年 + 2024-09 断点前后分段回归 | "
                    "判据段 post_202409"
                ),
                "metrics": {
                    "full_period": {k: agg(recs, "full_period", k) for k in FULL_KEYS},
                    **sub_period_metrics(recs),
                    "years": {
                        y: {
                            k: agg(
                                [{"y": (r.get("years") or {}).get(y, {})} for r in recs],
                                "y",
                                k,
                            )
                            for k in ("annualized_return", "sharpe_ratio", "alpha", "beta",
                                      "max_drawdown", "benchmark_annualized_return")
                        }
                        for y in years
                    },
                },
                "note": note
                + "；回测窗自 2020-08-03 起（regime 臂训练集截至 2020-07-31）",
                "baseline_ref": baseline_ref,
                "eval_output": rel,
                "report": "backtest/experiments/regime_adapt_phase_s_report.html",
                "plan": "backtest/experiments/plans/20260809_regime_adaptation_plan.md",
            }
        )
    if rows:
        with REG.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("appended", len(rows), "phase-s registry rows")


if __name__ == "__main__":
    main()
