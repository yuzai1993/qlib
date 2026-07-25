from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = ROOT / "backtest" / "configs"
SEEDS = (42, 1000, 2000, 3000, 4000)
VARIANTS = {
    "bollinger": ["bollinger"],
    "momentum": ["momentum"],
    "trend": ["trend"],
    "combined": ["bollinger", "momentum", "trend"],
}


def _read(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _without_allowed_changes(config: dict) -> dict:
    frozen = deepcopy(config)
    frozen["run"].pop("note")
    handler = frozen["data"]["handler"]
    handler.pop("class")
    handler.pop("module_path")
    handler.pop("feature_groups", None)
    return frozen


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", SEEDS)
def test_technical_config_changes_only_handler_and_run_note(variant, seed):
    candidate_path = (
        CONFIG_ROOT
        / "feature-technical"
        / variant
        / f"ft_{variant}_lgbm_s{seed}.yaml"
    )
    baseline_path = (
        CONFIG_ROOT
        / "train-data"
        / "csi1000-full-v2"
        / f"td_csi1000_full_v2_lgbm_s{seed}.yaml"
    )

    candidate = _read(candidate_path)
    baseline = _read(baseline_path)

    assert candidate["run"]["note"] == f"ft_{variant}_lgbm_s{seed}"
    assert candidate["data"]["handler"]["class"] == "Alpha158Technical"
    assert candidate["data"]["handler"]["module_path"] == "backtest.features.technical"
    assert candidate["data"]["handler"]["feature_groups"] == VARIANTS[variant]
    assert _without_allowed_changes(candidate) == _without_allowed_changes(baseline)
