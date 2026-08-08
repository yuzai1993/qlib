from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest/scripts"
sys.path.insert(0, str(SCRIPTS))

import finalize_strategy_neighborhood_full as finalizer  # noqa: E402
import build_strategy_stability_report as stability_report  # noqa: E402
import run_strategy_neighborhood_full as runner  # noqa: E402
import strategy_neighborhood_protocol as neighborhood  # noqa: E402
from phase_s_protocol import load_frozen_model  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def _use_test_authoritative_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        runner, "DEFAULT_PREDICTION_MANIFEST", tmp_path / "prediction_manifest.json"
    )
    index = pd.MultiIndex.from_arrays(
        [
            pd.to_datetime(["2020-01-13", "2020-01-14", "2026-07-31"]),
            ["SH600000", "SH600000", "SH600000"],
        ],
        names=["datetime", "instrument"],
    )
    monkeypatch.setattr(
        runner,
        "FULL_PREDICTION_COVERAGE",
        {
            "start": "2020-01-13",
            "end": "2026-07-31",
            "n_dates": 3,
            "n_rows": 3,
            "index_sha256": runner.prediction_index_sha256(index),
        },
    )


def _inputs(tmp_path: Path) -> tuple[dict, dict, Path, Path, Path]:
    base_path = runner.DEFAULT_BASE_CONFIG
    protocol = runner.protocol_payload(
        neighborhood.strategy_neighborhood_grid(), base_path
    )
    protocol_path = tmp_path / "protocol.json"
    _write_json(protocol_path, protocol)

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
    frozen = load_frozen_model(ROOT, "b6-m")
    manifest = {
        "schema_version": 1,
        "data_version": "2026-07-31",
        "predictions": [
            {
                "model_ref": "b6-m",
                "model_path": str(frozen.model_path),
                "model_sha256": frozen.model_sha256,
                "pool": "csi1000",
                "segment": "full",
                "path": str(prediction_path),
                "prediction_sha256": finalizer.sha256_file(prediction_path),
                "coverage": {
                    "start": "2020-01-13",
                    "end": "2026-07-31",
                    "n_dates": 3,
                    "n_rows": 3,
                    "index_sha256": runner.prediction_index_sha256(prediction.index),
                },
                "data_version": "2026-07-31",
            }
        ],
    }
    manifest_path = tmp_path / "prediction_manifest.json"
    _write_json(manifest_path, manifest)
    return protocol, manifest, protocol_path, manifest_path, prediction_path


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("schema", "schema_version"),
        ("data_version", "data_version"),
        ("model", "seed-4000 model binding"),
        ("coverage", "coverage"),
    ],
)
def test_preregistration_uses_authoritative_prediction_validation(
    tmp_path: Path, tamper: str, message: str
):
    protocol, manifest, protocol_path, manifest_path, prediction_path = _inputs(
        tmp_path
    )
    if tamper == "schema":
        manifest["schema_version"] = 2
    elif tamper == "data_version":
        manifest["data_version"] = None
        manifest["predictions"][0]["data_version"] = None
    elif tamper == "model":
        manifest["predictions"][0]["model_path"] = str(tmp_path / "other-model")
    else:
        changed = pd.read_pickle(prediction_path).iloc[[0, -1]]
        changed.to_pickle(prediction_path)
        manifest["predictions"][0]["prediction_sha256"] = finalizer.sha256_file(
            prediction_path
        )
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match=message):
        finalizer.build_preregistered_row(
            protocol,
            manifest,
            protocol_path=protocol_path,
            prediction_manifest_path=manifest_path,
        )


@pytest.fixture(scope="module")
def completed_rows() -> list[dict]:
    base = runner.load_config(str(runner.DEFAULT_BASE_CONFIG))
    rows = []
    for index, candidate in enumerate(neighborhood.strategy_neighborhood_grid()):
        ir = 0.75 + index / 10_000
        rows.append(
            {
                **candidate,
                "status": "success",
                "source_pred_sha256": "prediction-sha",
                "effective_config_sha256": runner.effective_config_sha256(
                    base, candidate
                ),
                "excess_with_cost_information_ratio": ir,
                "excess_with_cost_annualized_return": 0.10 + index / 100_000,
                "excess_with_cost_max_drawdown": -0.20 + index / 1_000_000,
                "annualized_one_way_turnover": 4.0 - index / 100_000,
                "yearly_ir": {"2020": 0.5, "2021": 0.7},
                "absolute_portfolio": {
                    "sharpe_ratio": 1.1,
                    "calmar_ratio": 0.8,
                    "annualized_volatility": 0.18,
                },
                "result_dir": f"backtest/result/{candidate['candidate_id']}",
            }
        )
    return rows


