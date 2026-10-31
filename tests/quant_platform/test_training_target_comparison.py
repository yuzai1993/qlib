"""Reviewed study groups reuse frozen results rather than replacing old metrics."""

import json
import hashlib
from pathlib import Path

import pytest

from quant_platform.research import ResearchCatalog
from backtest.scripts.research_layout import resolve_research_path
from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, model_doc, row


def study(tmp_path):
    parent = row("study/targets", baseline_version=None)
    children = [row(f"target/h{h}", baseline_version=None, train_label_horizon=h) for h in (1, 5, 10, 20)]
    docs = [model_doc() for _ in range(5)]
    for i, doc in enumerate(docs):
        doc["pools"]["all"]["ensemble"]["head"]["3"]["5"]["net_ann"] = i / 10
    c = catalog(tmp_path, [parent, *children], docs)
    descriptor = {"refs": [r["exp_id"] for r in children], "control_ref": children[-1]["exp_id"]}
    for r in children:
        manifest(tmp_path, r["exp_id"])
    directory = manifest(tmp_path, parent["exp_id"], training_target_comparison=descriptor)
    return c, parent, children, tmp_path / directory / "metadata.json"


def test_fixed_target_group_uses_each_authoritative_top3_h5_result(tmp_path):
    c, parent, children, _ = study(tmp_path)
    before = c.registry.read_bytes()
    records = {r["id"]: r for r in c.snapshot()["records"]}
    result = records[parent["exp_id"]]
    group = result["core_results"]
    assert group["status"] == "available"
    assert "Top3×H5" in group["title"]
    assert [r["name"] for r in group["records"]] == ["H1", "H5", "H10", "H20（对照）"]
    assert [r["metrics"]["net_ann"] for r in group["records"]] == [.1, .2, .3, .4]
    assert result["metrics"]["net_ann"] == 0  # The old event remains frozen.
    for r, child in zip(group["records"], children):
        assert r["metrics"] == records[child["exp_id"]]["metrics"]
        assert r["directory"] == records[child["exp_id"]]["directory"]
    assert c.registry.read_bytes() == before


@pytest.mark.parametrize("failure", ["missing", "protocol", "seed", "duplicate_h", "failed", "no_horizon"])
def test_broken_group_has_no_partial_results_or_parent_metric_fallback(tmp_path, failure):
    c, parent, children, _ = study(tmp_path)
    values = [json.loads(line) for line in c.registry.read_text().splitlines()]
    target = values[1]
    if failure == "missing":
        (tmp_path / target["eval_output"]).unlink()
    elif failure == "protocol":
        path = tmp_path / target["eval_output"]
        doc = json.loads(path.read_text())
        doc["cost_round_trip"] = .02
        path.write_text(json.dumps(doc))
    elif failure == "seed":
        target["seeds"] = [42]
    elif failure == "duplicate_h":
        target["train_label_horizon"] = 5
    elif failure == "failed":
        target["state"] = "failed"
    else:
        target.pop("train_label_horizon")
    c.registry.write_text("\n".join(json.dumps(r) for r in values))
    group = next(r for r in c.snapshot()["records"] if r["id"] == parent["exp_id"])["core_results"]
    assert group["status"] == "unavailable"
    assert group["records"] == []
    assert group["warnings"]


@pytest.mark.parametrize("descriptor", [
    {"refs": ["target/a", "target/a"], "control_ref": "target/a"},
    {"refs": ["target/a", "../b"], "control_ref": "target/a"},
    {"refs": ["target/a", "target/b"], "control_ref": "target/missing"},
    {"refs": ["target/a", "target/b"], "control_ref": "target/a", "metrics": {"net_ann": 99}},
])
def test_group_descriptor_cannot_supply_metrics_or_ambiguous_references(tmp_path, descriptor):
    manifest(tmp_path, "study/targets", training_target_comparison=descriptor)
    snapshot = ResearchCatalog(tmp_path).snapshot()
    assert snapshot["records"] == []
    assert "training_target_comparison" in snapshot["sources"]["discovery"]["errors"][0]["message"]


@pytest.mark.parametrize("failure", [None, "sha", "missing", "identity"])
def test_merged_source_receipt_preserves_group_without_restoring_deleted_entity(tmp_path, failure):
    c, parent, children, path = study(tmp_path)
    values = [json.loads(line) for line in c.registry.read_text().splitlines()]
    removed = values.pop(1)
    c.registry.write_text("\n".join(json.dumps(r) for r in values))
    (tmp_path / f"backtest/experiments/models/{removed['exp_id']}/metadata.json").unlink()
    receipt = path.parent / "source-records/h1.json"
    receipt.parent.mkdir()
    if failure == "identity":
        removed["exp_id"] = "target/unrelated"
    receipt.write_text(json.dumps(removed))
    metadata = json.loads(path.read_text())
    metadata["training_target_comparison"]["archived_records"] = [{
        "path": "source-records/h1.json", "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}]
    path.write_text(json.dumps(metadata))
    if failure == "missing":
        receipt.unlink()
    elif failure == "sha":
        receipt.write_text(receipt.read_text() + " ")
    snapshot = c.snapshot()
    assert removed["exp_id"] not in {r["id"] for r in snapshot["records"]}
    group = next(r for r in snapshot["records"] if r["id"] == parent["exp_id"])["core_results"]
    if failure:
        assert group["status"] == "unavailable" and group["records"] == []
    else:
        assert group["status"] == "available" and len(group["records"]) == 4
        assert group["records"][0]["metrics"]["net_ann"] == .1


def test_real_label_study_uses_v3_h20_and_preserves_historical_grid():
    root = Path(__file__).resolve().parents[2]
    c = ResearchCatalog(root)
    records = {r["id"]: r for r in c.snapshot()["records"]}
    result = records["regime-adapt/m0-label-k123-h2345"]
    assert result["experiment_type"] == "training_target"
    assert result["directory"] == "backtest/experiments/models/training-target/horizon-comparison"
    group = result["core_results"]
    assert group["status"] == "available"
    assert [r["metrics"]["net_ann"] for r in group["records"]] == [
        -.3979450067859733, .24768385502744275, .24370402110020561, .2969937650578606]
    assert max(group["records"], key=lambda r: r["metrics"]["net_ann"])["name"] == "H20（对照）"
    assert [r["metrics"]["rank_ic_mean"] for r in group["records"]][:3] == [None] * 3
    resource_paths = {c.resource(resource["id"]) for resource in result["resources"]
                      if resource["kind"] == "metrics" and resource["available"]}
    assert {resolve_research_path(r["source"], root) for r in group["records"]} <= resource_paths
    assert result["metrics"]["net_ann"] == .2933431911523481
    assert records["regime-adapt/m0-h20-t5h5-es-v1"]["current"]
    assert not records["regime-adapt/m0-h20-rankices-v1"]["current"]
