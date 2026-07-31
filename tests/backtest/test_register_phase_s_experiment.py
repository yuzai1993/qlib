from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import phase_s_protocol as protocol  # noqa: E402
import register_phase_s_experiment as register  # noqa: E402


def _prediction_manifest(model_ref: str) -> dict:
    return {
        "data_version": "2026-07-31",
        "predictions": [
            {
                "model_ref": model_ref,
                "pool": pool,
                "segment": segment,
                "path": f"predictions/{model_ref}/{pool}_{segment}.pkl",
                "prediction_sha256": f"{pool}-{segment}-sha",
                "coverage": {
                    "start": protocol.VALID_SEGMENT[0] if segment == "valid" else protocol.TEST_SEGMENT[0],
                    "end": protocol.VALID_SEGMENT[1] if segment == "valid" else protocol.TEST_SEGMENT[1],
                    "n_dates": 300,
                    "n_rows": 1000,
                },
            }
            for pool in protocol.POOL_BENCHMARKS
            for segment in ("valid", "test")
        ],
    }


def test_preregistered_row_freezes_grid_selection_account_and_predictions():
    frozen = protocol.load_frozen_model(ROOT, "b1-m")

    row = register.build_preregistered_row(
        frozen,
        _prediction_manifest("b1-m"),
        protocol_path="backtest/experiments/strategy/protocol.json",
    )

    assert row["exp_id"] == "strategy-sweep/b1-m"
    assert row["phase"] == "S"
    assert row["state"] == "preregistered"
    assert row["baseline_ref"] == "B1-S v1.0"
    assert row["frozen_model_ref"] == "B1 v1.0"
    assert len(row["strategy_grid"]) == 18
    assert row["selection_segment"] == ["2020-01-13", "2021-07-15"]
    assert row["selection_rule"][-1] == "candidate_id asc"
    assert row["account"] == 500000
    assert row["fees"]["open_cost"] == 0.00021
    assert len(row["prediction_artifacts"]) == 6


def test_registry_upsert_preserves_unrelated_rows_and_prevents_duplicates(tmp_path):
    registry = tmp_path / "registry.jsonl"
    registry.write_text(json.dumps({"exp_id": "baseline/b6-m"}) + "\n", encoding="utf-8")
    frozen = protocol.load_frozen_model(ROOT, "b6-m")
    row = register.build_preregistered_row(
        frozen,
        _prediction_manifest("b6-m"),
        protocol_path="protocol.json",
    )

    register.upsert_registry_row(registry, row, expected_previous_state=None)
    register.upsert_registry_row(
        registry,
        {**row, "state": "valid_complete"},
        expected_previous_state="preregistered",
    )

    rows = [json.loads(line) for line in registry.read_text(encoding="utf-8").splitlines()]
    assert [item["exp_id"] for item in rows] == ["baseline/b6-m", "strategy-sweep/b6-m"]
    assert rows[-1]["state"] == "valid_complete"


def test_registry_rejects_non_monotonic_state_transition(tmp_path):
    registry = tmp_path / "registry.jsonl"
    registry.write_text(
        json.dumps({"exp_id": "strategy-sweep/b1-m", "state": "valid_complete"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected previous state"):
        register.upsert_registry_row(
            registry,
            {"exp_id": "strategy-sweep/b1-m", "state": "test_complete"},
            expected_previous_state="preregistered",
        )


def test_bind_valid_results_rejects_candidate_set_changed_after_preregistration(tmp_path):
    frozen = protocol.load_frozen_model(ROOT, "b1-m")
    row = register.build_preregistered_row(frozen, _prediction_manifest("b1-m"), protocol_path="protocol.json")
    result = {
        "model_ref": "b1-m",
        "pool": "csi1000",
        "segment": "valid",
        "all_rows": [
            {
                "candidate_id": candidate["candidate_id"],
                "status": "failed",
            }
            for candidate in row["strategy_grid"][:-1]
        ],
    }
    path = tmp_path / "valid.json"
    path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(ValueError, match="candidate set"):
        register.bind_valid_results(row, path)
