"""summarize_feature_exp 汇总逻辑单测。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

QLIB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QLIB_ROOT))

from backtest.scripts.summarize_feature_exp import summarize_experiment, write_comparison


def _write_metrics(root: Path, exp: str, seed: int, **metrics):
    d = root / exp / f"seed_{seed:02d}"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "exp": exp,
        "seed": seed,
        "feature_groups": "mom" if exp != "E0" else "baseline",
        "status": "success",
        **metrics,
    }
    with open(d / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_summarize_computes_mean_std_and_delta(tmp_path: Path):
    _write_metrics(
        tmp_path,
        "E0",
        0,
        Rank_IC=0.02,
        excess_with_cost_annualized_return=0.10,
        excess_with_cost_information_ratio=0.5,
        new_feat_importance_share=0.0,
        new_feat_in_top50=0,
    )
    _write_metrics(
        tmp_path,
        "E0",
        1,
        Rank_IC=0.04,
        excess_with_cost_annualized_return=0.12,
        excess_with_cost_information_ratio=0.6,
        new_feat_importance_share=0.0,
        new_feat_in_top50=0,
    )
    _write_metrics(
        tmp_path,
        "E1",
        0,
        Rank_IC=0.05,
        excess_with_cost_annualized_return=0.15,
        excess_with_cost_information_ratio=0.8,
        new_feat_importance_share=0.1,
        new_feat_in_top50=3,
    )

    comparison = summarize_experiment(tmp_path, exp_ids=["E0", "E1"])
    assert set(comparison["exp"]) == {"E0", "E1"}

    e0 = comparison.loc[comparison["exp"] == "E0"].iloc[0]
    e1 = comparison.loc[comparison["exp"] == "E1"].iloc[0]
    assert abs(e0["Rank_IC_mean"] - 0.03) < 1e-9
    assert abs(e1["delta_Rank_IC"] - (0.05 - 0.03)) < 1e-9

    out = write_comparison(tmp_path, exp_ids=["E0", "E1"])
    assert out.exists()
    assert (tmp_path / "comparison_summary.json").exists()
