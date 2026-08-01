from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import run_strategy_neighborhood as runner  # noqa: E402
import strategy_neighborhood_protocol as protocol  # noqa: E402


def test_pending_candidates_skip_only_successful_checkpoints():
    grid = protocol.strategy_neighborhood_grid()[:3]
    checkpoint = {
        "all_rows": [
            {"candidate_id": grid[0]["candidate_id"], "status": "success"},
            {"candidate_id": grid[1]["candidate_id"], "status": "failed"},
        ]
    }

    pending = runner.pending_candidates(grid, checkpoint)

    assert [row["candidate_id"] for row in pending] == [
        grid[1]["candidate_id"],
        grid[2]["candidate_id"],
    ]


def test_upsert_result_replaces_retry_without_changing_grid_order():
    grid = protocol.strategy_neighborhood_grid()[:3]
    rows = [
        {"candidate_id": grid[0]["candidate_id"], "status": "success"},
        {"candidate_id": grid[1]["candidate_id"], "status": "failed"},
    ]

    merged = runner.upsert_result(
        rows,
        {"candidate_id": grid[1]["candidate_id"], "status": "success"},
        grid,
    )

    assert [row["candidate_id"] for row in merged] == [
        grid[0]["candidate_id"],
        grid[1]["candidate_id"],
    ]
    assert merged[1]["status"] == "success"
    assert merged[1]["previous_attempts"] == [{"status": "failed"}]


def test_atomic_checkpoint_round_trip(tmp_path: Path):
    path = tmp_path / "valid_results.json"
    payload = {"state": "running", "all_rows": [{"candidate_id": "x"}]}

    runner.write_json_atomic(path, payload)

    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert not path.with_name(path.name + ".tmp").exists()


def test_test_plan_contains_only_frozen_winner_for_three_pools(tmp_path: Path):
    winner = protocol.strategy_neighborhood_grid()[0]
    predictions = []
    for pool in ("csi1000", "csi300", "csi500"):
        path = tmp_path / f"{pool}_test.pkl"
        path.write_bytes(pool.encode())
        predictions.append(
            {
                "model_ref": "b6-m",
                "pool": pool,
                "segment": "test",
                "path": str(path),
                "prediction_sha256": "sha",
            }
        )

    tasks = runner.build_test_plan(winner, {"predictions": predictions})

    assert [task["pool"] for task in tasks] == ["csi1000", "csi300", "csi500"]
    assert {task["candidate"]["candidate_id"] for task in tasks} == {
        winner["candidate_id"]
    }
    assert all(task["segment"] == "test" for task in tasks)


def test_test_plan_rejects_incomplete_prediction_matrix(tmp_path: Path):
    winner = protocol.strategy_neighborhood_grid()[0]

    with pytest.raises(ValueError, match="prediction"):
        runner.build_test_plan(winner, {"predictions": []})


def test_prepare_only_is_an_explicit_cli_mode(tmp_path: Path):
    args = runner.parse_args(
        [
            "--prediction-manifest",
            str(tmp_path / "manifest.json"),
            "--prepare-only",
        ]
    )

    assert args.prepare_only is True
