"""Retired scores remain historical evidence and cannot silently run again."""

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import phase_s_prediction_validation as validation
import prepare_strategy_stability_predictions as prepare
import run_strategy_neighborhood_full as neighborhood
import run_strategy_sweep as sweep


def retired_manifest(tmp_path):
    entry = {"model_ref": "b6-m", "pool": "csi1000", "segment": "full",
             "path": str(tmp_path / "deleted.pkl"), "prediction_sha256": "a" * 64,
             "status": "retired", "retired_at": "2026-11-01"}
    manifest = {"schema_version": 1, "data_version": "2026-07-31",
                "status": "retired", "retired_at": "2026-11-01", "predictions": [entry]}
    path = tmp_path / "prediction_manifest.json"
    path.write_text(json.dumps(manifest))
    return manifest, path, entry


@pytest.mark.parametrize("entrypoint", ["entry", "artifact", "manifest"])
def test_retired_input_fails_before_model_or_pickle_reads(tmp_path, monkeypatch, entrypoint):
    manifest, path, entry = retired_manifest(tmp_path)
    monkeypatch.setattr(validation.pd, "read_pickle", lambda *_: pytest.fail("retired pickle read"))
    monkeypatch.setattr(validation, "load_frozen_model", lambda *_: pytest.fail("retired model load"))
    with pytest.raises(ValueError, match="2026-11-01.*CSI1000.*显式提供重新验证"):
        if entrypoint == "entry":
            validation.full_prediction_entry(manifest)
        elif entrypoint == "artifact":
            validation.validate_prediction_artifact(entry, Path(entry["path"]))
        else:
            validation.validate_full_prediction_manifest(manifest, path, authoritative_manifest_path=path)


def test_explicit_new_manifest_and_prediction_still_validate(tmp_path):
    frame = pd.DataFrame({"score": [.1, .2]}, index=pd.MultiIndex.from_arrays(
        [pd.to_datetime(["2020-01-13", "2026-07-31"]), ["SH600000", "SH600000"]],
        names=["datetime", "instrument"]))
    pred = tmp_path / "new.pkl"
    frame.to_pickle(pred)
    model = ROOT / "backtest/models/baselines/b6-m/seed4000/trained_model"
    entry = {"model_ref": "b6-m", "pool": "csi1000", "segment": "full", "data_version": "2026-07-31",
             "path": str(pred), "prediction_sha256": hashlib.sha256(pred.read_bytes()).hexdigest(),
             "model_path": str(model), "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
             "coverage": validation.prediction_coverage(frame)}
    manifest = {"schema_version": 1, "data_version": "2026-07-31", "predictions": [entry]}
    path = tmp_path / "new_manifest.json"
    path.write_text(json.dumps(manifest))
    verified, coverage = validation.validate_full_prediction_manifest(
        manifest, path, repo_root=ROOT, authoritative_manifest_path=path, expected_coverage=entry["coverage"])
    assert verified == entry
    assert coverage == entry["coverage"]


@pytest.mark.parametrize("runner", ["sweep", "neighborhood"])
def test_strategy_runners_report_retirement_before_workflow_or_pickle(tmp_path, monkeypatch, runner):
    _, path, entry = retired_manifest(tmp_path)
    monkeypatch.setattr(validation.pd, "read_pickle", lambda *_: pytest.fail("retired pickle read"))
    monkeypatch.setattr(validation, "load_frozen_model", lambda *_: pytest.fail("retired model load"))
    with pytest.raises(ValueError, match="2026-11-01.*CSI1000"):
        if runner == "sweep":
            sweep.main(["--pred", entry["path"], "--prediction-manifest", str(path),
                        "--config", "unused.yaml", "--model-ref", "b6-m"])
        else:
            neighborhood.main(["--prediction-manifest", str(path), "--prepare-only"])


def test_prepare_cli_requires_explicit_output_root():
    with pytest.raises(SystemExit) as error:
        prepare.parse_args([])
    assert error.value.code == 2


def test_prepare_cannot_overwrite_a_retired_output_root(tmp_path, monkeypatch):
    _, path, _ = retired_manifest(tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr(prepare.pd, "read_pickle", lambda *_: pytest.fail("retired pickle read"))
    with pytest.raises(ValueError, match="2026-11-01.*CSI1000"):
        prepare.prepare_bundles(tmp_path / "missing-source.json", tmp_path, ["b6-m"])
    assert path.read_bytes() == original
    assert not (tmp_path / "predictions").exists()


def test_prepare_accepts_explicit_fresh_output_and_keeps_its_manifest_active(tmp_path):
    entries = []
    for segment, date in (("valid", "2020-01-13"), ("test", "2026-07-31")):
        frame = pd.DataFrame({"score": [.1]}, index=pd.MultiIndex.from_arrays(
            [pd.to_datetime([date]), ["SH600000"]], names=["datetime", "instrument"]))
        path = tmp_path / f"{segment}.pkl"
        frame.to_pickle(path)
        entries.append({"model_ref": "b6-m", "pool": "csi1000", "segment": segment,
                        "path": str(path), "prediction_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "model_path": "frozen-model", "model_sha256": "b" * 64,
                        "coverage": validation.prediction_coverage(frame)})
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"data_version": "2026-07-31", "predictions": entries}))
    output = tmp_path / "new-explicit-output"
    manifest = prepare.prepare_bundles(source, output, ["b6-m"])
    assert manifest.get("status") != "retired"
    assert manifest["predictions"][0]["coverage"]["n_rows"] == 2
    assert (output / "predictions/b6-m/csi1000_full.pkl").is_file()


def test_repository_retirement_keeps_the_model_and_historical_prediction_identity():
    path = ROOT / "backtest/experiments/_shared/phase-s/prediction_manifest.json"
    manifest = json.loads(path.read_text())
    assert manifest["status"] == "retired" and manifest["retired_at"] == "2026-11-01"
    entry = manifest["predictions"][0]
    assert entry["status"] == "retired"
    assert entry["prediction_sha256"] == "951f5fa34fd5641217041edf30fc549931bc1fb07e5d5fdacefa7573bee4ae1f"
    assert not (ROOT / "backtest/experiments/_shared/phase-s/predictions/b6-m/csi1000_full.pkl").exists()
    model = ROOT / entry["model_path"]
    assert hashlib.sha256(model.read_bytes()).hexdigest() == entry["model_sha256"]
