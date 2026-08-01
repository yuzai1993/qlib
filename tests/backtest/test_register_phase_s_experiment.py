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


def _completed_sweep_row(state: str = "test_complete") -> dict:
    winner = {
        "candidate_id": "topk-t30-d2-h20",
        "strategy_class": "TopkDropoutStrategy",
        "topk": 30,
        "n_drop": 2,
        "hold_thresh": 20,
    }
    return {
        "exp_id": "strategy-sweep/b6-m",
        "direction": "strategy-sweep-b6-m",
        "phase": "S",
        "state": state,
        "date": "2026-08-01",
        "model_ref": "b6-m",
        "frozen_model_ref": "B6 v1.0",
        "model_manifest": "backtest/models/baselines/b6-m/manifest.json",
        "model_path": "backtest/models/baselines/b6-m/seed4000/trained_model",
        "model_sha256": "model-sha",
        "baseline_ref": "B1-S v1.0",
        "account": 500000,
        "risk_degree": 0.95,
        "fees": {"open_cost": 0.00021},
        "benchmarks": dict(protocol.POOL_BENCHMARKS),
        "selection_pool": "csi1000",
        "selection_segment": list(protocol.VALID_SEGMENT),
        "selection_rule": ["ir desc", "annualized_return desc"],
        "test_segment": list(protocol.TEST_SEGMENT),
        "data_version": "2026-07-31",
        "selected_candidate_id": "topk-t30-d2-h20",
        "selected_strategy": winner,
        "valid_result_path": "backtest/experiments/strategy/b6-m/valid_results.json",
        "valid_result_sha256": "valid-sha",
        "valid_results": [
            {
                **winner,
                "config": "backtest/configs/strategy-sweep/b6-m/winner_valid.yaml",
            }
        ],
        "prediction_artifacts": [
            {
                "model_ref": "b6-m",
                "pool": pool,
                "segment": segment,
                "path": f"backtest/predictions/{pool}_{segment}.pkl",
                "prediction_sha256": f"{pool}-{segment}-sha",
            }
            for pool in protocol.POOL_BENCHMARKS
            for segment in ("valid", "test")
        ],
        "strategy_grid": [
            {"candidate_id": protocol.BASELINE_CANDIDATE_ID},
            winner,
        ],
        "test_results": {
            pool: [
                {
                    "candidate_id": protocol.BASELINE_CANDIDATE_ID,
                    "result_dir": f"backtest/result/{pool}-b1s",
                    "excess_with_cost_information_ratio": 0.5,
                    "excess_with_cost_annualized_return": 0.1,
                    "excess_with_cost_max_drawdown": -0.4,
                },
                {
                    "candidate_id": "topk-t30-d2-h20",
                    "status": "success",
                    "result_dir": f"backtest/result/{pool}-winner",
                    "config": f"backtest/configs/strategy-sweep/b6-m/{pool}_test.yaml",
                    "excess_with_cost_information_ratio": index + 0.9,
                    "excess_with_cost_annualized_return": index + 0.16,
                    "excess_with_cost_max_drawdown": -(index + 0.33),
                    "annualized_one_way_turnover": 6.06,
                },
            ]
            for index, pool in enumerate(protocol.POOL_BENCHMARKS)
        },
    }


def test_strategy_baseline_promotion_matches_cleanup_retention_schema():
    row = register.build_strategy_baseline_promotion(
        _completed_sweep_row(), baseline_ref="B2-S v1.0"
    )

    assert row["exp_id"] == "baseline/b2-s-on-b6-m"
    assert row["direction"] == "baseline-strategy"
    assert row["phase"] == "S"
    assert row["state"] == "baseline"
    assert row["conclusion"] == "baseline"
    assert row["baseline_ref"] == "B2-S v1.0"
    assert row["frozen_model_ref"] == "B6 v1.0"
    assert row["cleanup_retention_eligible"] is True
    assert row["promoted_from"] == "strategy-sweep/b6-m"
    assert row["data_version"] == "2026-07-31"
    assert row["valid_result_path"].endswith("valid_results.json")
    assert len(row["configs"]) == 5
    assert row["configs"][0].endswith(
        "strategy-stability/b6-m/topk-t30-d2-h20_csi1000_full.yaml"
    )
    assert all(
        "baseline-strategy/b2-s" in path or "strategy-stability/b6-m" in path
        for path in row["configs"]
    )
    assert len(row["prediction_artifacts"]) == 4
    assert {
        (item["model_ref"], item["pool"], item["segment"])
        for item in row["prediction_artifacts"]
    } == {
        ("b6-m", "csi1000", "valid"),
        ("b6-m", "csi1000", "test"),
        ("b6-m", "csi300", "test"),
        ("b6-m", "csi500", "test"),
    }
    # 策略参数必须完整保留，而不只是 candidate_id
    assert row["strategy"]["topk"] == 30
    assert row["strategy"]["n_drop"] == 2
    assert row["strategy"]["hold_thresh"] == 20
    # cleanup 期望每个池是单个候选 dict，而非候选列表
    assert set(row["test_results"]) == set(protocol.POOL_BENCHMARKS)
    for pool in protocol.POOL_BENCHMARKS:
        assert row["test_results"][pool]["candidate_id"] == "topk-t30-d2-h20"
        assert row["test_results"][pool]["result_dir"].endswith(f"{pool}-winner")
    assert row["metrics_summary"]["csi1000"]["ir"] == 0.9
    assert sorted(row["result_dirs"]) == sorted(
        f"backtest/result/{pool}-winner" for pool in protocol.POOL_BENCHMARKS
    )