def _preregistered_and_results(
    tmp_path: Path, completed_rows: list[dict]
) -> tuple[dict, dict, dict, Path, Path, Path]:
    protocol, manifest, protocol_path, manifest_path, prediction_path = _inputs(
        tmp_path
    )
    preregistered = finalizer.build_preregistered_row(
        protocol,
        manifest,
        protocol_path=protocol_path,
        prediction_manifest_path=manifest_path,
    )
    prediction_sha = manifest["predictions"][0]["prediction_sha256"]
    rows = copy.deepcopy(completed_rows)
    for row in rows:
        row["source_pred_sha256"] = prediction_sha
    run_contract = {
        "protocol_sha256": finalizer.sha256_file(protocol_path),
        "prediction_manifest_sha256": finalizer.sha256_file(manifest_path),
        "base_config_sha256": protocol["base_config_sha256"],
    }
    results = runner.completed_results_payload(
        rows,
        protocol["strategy_grid"],
        protocol_sha256=run_contract["protocol_sha256"],
        run_contract=run_contract,
    )
    results_path = tmp_path / "full_results.json"
    _write_json(results_path, results)
    return (
        preregistered,
        protocol,
        results,
        results_path,
        manifest_path,
        prediction_path,
    )


def test_preregistered_row_freezes_full_period_contract(tmp_path: Path):
    protocol, manifest, protocol_path, manifest_path, _ = _inputs(tmp_path)

    row = finalizer.build_preregistered_row(
        protocol,
        manifest,
        protocol_path=protocol_path,
        prediction_manifest_path=manifest_path,
    )

    assert row["exp_id"] == "strategy-neighborhood/b2-s-local-full-v2"
    assert row["direction"] == "strategy-neighborhood-b2-s-full"
    assert row["phase"] == "S"
    assert row["state"] == "preregistered"
    assert row["baseline_ref"] == "B2-S v1.0"
    assert row["frozen_model_ref"] == "B6 v1.0"
    assert row["evaluation_mode"] == "full_history_in_sample"
    assert row["selection_pool"] == "csi1000"
    assert row["selection_segment"] == ["2020-01-13", "2026-07-31"]
    assert row["candidate_count"] == 540
    assert row["cleanup_retention_eligible"] is False
    assert row["account"] == 500000
    assert row["benchmark"] == "SH000852"
    assert row["prediction_artifact"]["segment"] == "full"
    assert "test_policy" not in row


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 1, "schema_version"),
        ("selection_rule", ["candidate_id desc"], "selection_rule"),
    ],
)
def test_preregistration_rejects_mutated_protocol_contract(
    tmp_path: Path, field: str, value: object, message: str
):
    protocol, manifest, protocol_path, manifest_path, _ = _inputs(tmp_path)
    protocol[field] = value
    _write_json(protocol_path, protocol)

    with pytest.raises(ValueError, match=message):
        finalizer.build_preregistered_row(
            protocol,
            manifest,
            protocol_path=protocol_path,
            prediction_manifest_path=manifest_path,
        )


def test_complete_row_recomputes_winner_and_records_robust_top50(
    tmp_path: Path, completed_rows: list[dict]
):
    preregistered, protocol, results, results_path, manifest_path, _ = (
        _preregistered_and_results(tmp_path, completed_rows)
    )

    row = finalizer.build_complete_row(
        preregistered,
        protocol,
        results,
        results_path=results_path,
        prediction_manifest_path=manifest_path,
    )

    _, expected_winner = neighborhood.score_valid_candidates(
        results["all_rows"], protocol["strategy_grid"]
    )
    assert row["state"] == "complete"
    assert row["selected_candidate_id"] == expected_winner["candidate_id"]
    assert row["full_winner_metrics"]["neighbor_ir_p25"] == pytest.approx(
        expected_winner["neighbor_ir_p25"]
    )
    assert row["metrics_summary"]["csi1000"]["ir"] == pytest.approx(
        expected_winner["excess_with_cost_information_ratio"]
    )
    assert len(row["robust_top50"]) == 50
    assert row["robust_top50"][0]["candidate_id"] == expected_winner["candidate_id"]
    assert row["full_result_sha256"] == finalizer.sha256_file(results_path)
    assert row["cleanup_retention_eligible"] is False
    assert "样本外" not in row["note"]
    assert "strategy_neighborhood_report.html" not in json.dumps(row)


