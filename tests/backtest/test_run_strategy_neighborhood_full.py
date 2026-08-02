from __future__ import annotations

import hashlib
import json
import math
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import run_strategy_neighborhood_full as runner  # noqa: E402
import strategy_neighborhood_protocol as protocol  # noqa: E402
from phase_s_protocol import load_frozen_model  # noqa: E402


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
    frozen = load_frozen_model(ROOT, "b6-m")
    return {
        "model_ref": "b6-m",
        "model_path": str(frozen.model_path),
        "model_sha256": frozen.model_sha256,
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
        "data_version": "2026-07-31",
    }


def _write_authoritative_manifest(path: Path, entry: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "data_version": "2026-07-31",
                "predictions": [entry],
            }
        ),
        encoding="utf-8",
    )


def test_canonical_manifest_binds_model_and_actual_prediction_frame(
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
    prediction_path = tmp_path / "csi1000_full.pkl"
    prediction.to_pickle(prediction_path)
    entry = _prediction_entry(prediction_path, prediction)
    manifest_path = tmp_path / "prediction_manifest.json"
    _write_authoritative_manifest(manifest_path, entry)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(runner, "DEFAULT_PREDICTION_MANIFEST", manifest_path)
    monkeypatch.setattr(runner, "FULL_PREDICTION_COVERAGE", entry["coverage"])

    verified, coverage = runner.validate_full_prediction_manifest(
        manifest, manifest_path
    )

    assert verified["model_path"] == entry["model_path"]
    assert verified["model_sha256"] == entry["model_sha256"]
    assert coverage == entry["coverage"]


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("schema", "schema_version"),
        ("data_version", "data_version"),
        ("model", "seed-4000 model binding"),
        ("coverage", "coverage"),
        ("manifest_path", "authoritative manifest"),
    ],
)
def test_canonical_manifest_rejects_self_declared_identity_tampering(
    tmp_path: Path, monkeypatch, tamper: str, message: str
):
    index = pd.MultiIndex.from_arrays(
        [
            pd.to_datetime(["2020-01-13", "2020-01-14", "2026-07-31"]),
            ["SH600000", "SH600000", "SH600000"],
        ],
        names=["datetime", "instrument"],
    )
    prediction = pd.DataFrame({"score": [0.1, 0.15, 0.2]}, index=index)
    prediction_path = tmp_path / "csi1000_full.pkl"
    prediction.to_pickle(prediction_path)
    entry = _prediction_entry(prediction_path, prediction)
    manifest_path = tmp_path / "prediction_manifest.json"
    _write_authoritative_manifest(manifest_path, entry)
    monkeypatch.setattr(runner, "DEFAULT_PREDICTION_MANIFEST", manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if tamper == "schema":
        manifest["schema_version"] = 2
    elif tamper == "data_version":
        manifest["data_version"] = None
        manifest["predictions"][0]["data_version"] = None
    elif tamper == "model":
        manifest["predictions"][0]["model_sha256"] = "0" * 64
    elif tamper == "coverage":
        manifest["predictions"][0]["coverage"]["n_rows"] += 1
    else:
        copied = tmp_path / "copied_manifest.json"
        copied.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            runner.validate_full_prediction_manifest(manifest, copied)
        return
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        runner.validate_full_prediction_manifest(manifest, manifest_path)


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
    monkeypatch.setattr(runner, "FULL_PREDICTION_COVERAGE", entry["coverage"])

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
    authoritative_entry = _prediction_entry(authoritative_path, authoritative)
    _write_authoritative_manifest(authoritative_manifest, authoritative_entry)
    monkeypatch.setattr(
        runner,
        "DEFAULT_PREDICTION_MANIFEST",
        authoritative_manifest,
        raising=False,
    )
    monkeypatch.setattr(
        runner, "FULL_PREDICTION_COVERAGE", authoritative_entry["coverage"]
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

    with pytest.raises(ValueError, match="canonical coverage"):
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


def test_run_candidate_accepts_complete_real_metrics_payload_with_success_status(
    tmp_path: Path, monkeypatch
):
    candidate = protocol.strategy_neighborhood_grid()[0]
    result_dir = tmp_path / "result"
    run_dir = result_dir / "run_01"
    run_dir.mkdir(parents=True)
    prediction_sha = "frozen-prediction-sha"
    (result_dir / "meta.json").write_text(
        json.dumps({"source_pred_sha256": prediction_sha}), encoding="utf-8"
    )
    real_metrics_payload = {
        "run": 1,
        "status": "success",
        "excess_with_cost_mean": 0.0003710174954030254,
        "excess_with_cost_std": 0.010413363730053731,
        "excess_with_cost_annualized_return": 0.0923002427804045,
        "excess_with_cost_information_ratio": 0.5496570841594484,
        "excess_with_cost_max_drawdown": -0.41742603472501005,
        "excess_no_cost_mean": 0.0003952894489520631,
        "excess_no_cost_std": 0.010413934930957318,
        "excess_no_cost_annualized_return": 0.09862599057619859,
        "excess_no_cost_information_ratio": 0.5855835134836722,
        "excess_no_cost_max_drawdown": -0.41166006603026384,
        "portfolio_cum_return": 1.5889075969404618,
        "benchmark_cum_return": 0.20909595489501953,
        "excess_cum_return": 1.3798116420454423,
        "portfolio_mean": 0.0006238388270298767,
        "portfolio_std": 0.011836936565324557,
        "portfolio_annualized_return": 0.16000851809176697,
        "portfolio_information_ratio": 0.8130580603033768,
        "portfolio_max_drawdown": -0.2341006822321069,
        "benchmark_mean": 0.00011968612670898438,
        "benchmark_std": 0.015803014859557152,
        "benchmark_annualized_return": 0.028884291648864746,
        "benchmark_information_ratio": 0.11684021132516621,
        "benchmark_max_drawdown": -0.46708500385284424,
        "annualized_one_way_turnover": 6.316854920493451,
        "cumulative_trade_cost": 35314.03872709765,
        "backtest_recorder_id": "bed2a7966e8543bb87fd6fe7bb69de1d",
        "backtest_experiment_id": "255933261929739874",
        "backtest_experiment_name": "backtest_full_candidate_run01",
    }
    (run_dir / "metrics.json").write_text(
        json.dumps(real_metrics_payload), encoding="utf-8"
    )
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
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=f"结果目录: {result_dir}\n",
            stderr="",
        ),
    )

    row = runner._run_candidate(
        runner.load_config(str(runner.DEFAULT_BASE_CONFIG)),
        candidate,
        pred_path=tmp_path / "prediction.pkl",
        prediction_entry={"prediction_sha256": prediction_sha},
        configs_dir=tmp_path / "configs",
    )

    assert row["status"] == "success"
    assert row[protocol.IR_KEY] == real_metrics_payload[protocol.IR_KEY]
    assert row["run"] == 1
    assert row["absolute_portfolio"]["sharpe_ratio"] is not None
    assert set(row["yearly_ir"]) == {"2021"}


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


