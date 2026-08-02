from __future__ import annotations

import hashlib
import json
import math
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import run_strategy_neighborhood_full as runner  # noqa: E402
import strategy_neighborhood_protocol as protocol  # noqa: E402


def test_protocol_is_versioned_full_history_in_sample():
    grid = protocol.strategy_neighborhood_grid()

    payload = runner.protocol_payload(grid, runner.DEFAULT_BASE_CONFIG)

    assert payload["exp_id"] == "strategy-neighborhood/b2-s-local-full-v2"
    assert payload["evaluation_mode"] == "full_history_in_sample"
    assert payload["selection_segment"] == ["2020-01-13", "2026-07-31"]
    assert payload["strategy_grid"] == grid
    assert payload["baseline_ref"] == "B2-S v1.0"
    assert payload["frozen_model_ref"] == "B6 v1.0"
    assert "test_policy" not in payload
    assert "test_segment" not in payload
    assert "test_pools" not in payload


def test_protocol_rejects_a_mutated_540_point_grid():
    grid = protocol.strategy_neighborhood_grid()
    grid[0]["topk"] = 999

    with pytest.raises(ValueError, match="immutable 540-candidate grid"):
        runner.protocol_payload(grid, runner.DEFAULT_BASE_CONFIG)


def test_manifest_requires_exact_b6_csi1000_full_prediction():
    expected = {
        "model_ref": "b6-m",
        "pool": "csi1000",
        "segment": "full",
        "path": "prediction.pkl",
    }
    manifest = {
        "predictions": [
            {"model_ref": "b6-m", "pool": "csi1000", "segment": "valid"},
            expected,
        ]
    }

    entry = runner.full_prediction_entry(manifest)

    assert entry == expected


@pytest.mark.parametrize(
    "predictions",
    [
        [],
        [
            {"model_ref": "b6-m", "pool": "csi1000", "segment": "full"},
            {"model_ref": "b6-m", "pool": "csi1000", "segment": "full"},
        ],
        [{"model_ref": "b6-m", "pool": "csi300", "segment": "full"}],
    ],
)
def test_manifest_rejects_missing_duplicate_or_wrong_scope(predictions):
    with pytest.raises(ValueError, match="exactly one b6-m/csi1000/full"):
        runner.full_prediction_entry({"predictions": predictions})


def test_checkpoint_reuse_requires_prediction_and_effective_config_sha():
    grid = protocol.strategy_neighborhood_grid()[:2]
    base = runner.load_config(str(runner.DEFAULT_BASE_CONFIG))
    checkpoint = {
        "all_rows": [
            {
                **grid[0],
                "status": "success",
                "source_pred_sha256": "old",
                "effective_config_sha256": runner.effective_config_sha256(
                    base, grid[0]
                ),
            },
            {
                **grid[1],
                "status": "success",
                "source_pred_sha256": "new",
                "effective_config_sha256": "stale-config",
            },
        ]
    }

    pending = runner.pending_candidates(
        grid, checkpoint, base=base, prediction_sha256="new"
    )

    assert pending == grid


def test_checkpoint_reuses_only_matching_full_config_and_prediction():
    grid = protocol.strategy_neighborhood_grid()[:2]
    base = runner.load_config(str(runner.DEFAULT_BASE_CONFIG))
    checkpoint = {
        "all_rows": [
            {
                **grid[0],
                "status": "success",
                "source_pred_sha256": "pred-sha",
                "effective_config_sha256": runner.effective_config_sha256(
                    base, grid[0]
                ),
            },
            {
                **grid[1],
                "status": "failed",
                "source_pred_sha256": "pred-sha",
                "effective_config_sha256": runner.effective_config_sha256(
                    base, grid[1]
                ),
            },
        ]
    }

    pending = runner.pending_candidates(
        grid, checkpoint, base=base, prediction_sha256="pred-sha"
    )

    assert pending == [grid[1]]
    assert pending[0] is not grid[1]


def _prediction_entry(path: Path, prediction: pd.DataFrame) -> dict:
    dates = pd.DatetimeIndex(prediction.index.get_level_values("datetime"))
    return {
        "model_ref": "b6-m",
        "pool": "csi1000",
        "segment": "full",
        "path": str(path),
        "prediction_sha256": runner.sha256_file(path),
        "coverage": {
            "start": str(dates.min().date()),
            "end": str(dates.max().date()),
            "n_dates": int(dates.nunique()),
            "n_rows": int(len(prediction)),
            "index_sha256": runner.prediction_index_sha256(prediction.index),
        },
    }


def _write_authoritative_manifest(path: Path, entry: dict) -> None:
    path.write_text(json.dumps({"predictions": [entry]}), encoding="utf-8")


