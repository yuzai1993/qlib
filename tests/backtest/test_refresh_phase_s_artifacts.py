import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

from refresh_phase_s_artifacts import normalize_result_row  # noqa: E402


def test_normalize_result_row_removes_wrong_yearly_ir_and_nulls_non_finite_metrics():
    row = {
        "status": "success",
        "yearly_ir": {"2025": 9.9},
        "excess_with_cost_information_ratio": math.nan,
        "excess_with_cost_annualized_return": 0.1,
        "excess_with_cost_max_drawdown": -0.2,
        "annualized_one_way_turnover": 3.0,
    }

    normalized = normalize_result_row(row, yearly=None)

    assert normalized["status"] == "invalid"
    assert "yearly_ir" not in normalized
    assert normalized["excess_with_cost_information_ratio"] is None