def test_completed_resume_is_a_byte_preserving_noop(
    tmp_path: Path, monkeypatch, capsys
):
    output_root = tmp_path / "output"
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    sentinel_config = configs_dir / "sentinel.yaml"
    sentinel_config.write_text("untouched: true\n", encoding="utf-8")
    manifest_path = tmp_path / "prediction_manifest.json"
    manifest_path.write_text('{"predictions": []}\n', encoding="utf-8")
    prediction_path = tmp_path / "prediction.pkl"
    prediction_path.write_bytes(b"frozen prediction")
    prediction_sha = hashlib.sha256(prediction_path.read_bytes()).hexdigest()
    prediction_entry = {
        "path": str(prediction_path),
        "prediction_sha256": prediction_sha,
    }
    grid = protocol.strategy_neighborhood_grid()
    base = runner.load_config(str(runner.DEFAULT_BASE_CONFIG))
    rows = _successful_grid_rows()
    for row, candidate in zip(rows, grid):
        row["source_pred_sha256"] = prediction_sha
        row["effective_config_sha256"] = runner.effective_config_sha256(
            base, candidate
        )

    protocol_payload = runner.protocol_payload(grid, runner.DEFAULT_BASE_CONFIG)
    protocol_path = output_root / "protocol.json"
    runner.write_json_atomic(protocol_path, protocol_payload)
    protocol_sha = runner.sha256_file(protocol_path)
    run_contract = runner.build_checkpoint_contract(
        protocol_sha, manifest_path, runner.DEFAULT_BASE_CONFIG
    )
    completed = runner.completed_results_payload(
        rows,
        grid,
        protocol_sha256=protocol_sha,
        run_contract=run_contract,
    )
    completed.update(
        {
            "updated_at": "2000-01-01T00:00:00",
            "prediction_manifest": runner._repo_path(manifest_path),
            "base_config": runner._repo_path(runner.DEFAULT_BASE_CONFIG),
            "source_pred": runner._repo_path(prediction_path),
            "source_pred_sha256": prediction_sha,
        }
    )
    results_path = output_root / "full_results.json"
    runner.write_json_atomic(results_path, completed)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    monkeypatch.setattr(
        runner,
        "validate_full_prediction_manifest",
        lambda manifest, path: (prediction_entry, {"n_rows": 1}),
    )
    monkeypatch.setattr(
        runner,
        "_run_candidate",
        lambda *args, **kwargs: pytest.fail("completed resume ran a candidate"),
    )

    runner.main(
        [
            "--prediction-manifest",
            str(manifest_path),
            "--output-root",
            str(output_root),
            "--configs-dir",
            str(configs_dir),
            "--base-config",
            str(runner.DEFAULT_BASE_CONFIG),
        ]
    )

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    output = capsys.readouterr().out
    assert after == before
    assert "already full_complete" in output
    assert "no files rewritten" in output


