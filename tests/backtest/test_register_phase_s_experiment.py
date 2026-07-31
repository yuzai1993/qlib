from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import phase_s_protocol as protocol  # noqa: E402
import register_phase_s_experiment as register  # noqa: E402


def _prediction_manifest(model_ref: str, tmp_path: Path) -> dict:
    entries = []
    for pool in protocol.POOL_BENCHMARKS:
        for segment in ("valid", "test"):
            path = tmp_path / f"{model_ref}-{pool}-{segment}.pkl"
            payload = f"{model_ref}-{pool}-{segment}".encode()
            path.write_bytes(payload)
            entries.append(
                {
                    "model_ref": model_ref,
                    "pool": pool,
                    "segment": segment,
                    "path": str(path),
                    "prediction_sha256": hashlib.sha256(payload).hexdigest(),
                    "coverage": {
                        "start": protocol.VALID_SEGMENT[0] if segment == "valid" else protocol.TEST_SEGMENT[0],
                        "end": protocol.VALID_SEGMENT[1] if segment == "valid" else protocol.TEST_SEGMENT[1],
                        "n_dates": 300,
                        "n_rows": 1000,
                        "index_sha256": "a" * 64,
                    },
                }
            )
    return {
        "data_version": "2026-07-31",
        "predictions": entries,
    }


def test_preregistered_row_freezes_grid_selection_account_and_predictions(tmp_path):
    frozen = protocol.load_frozen_model(ROOT, "b1-m")

    row = register.build_preregistered_row(
        frozen,
        _prediction_manifest("b1-m", tmp_path),
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
        _prediction_manifest("b6-m", tmp_path),
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
    row = register.build_preregistered_row(frozen, _prediction_manifest("b1-m", tmp_path), protocol_path="protocol.json")
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


def test_model_specific_baseline_anchor_uses_only_its_frozen_model_metrics():
    sweep_row = {
        "exp_id": "strategy-sweep/b1-m",
        "direction": "strategy-sweep-b1-m",
        "phase": "S",
        "model_ref": "b1-m",
        "frozen_model_ref": "B1 v1.0",
        "model_manifest": "backtest/models/baselines/b1-m/manifest.json",
        "model_path": "backtest/models/baselines/b1-m/model",
        "model_sha256": "model-sha",
        "baseline_ref": "B1-S v1.0",
        "account": 500000,
        "fees": {"open_cost": 0.00021},
        "test_results": {
            pool: [
                {
                    "candidate_id": protocol.BASELINE_CANDIDATE_ID,
                    "result_dir": f"backtest/result/{pool}",
                    "excess_with_cost_information_ratio": index + 0.1,
                    "excess_with_cost_annualized_return": index + 0.2,
                    "excess_with_cost_max_drawdown": -(index + 0.3),
                }
            ]
            for index, pool in enumerate(protocol.POOL_BENCHMARKS)
        },
    }

    anchor = register.build_phase_s_baseline_anchor(sweep_row)

    assert anchor["exp_id"] == "baseline/b1-s-on-b1-m"
    assert anchor["frozen_model_ref"] == "B1 v1.0"
    assert anchor["metrics_summary"]["csi1000"]["ir"] == 0.1
    assert len(anchor["result_dirs"]) == 3