def test_strategy_baseline_promotion_is_accepted_by_cleanup_selector():
    sys.path.insert(0, str(SCRIPTS))
    import cleanup_experiment_artifacts as cleanup  # noqa: PLC0415

    row = register.build_strategy_baseline_promotion(
        _completed_sweep_row(), baseline_ref="B2-S v1.0"
    )

    assert cleanup.select_phase_s_retained_result_paths([row]) == {
        f"backtest/result/{pool}-winner" for pool in protocol.POOL_BENCHMARKS
    }


def test_strategy_baseline_promotion_rejects_unfinished_sweep():
    with pytest.raises(ValueError, match="test_complete"):
        register.build_strategy_baseline_promotion(
            _completed_sweep_row(state="valid_complete"), baseline_ref="B2-S v1.0"
        )


def test_strategy_baseline_promotion_rejects_candidate_without_valid_selection():
    sweep_row = _completed_sweep_row()
    sweep_row["selected_candidate_id"] = protocol.BASELINE_CANDIDATE_ID

    with pytest.raises(ValueError, match="selected_strategy"):
        register.build_strategy_baseline_promotion(sweep_row, baseline_ref="B2-S v1.0")


def test_strategy_baseline_promotion_rejects_incomplete_prediction_matrix():
    sweep_row = _completed_sweep_row()
    sweep_row["prediction_artifacts"] = sweep_row["prediction_artifacts"][:-1]

    with pytest.raises(ValueError, match="prediction artifact matrix"):
        register.build_strategy_baseline_promotion(sweep_row, baseline_ref="B2-S v1.0")


def test_strategy_baseline_promotion_rejects_duplicate_prediction_identity():
    sweep_row = _completed_sweep_row()
    sweep_row["prediction_artifacts"].append(
        dict(sweep_row["prediction_artifacts"][0])
    )

    with pytest.raises(ValueError, match="prediction artifact matrix"):
        register.build_strategy_baseline_promotion(sweep_row, baseline_ref="B2-S v1.0")


def test_strategy_baseline_promotion_accepts_frozen_promotion_date():
    row = register.build_strategy_baseline_promotion(
        _completed_sweep_row(),
        baseline_ref="B2-S v1.0",
        promotion_date="2026-08-01",
    )

    assert row["date"] == "2026-08-01"


def test_baseline_anchor_upsert_is_idempotent_for_identical_promotion(tmp_path: Path):
    registry = tmp_path / "registry.jsonl"
    registry.write_text(
        json.dumps({"exp_id": "strategy-sweep/b6-m", "phase": "S"}) + "\n",
        encoding="utf-8",
    )
    row = register.build_strategy_baseline_promotion(
        _completed_sweep_row(), baseline_ref="B2-S v1.0"
    )

    register.upsert_baseline_anchor_row(registry, row)
    register.upsert_baseline_anchor_row(registry, row)

    rows = register.load_registry(registry)
    assert [item["exp_id"] for item in rows] == [
        "strategy-sweep/b6-m",
        "baseline/b2-s-on-b6-m",
    ]


def test_baseline_anchor_upsert_rejects_silent_history_rewrite(tmp_path: Path):
    registry = tmp_path / "registry.jsonl"
    row = register.build_strategy_baseline_promotion(
        _completed_sweep_row(), baseline_ref="B2-S v1.0"
    )
    register.upsert_baseline_anchor_row(registry, row)
    changed = dict(row, model_sha256="changed")

    with pytest.raises(ValueError, match="new baseline version"):
        register.upsert_baseline_anchor_row(registry, changed)


def test_current_protocol_writer_only_emits_b6(tmp_path: Path):
    output = tmp_path / "protocol.json"

    register.write_protocol(output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert list(payload["models"]) == ["b6-m"]