def test_completed_resume_with_missing_protocol_fails_before_any_write(
    tmp_path: Path, monkeypatch
):
    output_root = tmp_path / "output"
    manifest_path = tmp_path / "prediction_manifest.json"
    manifest_path.write_text('{"predictions": []}\n', encoding="utf-8")
    prediction_path = tmp_path / "prediction.pkl"
    prediction_path.write_bytes(b"frozen prediction")
    prediction_sha = hashlib.sha256(prediction_path.read_bytes()).hexdigest()
    prediction_entry = {
        "path": str(prediction_path),
        "prediction_sha256": prediction_sha,
    }
    grid = protocol.strategy_neighborhood_grid()
    base = runner.load_config(str(runner.DEFAULT_BASE_CONFIG))
    rows = _successful_grid_rows()
    for row, candidate in zip(rows, grid):
        row["source_pred_sha256"] = prediction_sha
        row["effective_config_sha256"] = runner.effective_config_sha256(
            base, candidate
        )

    protocol_payload = runner.protocol_payload(grid, runner.DEFAULT_BASE_CONFIG)
    protocol_path = output_root / "protocol.json"
    runner.write_json_atomic(protocol_path, protocol_payload)
    protocol_sha = runner.sha256_file(protocol_path)
    run_contract = runner.build_checkpoint_contract(
        protocol_sha, manifest_path, runner.DEFAULT_BASE_CONFIG
    )
    completed = runner.completed_results_payload(
        rows,
        grid,
        protocol_sha256=protocol_sha,
        run_contract=run_contract,
    )
    completed.update(
        {
            "prediction_manifest": runner._repo_path(manifest_path),
            "base_config": runner._repo_path(runner.DEFAULT_BASE_CONFIG),
            "source_pred": runner._repo_path(prediction_path),
            "source_pred_sha256": prediction_sha,
        }
    )
    results_path = output_root / "full_results.json"
    runner.write_json_atomic(results_path, completed)
    protocol_path.unlink()
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        runner,
        "validate_full_prediction_manifest",
        lambda manifest, path: (prediction_entry, {"n_rows": 1}),
    )
    monkeypatch.setattr(
        runner,
        "_run_candidate",
        lambda *args, **kwargs: pytest.fail("completed resume ran a candidate"),
    )

    with pytest.raises(ValueError, match="completed.*protocol"):
        runner.main(
            [
                "--prediction-manifest",
                str(manifest_path),
                "--output-root",
                str(output_root),
                "--configs-dir",
                str(tmp_path / "configs"),
                "--base-config",
                str(runner.DEFAULT_BASE_CONFIG),
            ]
        )

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not protocol_path.exists()