def test_prediction_artifact_requires_exact_full_coverage_and_sha(
    tmp_path: Path, monkeypatch
):
    index = pd.MultiIndex.from_arrays(
        [
            pd.to_datetime(["2020-01-13", "2020-01-14", "2026-07-31"]),
            ["SH600000", "SH600000", "SH600000"],
        ],
        names=["datetime", "instrument"],
    )
    prediction = pd.DataFrame({"score": [0.1, 0.15, 0.2]}, index=index)
    path = tmp_path / "csi1000_full.pkl"
    prediction.to_pickle(path)
    entry = _prediction_entry(path, prediction)
    authoritative_manifest = tmp_path / "tracked_prediction_manifest.json"
    _write_authoritative_manifest(authoritative_manifest, entry)
    monkeypatch.setattr(
        runner,
        "DEFAULT_PREDICTION_MANIFEST",
        authoritative_manifest,
        raising=False,
    )

    coverage = runner.validate_prediction_artifact(entry, path)

    assert coverage == entry["coverage"]
    changed = dict(entry)
    changed["prediction_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA"):
        runner.validate_prediction_artifact(changed, path)
    wrong_coverage = {**entry, "coverage": {**entry["coverage"], "n_rows": 4}}
    with pytest.raises(ValueError, match="coverage"):
        runner.validate_prediction_artifact(wrong_coverage, path)


def test_self_consistent_endpoint_only_prediction_is_rejected_by_tracked_manifest(
    tmp_path: Path, monkeypatch
):
    authoritative_index = pd.MultiIndex.from_arrays(
        [
            pd.to_datetime(["2020-01-13", "2020-01-14", "2026-07-31"]),
            ["SH600000", "SH600000", "SH600000"],
        ],
        names=["datetime", "instrument"],
    )
    authoritative = pd.DataFrame({"score": [0.1, 0.15, 0.2]}, index=authoritative_index)
    authoritative_path = tmp_path / "authoritative_full.pkl"
    authoritative.to_pickle(authoritative_path)
    authoritative_manifest = tmp_path / "tracked_prediction_manifest.json"
    _write_authoritative_manifest(
        authoritative_manifest,
        _prediction_entry(authoritative_path, authoritative),
    )
    monkeypatch.setattr(
        runner,
        "DEFAULT_PREDICTION_MANIFEST",
        authoritative_manifest,
        raising=False,
    )

    endpoint_index = pd.MultiIndex.from_arrays(
        [
            pd.to_datetime(["2020-01-13", "2026-07-31"]),
            ["SH600000", "SH600000"],
        ],
        names=["datetime", "instrument"],
    )
    endpoint_only = pd.DataFrame({"score": [0.1, 0.2]}, index=endpoint_index)
    endpoint_path = tmp_path / "endpoint_only.pkl"
    endpoint_only.to_pickle(endpoint_path)
    self_consistent_entry = _prediction_entry(endpoint_path, endpoint_only)

    with pytest.raises(ValueError, match="authoritative tracked manifest"):
        runner.validate_prediction_artifact(self_consistent_entry, endpoint_path)


def test_full_config_uses_full_segment_and_canonical_filename(tmp_path: Path):
    candidate = protocol.strategy_neighborhood_grid()[0]
    base = runner.load_config(str(runner.DEFAULT_BASE_CONFIG))

    config_path, rendered = runner.render_full_config(
        base, candidate, configs_dir=tmp_path
    )

    assert config_path.name == f"{candidate['candidate_id']}_csi1000_full.yaml"
    config = yaml.safe_load(rendered)
    assert config["segments"]["test"] == ["2020-01-13", "2026-07-31"]
    assert config["phase_s"] == {
        "candidate_id": candidate["candidate_id"],
        "selection_segment": "full",
        "pool": "csi1000",
    }


def test_cli_defaults_to_three_bounded_workers(tmp_path: Path):
    args = runner.parse_args(["--prediction-manifest", str(tmp_path / "manifest.json")])

    assert args.workers == 3
    with pytest.raises(SystemExit):
        runner.parse_args(
            [
                "--prediction-manifest",
                str(tmp_path / "manifest.json"),
                "--workers",
                "4",
            ]
        )


def test_batch_completion_checkpoints_on_caller_thread_with_at_most_three_workers():
    caller_thread = threading.get_ident()
    active = 0
    max_active = 0
    lock = threading.Lock()
    worker_threads = set()
    checkpoint_threads = []

    def run_candidate(candidate):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            worker_threads.add(threading.get_ident())
        time.sleep(0.01)
        with lock:
            active -= 1
        return {**candidate, "status": "success"}

    completed = []

    def checkpoint(row):
        checkpoint_threads.append(threading.get_ident())
        completed.append(row)

    candidates = [{"candidate_id": f"c{i}"} for i in range(7)]
    runner.run_bounded_batches(
        candidates, run_candidate=run_candidate, checkpoint=checkpoint, workers=3
    )

    assert {row["candidate_id"] for row in completed} == {
        candidate["candidate_id"] for candidate in candidates
    }
    assert 1 < max_active <= 3
    assert worker_threads != {caller_thread}
    assert checkpoint_threads == [caller_thread] * len(candidates)


