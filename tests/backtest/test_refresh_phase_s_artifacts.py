import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import refresh_phase_s_artifacts as refresh  # noqa: E402


def test_normalize_result_row_removes_wrong_yearly_ir_and_nulls_non_finite_metrics():
    row = {
        "status": "success",
        "yearly_ir": {"2025": 9.9},
        "excess_with_cost_information_ratio": math.nan,
        "excess_with_cost_annualized_return": 0.1,
        "excess_with_cost_max_drawdown": -0.2,
        "annualized_one_way_turnover": 3.0,
    }

    normalized = refresh.normalize_result_row(row, yearly=None)

    assert normalized["status"] == "invalid"
    assert "yearly_ir" not in normalized
    assert normalized["excess_with_cost_information_ratio"] is None


def test_refresh_all_rebuilds_only_current_b2_anchor_without_b1_files(
    tmp_path, monkeypatch
):
    registry = tmp_path / "backtest/experiments/registry.jsonl"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        "\n".join(
            json.dumps(row)
                for row in (
                    {"exp_id": "historical/keep"},
                    {"exp_id": "strategy-sweep/b6-m", "phase": "S"},
                    {
                        "exp_id": "baseline/b2-s-on-b6-m",
                        "direction": "baseline-strategy",
                        "state": "baseline",
                        "baseline_ref": "B2-S v1.0",
                        "date": "2026-08-01",
                    },
                )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(refresh, "refresh_prediction_manifest", lambda _path: {})
    monkeypatch.setattr(
        refresh,
        "build_strategy_baseline_promotion",
        lambda _row, baseline_ref, promotion_date: {
            "exp_id": "baseline/b2-s-on-b6-m",
            "direction": "baseline-strategy",
            "state": "baseline",
            "baseline_ref": baseline_ref,
            "date": promotion_date or "2026-08-01",
        },
    )

    refresh.refresh_all(tmp_path, registry)

    rows = refresh.load_registry(registry)
    assert [row["exp_id"] for row in rows] == [
        "historical/keep",
        "strategy-sweep/b6-m",
        "baseline/b2-s-on-b6-m",
    ]
    assert rows[-1]["baseline_ref"] == "B2-S v1.0"
    assert rows[-1]["date"] == "2026-08-01"
