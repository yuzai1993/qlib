import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

from combine_phase_s_test_results import combine_payloads  # noqa: E402


def test_combine_payloads_builds_complete_pool_matrix():
    payloads = [
        {"model_ref": "b1-m", "pool": pool, "segment": "test", "all_rows": []}
        for pool in ("csi1000", "csi300", "csi500")
    ]

    combined = combine_payloads("b1-m", payloads)

    assert combined["model_ref"] == "b1-m"
    assert list(combined["pools"]) == ["csi1000", "csi300", "csi500"]


def test_combine_payloads_rejects_missing_pool():
    payloads = [
        {"model_ref": "b1-m", "pool": "csi1000", "segment": "test"},
        {"model_ref": "b1-m", "pool": "csi300", "segment": "test"},
    ]

    with pytest.raises(ValueError, match="pool matrix"):
        combine_payloads("b1-m", payloads)
