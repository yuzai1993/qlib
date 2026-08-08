from __future__ import annotations

import sys
from pathlib import Path
import math

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_strategy_report as report  # noqa: E402


def _row(model_ref: str) -> dict:
    winner = "topk-t20-d2-h10"
    baseline = "topk-t10-d2-h1"
    valid_rows = [
        {
            "candidate_id": baseline,
            "status": "success",
            "excess_with_cost_information_ratio": 0.8,
            "excess_with_cost_annualized_return": 0.10,
            "excess_with_cost_max_drawdown": -0.20,
            "annualized_one_way_turnover": 20.0,
        },
        {
            "candidate_id": winner,
            "status": "success",
            "excess_with_cost_information_ratio": 1.0,
            "excess_with_cost_annualized_return": 0.12,
            "excess_with_cost_max_drawdown": -0.18,
            "annualized_one_way_turnover": 10.0,
        },
    ]
    test_results = {
        pool: [
            {
                "candidate_id": baseline,
                "excess_with_cost_information_ratio": 0.7,
                "excess_with_cost_annualized_return": 0.09,
                "excess_with_cost_max_drawdown": -0.22,
                "yearly_ir": {"2025": 0.6},
            },
            {
                "candidate_id": winner,
                "excess_with_cost_information_ratio": 0.9,
                "excess_with_cost_annualized_return": 0.11,
                "excess_with_cost_max_drawdown": -0.19,
                "yearly_ir": {"2025": 0.8},
            },
        ]
        for pool in ("csi1000", "csi300", "csi500")
    }
    return {
        "exp_id": f"strategy-sweep/{model_ref}",
        "phase": "S",
        "model_ref": model_ref,
        "state": "test_complete",
        "model_path": f"backtest/models/baselines/{model_ref}/trained_model",
        "model_sha256": f"{model_ref}-sha",
        "account": 500000,
        "selection_segment": ["2020-01-13", "2021-07-15"],
        "test_segment": ["2021-07-16", "2026-07-31"],
        "selected_candidate_id": winner,
        "valid_results": valid_rows,
        "test_results": test_results,
    }


def test_strategy_report_renders_two_models_baseline_first_and_frozen_winner():
    html = report.build_html([_row("b1-m"), _row("b6-m")])

    assert "B1-M" in html and "B6-M" in html
    for model_ref in ("b1-m", "b6-m"):
        section = html.split(f'id="{model_ref}"', 1)[1].split("</section>", 1)[0]
        valid_table = section.split("CSI1000 valid 全候选", 1)[1].split(
            "冻结胜者与 B1-S 基线 test 对比", 1
        )[0]
        assert valid_table.index("topk-t10-d2-h1") < valid_table.index("topk-t20-d2-h10")
        assert "valid-winner" in section
        assert f"{model_ref}-sha" in section
        assert all(pool.upper() in section for pool in ("csi1000", "csi300", "csi500"))
        assert "2025" in section
    assert "test 不参与选型" in html
    assert "500,000" in html


def test_strategy_report_labels_non_finite_and_shows_retry_audit():
    row = _row("b1-m")
    row["valid_results"][0]["previous_attempts"] = [{"status": "failed"}]
    row["valid_results"].append(
        {
            "candidate_id": "soft-invalid",
            "status": "invalid",
            "error": "non-finite strategy metrics",
            "excess_with_cost_information_ratio": math.nan,
        }
    )

    html = report.build_html([row])

    assert "无效" in html
    assert "工程失败后重跑" in html
    assert ">nan<" not in html


def test_legacy_strategy_report_cli_has_only_unified_output_target():
    args = report.parse_args([])

    assert not hasattr(args, "output")
    assert report.UNIFIED_REPORT.name == "strategy_stability_report.html"
