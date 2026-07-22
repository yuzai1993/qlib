"""
汇总特征消融实验结果，生成 comparison.csv。

读取 backtest/result/feature_exp/{E0..E4}/seed_*/metrics.json，
聚合均值与标准差，输出总对比表。

用法：
  python backtest/scripts/summarize_feature_exp.py
  python backtest/scripts/summarize_feature_exp.py --result-dir backtest/result/feature_exp
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))

DEFAULT_RESULT_DIR = Path(__file__).resolve().parents[1] / "result" / "feature_exp"

# 对比表核心列（均值）
METRIC_KEYS = [
    "IC",
    "ICIR",
    "Rank_IC",
    "Rank_ICIR",
    "excess_with_cost_annualized_return",
    "excess_with_cost_information_ratio",
    "excess_with_cost_max_drawdown",
    "portfolio_cum_return",
    "benchmark_cum_return",
    "excess_cum_return",
    "new_feat_importance_share",
    "new_feat_in_top50",
]


def _load_seed_metrics(exp_dir: Path) -> List[dict]:
    rows = []
    for metrics_path in sorted(exp_dir.glob("seed_*/metrics.json")):
        with open(metrics_path, encoding="utf-8") as f:
            rows.append(json.load(f))
    return rows


def summarize_experiment(result_dir: Path, exp_ids: List[str] | None = None) -> pd.DataFrame:
    """聚合各实验组指标，返回 comparison DataFrame。"""
    if exp_ids is None:
        exp_ids = sorted(
            p.name for p in result_dir.iterdir() if p.is_dir() and p.name.startswith("E")
        )

    records = []
    for exp_id in exp_ids:
        exp_dir = result_dir / exp_id
        if not exp_dir.exists():
            continue
        seed_rows = _load_seed_metrics(exp_dir)
        if not seed_rows:
            continue

        df = pd.DataFrame(seed_rows)
        success = df[df.get("status", "success") == "success"] if "status" in df.columns else df
        if success.empty:
            continue

        row: Dict[str, object] = {
            "exp": exp_id,
            "feature_groups": success.iloc[0].get("feature_groups", ""),
            "n_seeds": int(len(success)),
            "n_failed": int(len(df) - len(success)),
        }

        for key in METRIC_KEYS:
            if key not in success.columns:
                continue
            vals = pd.to_numeric(success[key], errors="coerce").dropna()
            if vals.empty:
                continue
            row[f"{key}_mean"] = float(vals.mean())
            row[f"{key}_std"] = float(vals.std(ddof=0)) if len(vals) > 1 else 0.0

        # 相对 E0 的提升稍后统一补
        records.append(row)

    comparison = pd.DataFrame(records)
    if comparison.empty:
        return comparison

    # 相对基线 E0 的 Rank_IC / 年化超额 / IR 提升
    if "exp" in comparison.columns and (comparison["exp"] == "E0").any():
        base = comparison.loc[comparison["exp"] == "E0"].iloc[0]
        for key in ("Rank_IC", "excess_with_cost_annualized_return", "excess_with_cost_information_ratio"):
            mean_col = f"{key}_mean"
            if mean_col not in comparison.columns:
                continue
            base_val = base.get(mean_col, np.nan)
            comparison[f"delta_{key}"] = comparison[mean_col] - base_val

    return comparison.sort_values("exp").reset_index(drop=True)


def write_comparison(result_dir: Path, exp_ids: List[str] | None = None) -> Path:
    result_dir = Path(result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    comparison = summarize_experiment(result_dir, exp_ids=exp_ids)
    out_path = result_dir / "comparison.csv"
    comparison.to_csv(out_path, index=False)

    # 同步一份简要 JSON，便于阅读
    summary_path = result_dir / "comparison_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(comparison.to_dict(orient="records"), f, ensure_ascii=False, indent=2, default=str)

    return out_path


def main():
    parser = argparse.ArgumentParser(description="汇总特征消融实验结果")
    parser.add_argument("--result-dir", type=Path, default=DEFAULT_RESULT_DIR)
    parser.add_argument("--exps", nargs="+", default=None, help="指定实验组，如 E0 E1 E4")
    args = parser.parse_args()

    comparison = summarize_experiment(args.result_dir, exp_ids=args.exps)
    out_path = write_comparison(args.result_dir, exp_ids=args.exps)
    print(f"已写入: {out_path}")
    if comparison.empty:
        print("未找到任何 seed metrics，请先运行 run_feature_experiment.py")
        sys.exit(1)

    cols = [c for c in comparison.columns if c.endswith("_mean") or c.startswith("delta_") or c in ("exp", "n_seeds")]
    print(comparison[cols].to_string(index=False))


if __name__ == "__main__":
    main()
