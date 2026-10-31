"""Consolidated baselines remain references without duplicating experiment rows."""

import hashlib
import json
from pathlib import Path

import pytest

from quant_platform.research import ResearchCatalog
from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, model_doc, row, strategy_doc


def archived_baseline(tmp_path, *, failure=None):
    baseline = row("model/v1", baseline_version="v1", display_name="H20历史基线")
    trial = row("model/v2", baseline_version="v2", baseline_ref=baseline["exp_id"], current_baseline=True)
    newest = row("model/v4", baseline_version="v4")
    bt = row("bt/v1", "strategy", model_ref=baseline["exp_id"], bt_version="v1")
    c = catalog(tmp_path, [trial, newest, bt], [model_doc(), model_doc(), strategy_doc()])
    # A frozen source survives consolidation while the v1 active event is absent.
    source = tmp_path / baseline["eval_output"]
    source.write_text(json.dumps(model_doc()))
    directory = manifest(tmp_path, "study/targets")
    for current in (trial, newest, bt):
        manifest(tmp_path, current["exp_id"], kind="strategy" if current is bt else "model")
    receipt = tmp_path / directory / "baseline-records/v1.json"
    receipt.parent.mkdir()
    receipt.write_text(json.dumps(baseline))
    reference = {"path": "baseline-records/v1.json", "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}
    manifest(tmp_path, "study/targets", historical_baselines=[reference])
    if failure == "missing":
        receipt.unlink()
    elif failure == "sha":
        receipt.write_text(receipt.read_text() + " ")
    elif failure == "not_baseline":
        baseline.pop("baseline_version")
        receipt.write_text(json.dumps(baseline))
        reference["sha256"] = hashlib.sha256(receipt.read_bytes()).hexdigest()
        manifest(tmp_path, "study/targets", historical_baselines=[reference])
    return c


def test_consolidated_v1_is_used_for_v2_control_and_strategy_model_description(tmp_path):
    c = archived_baseline(tmp_path)
    snapshot = c.snapshot()
    records = {r["id"]: r for r in snapshot["records"]}
    assert "model/v1" not in records
    assert records["model/v2"]["current"] and not records["model/v4"]["current"]
    control = records["model/v2"]["core_results"]["records"][0]
    assert control["id"] == "model/v1" and control["metrics"]["net_ann"] == .31
    assert records["bt/v1"]["model_summary"]["version"] == "v1"
    assert records["bt/v1"]["model_summary"]["name"] == "H20历史基线"
    assert snapshot["warnings"] == []


@pytest.mark.parametrize("failure", ["missing", "sha", "not_baseline"])
def test_invalid_historical_source_keeps_control_empty_without_changing_selected_v2(tmp_path, failure):
    c = archived_baseline(tmp_path, failure=failure)
    snapshot = c.snapshot()
    records = {r["id"]: r for r in snapshot["records"]}
    assert records["model/v2"]["current"] and not records["model/v4"]["current"]
    assert "model/v1" not in records
    assert all(v is None for v in records["model/v2"]["core_results"]["records"][0]["metrics"].values())
    assert snapshot["warnings"]


def test_real_current_model_is_v2_and_v1_only_remains_a_historical_reference():
    root = Path(__file__).resolve().parents[2]
    records = {r["id"]: r for r in ResearchCatalog(root).snapshot()["records"]}
    assert len(records) == 23
    assert "regime-adapt/m0-h20-label-v4" not in records
    v2 = records["regime-adapt/m0-h20-t5h5-es-v1"]
    assert v2["current"] and not records["regime-adapt/m0-h20-rankices-v1"]["current"]
    assert v2["core_results"]["records"][0]["metrics"]["net_ann"] == .2933431911523481
    assert records["baseline/phase-m-v1-bt-v1"]["model_summary"]["version"] == "v1"
    assert records["baseline/phase-m-v1-bt-v5"]["current"]
