from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

from phase_s_protocol import load_frozen_model  # noqa: E402


@pytest.mark.parametrize("model_ref", ["b1-m", "b6-m"])
def test_removed_research_models_do_not_fall_back_to_live_copies(model_ref):
    """Old live exports remain independent and must not replace removed research inputs."""
    live_copy = {
        "b1-m": "live_trading/models/b1_m/csi1000_full_v2_s2000_20260725/trained_model",
        "b6-m": "live_trading/models/b6_m/seed4000/trained_model",
    }[model_ref]
    assert (ROOT / live_copy).is_file()
    assert not (ROOT / "backtest/models/baselines").exists()
    with pytest.raises(FileNotFoundError, match="frozen model manifest missing"):
        load_frozen_model(ROOT, model_ref)
