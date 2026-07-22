import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest" / "scripts"))

import run_backtest as rb  # noqa: E402


def test_failed_runs_produce_nonzero_exit_code():
    assert rb.exit_code_for_summary({"success_runs": 0}, expected_runs=1) == 1


def test_all_successful_runs_produce_zero_exit_code():
    assert rb.exit_code_for_summary({"success_runs": 2}, expected_runs=2) == 0
