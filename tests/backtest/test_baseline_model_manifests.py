from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
B6_MANIFEST = ROOT / "backtest/models/baselines/b6-m/manifest.json"
REGISTRY = ROOT / "backtest/experiments/registry.jsonl"
STANDARD = ROOT / "backtest/EXPERIMENT_STANDARD.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_b6_manifest_is_single_model_phase_s_contract_selected_on_valid():
    manifest = json.loads(B6_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["baseline_exp_id"] == "baseline/b6-m"
    assert manifest["baseline_ref"] == "B6 v1.0"
    assert manifest["retention_scope"] == "phase_s_single_model"
    assert manifest["selection"] == {
        "metric": "valid.csi1000.rank_ic_mean",
        "direction": "max",
        "selected_seed": 4000,
        "selected_value": 0.049698109941383316,
        "selection_segment": ["2020-01-13", "2021-07-15"],
        "result": "backtest/experiments/ic/mh_rankic_es_lr010_valid_1d.json",
    }

    config = manifest["config"]
    config_path = ROOT / config["path"]
    assert config_path.is_file()
    assert _sha256(config_path) == config["sha256"]

    model = manifest["model"]
    model_path = ROOT / model["path"]
    assert model_path.is_file()
    assert model_path.stat().st_size == model["size_bytes"]
    assert _sha256(model_path) == model["sha256"]


def test_phase_s_references_canonical_single_model_manifest_only():
    sys.path.insert(0, str(ROOT / "backtest/scripts"))
    from phase_s_protocol import load_frozen_model

    rows = [json.loads(line) for line in REGISTRY.read_text(encoding="utf-8").splitlines()]
    b6 = next(row for row in rows if row["exp_id"] == "baseline/b6-m")

    assert b6["model_manifest"] == "backtest/models/baselines/b6-m/manifest.json"
    assert "freeze_manifest" not in b6
    assert "backtest/experiments/b6_model_freeze.json" not in json.dumps(b6)
    for model_ref in ("b1-m", "b6-m"):
        frozen = load_frozen_model(ROOT, model_ref)
        expected_dir = (ROOT / "backtest/models/baselines" / model_ref).resolve()
        assert frozen.model_path.is_relative_to(expected_dir)