def test_complete_row_rejects_non_exact_540_candidate_set(
    tmp_path: Path, completed_rows: list[dict]
):
    preregistered, protocol, results, results_path, manifest_path, _ = (
        _preregistered_and_results(tmp_path, completed_rows)
    )
    results["all_rows"][-1] = copy.deepcopy(results["all_rows"][0])

    with pytest.raises(ValueError, match="exactly 540 unique"):
        finalizer.build_complete_row(
            preregistered,
            protocol,
            results,
            results_path=results_path,
            prediction_manifest_path=manifest_path,
        )


def test_complete_row_rejects_stored_winner_tampering(
    tmp_path: Path, completed_rows: list[dict]
):
    preregistered, protocol, results, results_path, manifest_path, _ = (
        _preregistered_and_results(tmp_path, completed_rows)
    )
    results["winner"] = copy.deepcopy(results["all_rows"][0])

    with pytest.raises(ValueError, match="recomputed full-period winner"):
        finalizer.build_complete_row(
            preregistered,
            protocol,
            results,
            results_path=results_path,
            prediction_manifest_path=manifest_path,
        )


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("protocol", "protocol SHA"),
        ("manifest", "prediction manifest SHA"),
        ("prediction", "prediction file SHA"),
        ("preregistered_prediction_model", "prediction artifact"),
        ("effective_config", "effective config SHA"),
    ],
)
def test_complete_row_rejects_artifact_identity_tampering(
    tmp_path: Path,
    completed_rows: list[dict],
    tamper: str,
    message: str,
):
    preregistered, protocol, results, results_path, manifest_path, prediction_path = (
        _preregistered_and_results(tmp_path, completed_rows)
    )
    if tamper == "protocol":
        results["protocol_sha256"] = "0" * 64
    elif tamper == "manifest":
        preregistered["prediction_manifest_sha256"] = "0" * 64
    elif tamper == "prediction":
        prediction_path.write_bytes(b"tampered prediction")
    elif tamper == "preregistered_prediction_model":
        preregistered["prediction_artifact"]["model_sha256"] = "0" * 64
    else:
        results["all_rows"][0]["effective_config_sha256"] = "0" * 64

    with pytest.raises(ValueError, match=message):
        finalizer.build_complete_row(
            preregistered,
            protocol,
            results,
            results_path=results_path,
            prediction_manifest_path=manifest_path,
        )


def test_complete_row_rejects_base_config_identity_tampering(
    tmp_path: Path, completed_rows: list[dict]
):
    protocol, manifest, protocol_path, manifest_path, _ = _inputs(tmp_path)
    base_copy = tmp_path / "base.yaml"
    base_copy.write_bytes(runner.DEFAULT_BASE_CONFIG.read_bytes())
    protocol["base_config"] = str(base_copy)
    protocol["base_config_sha256"] = finalizer.sha256_file(base_copy)
    _write_json(protocol_path, protocol)
    preregistered = finalizer.build_preregistered_row(
        protocol,
        manifest,
        protocol_path=protocol_path,
        prediction_manifest_path=manifest_path,
    )
    prediction_sha = manifest["predictions"][0]["prediction_sha256"]
    rows = copy.deepcopy(completed_rows)
    for row in rows:
        row["source_pred_sha256"] = prediction_sha
    contract = {
        "protocol_sha256": finalizer.sha256_file(protocol_path),
        "prediction_manifest_sha256": finalizer.sha256_file(manifest_path),
        "base_config_sha256": protocol["base_config_sha256"],
    }
    results = runner.completed_results_payload(
        rows,
        protocol["strategy_grid"],
        protocol_sha256=contract["protocol_sha256"],
        run_contract=contract,
    )
    results_path = tmp_path / "full_results.json"
    _write_json(results_path, results)
    base_copy.write_text(base_copy.read_text() + "\n# tampered\n")

    with pytest.raises(ValueError, match="base config SHA"):
        finalizer.build_complete_row(
            preregistered,
            protocol,
            results,
            results_path=results_path,
            prediction_manifest_path=manifest_path,
        )