def test_worker_exception_becomes_failed_row_and_other_futures_are_checkpointed():
    candidates = [{"candidate_id": f"c{i}"} for i in range(3)]
    checkpointed = []

    def run_candidate(candidate):
        if candidate["candidate_id"] == "c1":
            raise OSError("subprocess launch failed")
        return {**candidate, "status": "success"}

    runner.run_bounded_batches(
        candidates,
        run_candidate=run_candidate,
        checkpoint=checkpointed.append,
        workers=3,
    )

    assert {row["candidate_id"] for row in checkpointed} == {"c0", "c1", "c2"}
    failed = next(row for row in checkpointed if row["candidate_id"] == "c1")
    assert failed["status"] == "failed"
    assert "subprocess launch failed" in failed["error"]
    assert sum(row["status"] == "success" for row in checkpointed) == 2


def _successful_grid_rows():
    rows = []
    for index, candidate in enumerate(protocol.strategy_neighborhood_grid()):
        rows.append(
            {
                **candidate,
                "status": "success",
                protocol.IR_KEY: 0.2 + index / 10000,
                protocol.ANN_KEY: 0.1,
                protocol.MDD_KEY: -0.2,
                protocol.TURNOVER_KEY: 10.0,
            }
        )
    return rows


def test_full_results_freezes_score_winner_only_after_540_successes():
    grid = protocol.strategy_neighborhood_grid()
    rows = _successful_grid_rows()
    _, expected_winner = protocol.score_valid_candidates(rows, grid)

    payload = runner.completed_results_payload(
        rows,
        grid,
        protocol_sha256="protocol-sha",
        run_contract={
            "protocol_sha256": "protocol-sha",
            "prediction_manifest_sha256": "manifest-sha",
            "base_config_sha256": "base-sha",
        },
    )

    assert payload["state"] == "full_complete"
    assert len(payload["all_rows"]) == 540
    assert payload["winner"] == expected_winner
    assert payload["evaluation_mode"] == "full_history_in_sample"
    assert payload["segment"] == "full"
    assert "test" not in payload

    incomplete = rows[:-1]
    with pytest.raises(ValueError, match="540 successful"):
        runner.completed_results_payload(
            incomplete,
            grid,
            protocol_sha256="protocol-sha",
            run_contract={},
        )


def test_result_metrics_include_excess_yearly_ir_and_absolute_portfolio_metrics(
    tmp_path: Path,
):
    run_dir = tmp_path / "result" / "run_01"
    run_dir.mkdir(parents=True)
    metrics = {
        protocol.IR_KEY: 0.75,
        protocol.ANN_KEY: 0.12,
        protocol.MDD_KEY: -0.18,
        protocol.TURNOVER_KEY: 8.5,
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    report = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"]),
            "return": [0.02, -0.01, 0.01],
            "cost": [0.001, 0.001, 0.001],
            "bench": [0.002, -0.002, 0.001],
            "turnover": [0.2, 0.2, 0.2],
        }
    )
    report.to_csv(run_dir / "report_normal.csv", index=False)

    result = runner.load_result_metrics(tmp_path / "result")

    net = pd.Series([0.019, -0.011, 0.009])
    expected_return = net.mean() * 250
    expected_volatility = net.std(ddof=1) * math.sqrt(250)
    assert result[protocol.IR_KEY] == 0.75
    assert set(result["yearly_ir"]) == {"2021"}
    assert result["absolute_portfolio"]["annualized_return"] == pytest.approx(
        expected_return
    )
    assert result["absolute_portfolio"]["annualized_volatility"] == pytest.approx(
        expected_volatility
    )
    assert result["absolute_portfolio"]["sharpe_ratio"] == pytest.approx(
        expected_return / expected_volatility
    )
    assert result["absolute_portfolio"]["calmar_ratio"] is not None


def test_checkpoint_contract_stores_all_frozen_input_hashes(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    base = tmp_path / "base.yaml"
    manifest.write_text('{"predictions": []}', encoding="utf-8")
    base.write_text("account: 500000\n", encoding="utf-8")

    contract = runner.build_checkpoint_contract("protocol-sha", manifest, base)

    assert contract == {
        "protocol_sha256": "protocol-sha",
        "prediction_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "base_config_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
    }
