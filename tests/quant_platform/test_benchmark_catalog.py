"""Archived model versions can be benchmarks without duplicate experiments."""

import hashlib
import json
from pathlib import Path

import pytest

from quant_platform.research import ResearchCatalog
from tests.quant_platform.test_historical_baseline_sources import archived_baseline


def profile(c, *, corrupt=False):
    directory = c.root / "backtest/experiments/models/study/targets"
    path = directory / "baseline-profiles/v1.json"
    path.parent.mkdir()
    payload = {"exp_id": "model/v1", "kind": "model", "track": "all_a", "title": "历史H20",
               "model_profile": {"structure": "单LightGBM", "features": "Alpha158+range",
                                 "early_stopping": "499天次日RankIC", "training_target": "H20"},
               "current": True, "baseline_version": "v99", "metrics": {"net_ann": 999}}
    path.write_text(json.dumps(payload))
    metadata_path = directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["historical_baselines"][0]["profile"] = {
        "path": "baseline-profiles/v1.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    metadata["benchmark_outputs"] = [{"ref": "model/v1", "version": "v1"}]
    metadata_path.write_text(json.dumps(metadata))
    if corrupt:
        path.write_text(path.read_text() + " ")
    return metadata_path


def test_historical_benchmark_has_original_metrics_and_recipe_without_active_duplicate(tmp_path):
    c = archived_baseline(tmp_path)
    profile(c)
    raw_registry = c.registry.read_bytes()
    s = c.snapshot()
    benchmark = next(r for r in s["benchmarks"] if r["id"] == "model/v1")
    assert benchmark["version"] == "v1" and benchmark["metrics"]["net_ann"] == .31
    assert benchmark["benchmark_only"] and not benchmark["current"]
    assert benchmark["model_profile"]["early_stopping"] == "499天次日RankIC"
    assert benchmark["model_profile"]["features"] == "Alpha158+range"
    assert benchmark["model_profile"]["version"] == "v1"
    assert "model/v1" not in {r["id"] for r in s["records"]}
    study = next(r for r in s["records"] if r["id"] == "study/targets")
    assert study["benchmark_outputs"] == [{"ref": "model/v1", "version": "v1", "available": True}]
    assert study["promoted_version"] == ""
    assert [r["id"] for r in s["benchmarks"] if r["current"] and r["kind"] == "model"] == ["model/v2"]
    assert c.registry.read_bytes() == raw_registry


def test_bad_recipe_hash_does_not_fabricate_recipe_or_change_verified_benchmark_numbers(tmp_path):
    c = archived_baseline(tmp_path)
    profile(c, corrupt=True)
    s = c.snapshot()
    benchmark = next(r for r in s["benchmarks"] if r["id"] == "model/v1")
    assert benchmark["metrics"]["net_ann"] == .31
    assert benchmark["model_profile"]["early_stopping"] == ""
    assert benchmark["warnings"]
    assert next(r for r in s["records"] if r["id"] == "model/v2")["current"]


def test_malformed_profile_category_cannot_crash_the_catalog(tmp_path):
    c = archived_baseline(tmp_path)
    metadata_path = profile(c)
    metadata = json.loads(metadata_path.read_text())
    path = metadata_path.parent / "baseline-profiles/v1.json"
    data = json.loads(path.read_text())
    data["experiment_type"] = {"invalid": "category"}
    path.write_text(json.dumps(data))
    metadata["historical_baselines"][0]["profile"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata_path.write_text(json.dumps(metadata))
    snapshot = c.snapshot()
    benchmark = next(r for r in snapshot["benchmarks"] if r["id"] == "model/v1")
    assert benchmark["metrics"]["net_ann"] == .31
    assert benchmark["experiment_type"] == "uncategorized"


@pytest.mark.parametrize("ref,version", [("model/missing", "v1"), ("model/v1", "v99")])
def test_output_marker_requires_a_real_matching_benchmark(tmp_path, ref, version):
    c = archived_baseline(tmp_path)
    path = profile(c)
    d = json.loads(path.read_text())
    d["benchmark_outputs"] = [{"ref": ref, "version": version}]
    path.write_text(json.dumps(d))
    study = next(r for r in c.snapshot()["records"] if r["id"] == "study/targets")
    assert study["benchmark_outputs"] == [{"ref": ref, "version": version, "available": False}]
    assert study["promoted_version"] == "" and study["warnings"]


def test_real_benchmark_v1_is_restored_and_study_marks_its_output():
    root = Path(__file__).resolve().parents[2]
    snapshot = ResearchCatalog(root).snapshot()
    assert len(snapshot["records"]) == 23
    v1 = next(r for r in snapshot["benchmarks"] if r["id"] == "regime-adapt/m0-h20-label-v4")
    assert v1["version"] == "v1" and v1["metrics"]["net_ann"] == .2933431911523481
    assert "499" in v1["model_profile"]["early_stopping"]
    assert not v1["current"]
    assert {r["version"] for r in snapshot["benchmarks"] if r["kind"] == "model"} == {"v1", "v2", "v3", "v4"}
    study = next(r for r in snapshot["records"] if r["id"] == "regime-adapt/m0-label-k123-h2345")
    assert study["benchmark_outputs"] == [{"ref": v1["id"], "version": "v1", "available": True}]
    assert study["core_results"]["records"][-1]["metrics"]["net_ann"] == .2969937650578606