def test_registry_transition_intrinsically_enforces_only_two_legal_edges(
    tmp_path: Path,
):
    registry = tmp_path / "registry.jsonl"
    original = '{"exp_id":"baseline/x","state":"baseline"}\n'
    registry.write_text(original, encoding="utf-8")
    preregistered = {
        "exp_id": finalizer.EXP_ID,
        "state": "preregistered",
    }
    finalizer.upsert_registry_transition(registry, preregistered)
    complete = {**preregistered, "state": "complete", "winner": "x"}
    finalizer.upsert_registry_transition(registry, complete)

    assert registry.read_text(encoding="utf-8").startswith(original)
    with pytest.raises(ValueError, match="immutable"):
        finalizer.upsert_registry_transition(
            registry, {**complete, "winner": "changed"}
        )


@pytest.mark.parametrize("state", ["complete", "running", "test_complete", None])
def test_registry_transition_rejects_direct_non_preregistered_insert(
    tmp_path: Path, state: object
):
    registry = tmp_path / "registry.jsonl"

    with pytest.raises(ValueError, match="absent -> preregistered"):
        finalizer.upsert_registry_transition(
            registry, {"exp_id": finalizer.EXP_ID, "state": state}
        )


@pytest.mark.parametrize("state", ["preregistered", "running", "test_complete"])
def test_registry_transition_rejects_invalid_update_from_preregistered(
    tmp_path: Path, state: str
):
    registry = tmp_path / "registry.jsonl"
    finalizer.upsert_registry_transition(
        registry, {"exp_id": finalizer.EXP_ID, "state": "preregistered"}
    )

    with pytest.raises(ValueError, match="preregistered -> complete"):
        finalizer.upsert_registry_transition(
            registry, {"exp_id": finalizer.EXP_ID, "state": state}
        )


def test_cli_has_no_legacy_neighborhood_report_output_target():
    args = finalizer.parse_args(["finalize"])
    assert "strategy_neighborhood_report.html" not in json.dumps(
        vars(args), default=str
    )


def test_full_baseline_comparison_correction_preserves_completed_result(
    tmp_path: Path, completed_rows: list[dict]
):
    preregistered, protocol, results, results_path, manifest_path, _ = (
        _preregistered_and_results(tmp_path, completed_rows)
    )
    completed = finalizer.build_complete_row(
        preregistered,
        protocol,
        results,
        results_path=results_path,
        prediction_manifest_path=manifest_path,
    )

    correction = finalizer.build_full_baseline_comparison_correction(
        completed, results, results_path=results_path
    )

    assert correction["state"] == "correction"
    assert correction["correction_of"] == finalizer.EXP_ID
    assert correction["full_result_sha256"] == finalizer.sha256_file(results_path)
    assert correction["same_run_baseline"]["candidate_id"] == "topk-t30-d2-h20-r095"
    assert correction["robust_winner"]["candidate_id"] == completed[
        "selected_candidate_id"
    ]
    assert correction["selection_rationale"] == "neighbor_ir_p25_not_own_metric"


def test_full_neighborhood_report_shows_same_run_baseline_before_winner(
    tmp_path: Path, completed_rows: list[dict]
):
    preregistered, protocol, results, results_path, manifest_path, _ = (
        _preregistered_and_results(tmp_path, completed_rows)
    )
    completed = finalizer.build_complete_row(
        preregistered,
        protocol,
        results,
        results_path=results_path,
        prediction_manifest_path=manifest_path,
    )
    correction = finalizer.build_full_baseline_comparison_correction(
        completed, results, results_path=results_path
    )

    html = stability_report._full_neighborhood_section(completed, correction)

    assert html.index("topk-t30-d2-h20-r095") < html.index(
        completed["selected_candidate_id"]
    )
    assert "自身指标更弱" in html
    assert "邻域 IR P25" in html
