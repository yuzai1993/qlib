"""Archive discovery is independent of registry authority and rejects ambiguity."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backtest/scripts"))


def manifest(root, folder="models/group/arm", **changes):
    path = root / "backtest/experiments" / folder / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"schema_version": 2, "entity_type": "experiment", "exp_id": "group/arm",
             "kind": "model", "track": "csi1000", "title": "An experiment", **changes}
    path.write_text(json.dumps(value))
    return path


def test_discovery_retains_editorial_metadata_and_ignores_system_roots(tmp_path):
    import experiment_archive as archive
    manifest(tmp_path, description="Editorial", metrics_source={"path": "metrics/a.json"})
    manifest(tmp_path, "_shared/ignored", exp_id="ignored")
    result = archive.scan_experiment_archives(tmp_path)
    assert result["mode"] == "typed" and not result["errors"]
    assert len(result["experiments"]) == 1
    row = result["experiments"][0]
    assert row["description"] == "Editorial"
    assert row["directory"] == "backtest/experiments/models/group/arm"
    assert row["metadata_path"].endswith("models/group/arm/metadata.json")
    assert archive.archive_directory(tmp_path, "group/arm") == tmp_path / row["directory"]


def test_category_manifest_preserves_stable_id_and_declares_its_physical_directory(tmp_path):
    import experiment_archive as archive
    relative = "backtest/experiments/models/training-target/h1"
    manifest(tmp_path, "models/training-target/h1", archive_path=relative,
             experiment_type="training_target", analysis={"conclusion": "", "limitations": "",
                 "evidence": ["metrics/eval.json"], "review_status": "draft", "updated_at": None})
    result = archive.scan_experiment_archives(tmp_path)
    assert not result["errors"] and result["experiments"][0]["exp_id"] == "group/arm"
    assert result["experiments"][0]["directory"] == relative
    assert archive.archive_directory(tmp_path, "group/arm", "model") == tmp_path / relative
    assert archive.EXPERIMENT_TYPES == {"training_target": "训练目标", "early_stopping": "早停方法",
        "model_structure": "模型结构", "features": "特征", "evaluation_method": "评估方法",
        "strategy_backtest": "策略回测", "uncategorized": "未分类"}


@pytest.mark.parametrize("changes", [
    {}, {"archive_path": None}, {"archive_path": "../escape"},
    {"archive_path": "backtest/experiments/strategies/training-target/h1"},
    {"archive_path": "backtest/experiments/models/other/h1"},
    {"archive_path": "backtest/experiments/models"},
])
def test_category_directory_requires_matching_safe_kind_local_declaration(tmp_path, changes):
    import experiment_archive as archive
    manifest(tmp_path, "models/training-target/h1", **changes)
    result = archive.scan_experiment_archives(tmp_path)
    assert not result["experiments"] and result["errors"]


@pytest.mark.parametrize("changes", [
    {"experiment_type": None}, {"experiment_type": []}, {"experiment_type": "unknown"},
    {"analysis": None}, {"analysis": []}, {"analysis": {"conclusion": None}},
    {"analysis": {"limitations": []}}, {"analysis": {"evidence": None}},
    {"analysis": {"evidence": [1]}}, {"analysis": {"evidence": ["../outside.md"]}},
    {"analysis": {"review_status": []}}, {"analysis": {"review_status": "current"}},
    {"analysis": {"updated_at": 1}}, {"analysis": {"updated_at": "2026-02-30"}},
    {"analysis": {"current": True}},
])
def test_editorial_category_fields_are_strict_and_cannot_define_authority(tmp_path, changes):
    import experiment_archive as archive
    manifest(tmp_path, **changes)
    result = archive.scan_experiment_archives(tmp_path)
    assert not result["experiments"] and result["errors"]


@pytest.mark.parametrize("updated", [None, "", "2026-10-31", "2026-10-31T08:30:00Z"])
def test_analysis_accepts_empty_or_valid_date_strings(tmp_path, updated):
    import experiment_archive as archive
    manifest(tmp_path, experiment_type="evaluation_method", analysis={"updated_at": updated})
    assert not archive.scan_experiment_archives(tmp_path)["errors"]


def test_duplicate_exp_id_remains_invalid_across_physical_categories(tmp_path):
    import experiment_archive as archive
    manifest(tmp_path)
    manifest(tmp_path, "models/training-target/h1",
             archive_path="backtest/experiments/models/training-target/h1")
    result = archive.scan_experiment_archives(tmp_path)
    assert not result["experiments"] and len(result["errors"]) == 2
    with pytest.raises(ValueError, match="duplicate"):
        archive.archive_directory(tmp_path, "group/arm", "model")


def test_analysis_evidence_cannot_follow_an_archive_internal_symlink(tmp_path):
    import experiment_archive as archive
    path = manifest(tmp_path, analysis={"evidence": ["docs/link.md"]})
    asset = path.parent / "docs/link.md"
    asset.parent.mkdir()
    outside = tmp_path / "source.md"
    outside.write_text("evidence")
    asset.symlink_to(outside)
    assert archive.scan_experiment_archives(tmp_path)["errors"]


@pytest.mark.parametrize("change", [{"exp_id": "../escape"}, {"kind": "strategy"},
    {"metrics_source": {"path": "../escape.json"}}, {"metrics_source": {"path": "/tmp/a"}},
    {"schema_version": 1}, {"entity_type": "shared"}])
def test_discovery_invalid_manifests_are_not_offered(tmp_path, change):
    import experiment_archive as archive
    manifest(tmp_path, **change)
    result = archive.scan_experiment_archives(tmp_path)
    assert not result["experiments"] and result["errors"]
    with pytest.raises(ValueError):
        archive.archive_directory(tmp_path, "group/arm")


@pytest.mark.parametrize("field", ["configs", "reports", "docs", "notes", "data", "metrics",
    "records", "shared_assets", "recipe_sources", "recipe_session_metadata", "archived"])
@pytest.mark.parametrize("invalid", [None, 1, True, "asset.json", {"path": "asset.json"}])
def test_inventory_fields_require_lists_and_keep_other_archives_visible(tmp_path, field, invalid):
    import experiment_archive as archive
    manifest(tmp_path, **{field: invalid})
    manifest(tmp_path, "models/group/good", exp_id="group/good")
    result = archive.scan_experiment_archives(tmp_path)
    assert [row["exp_id"] for row in result["experiments"]] == ["group/good"]
    assert len(result["errors"]) == 1 and field in result["errors"][0]["message"]


@pytest.mark.parametrize("invalid", [None, 1, True, [], {}, {"path": None}, {"path": 1}])
def test_inventory_entries_require_local_paths(tmp_path, invalid):
    import experiment_archive as archive
    manifest(tmp_path, data=[invalid])
    result = archive.scan_experiment_archives(tmp_path)
    assert not result["experiments"] and result["errors"]


@pytest.mark.parametrize("invalid", [1, None, {}, {"sha256": "a" * 64}])
def test_declared_metrics_source_requires_a_local_path(tmp_path, invalid):
    import experiment_archive as archive
    manifest(tmp_path, metrics_source=invalid)
    result = archive.scan_experiment_archives(tmp_path)
    assert not result["experiments"] and result["errors"]


def test_duplicate_ids_are_all_excluded_even_if_one_directory_disagrees(tmp_path):
    import experiment_archive as archive
    manifest(tmp_path)
    manifest(tmp_path, "strategies/other/name", kind="strategy")
    result = archive.scan_experiment_archives(tmp_path)
    assert not result["experiments"]
    assert any("duplicate" in error["message"] for error in result["errors"])


def test_symlink_and_oversize_metadata_are_not_read(tmp_path):
    import experiment_archive as archive
    path = manifest(tmp_path)
    target = tmp_path / "target.json"
    path.rename(target)
    path.symlink_to(target)
    assert archive.scan_experiment_archives(tmp_path)["errors"]
    path.unlink()
    path.write_bytes(b" " * (archive.MAX_METADATA_BYTES + 1))
    assert archive.scan_experiment_archives(tmp_path)["errors"]


def test_metadata_read_is_bounded_even_when_file_size_stat_is_stale(tmp_path, monkeypatch):
    import experiment_archive as archive
    path = manifest(tmp_path)
    path.write_text(path.read_text() + " " * archive.MAX_METADATA_BYTES)
    real_stat = Path.stat

    def stale_stat(value, *args, **kwargs):
        result = real_stat(value, *args, **kwargs)
        if value == path:
            fields = list(result)
            fields[6] = 0
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(Path, "stat", stale_stat)
    result = archive.scan_experiment_archives(tmp_path)
    assert not result["experiments"] and result["errors"]


def test_repository_inventory_boundary_and_metrics_symlink_are_checked(tmp_path):
    import experiment_archive as archive
    path = manifest(tmp_path, inventory_kind="asset_snapshot", data=["../escape"])
    assert archive.scan_experiment_archives(tmp_path)["errors"]
    path = manifest(tmp_path, metrics_source={"path": "metrics/a.json"})
    (path.parent / "metrics").symlink_to(tmp_path, target_is_directory=True)
    assert archive.scan_experiment_archives(tmp_path)["errors"]


def test_malformed_track_and_symlinked_experiments_root_are_reported(tmp_path):
    import experiment_archive as archive
    manifest(tmp_path, track=[])
    assert archive.scan_experiment_archives(tmp_path)["errors"]
    base = tmp_path / "backtest/experiments"
    target = tmp_path / "target"
    base.rename(target)
    base.symlink_to(target, target_is_directory=True)
    result = archive.scan_experiment_archives(tmp_path)
    assert result["errors"] and not result["experiments"]


@pytest.mark.parametrize("entry_type", ["directory", "fifo"])
def test_nonregular_metadata_is_rejected_without_blocking(tmp_path, entry_type):
    path = manifest(tmp_path)
    path.unlink()
    if entry_type == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    scripts = str(Path(__file__).resolve().parents[2] / "backtest/scripts")
    code = "import sys,json;sys.path.insert(0,sys.argv[1]);import experiment_archive as a;print(json.dumps(a.scan_experiment_archives(sys.argv[2])))"
    process = subprocess.run([sys.executable, "-c", code, scripts, str(tmp_path)],
                             capture_output=True, text=True, timeout=2, check=True)
    result = json.loads(process.stdout)
    assert not result["experiments"] and result["errors"]


def test_symlinked_backtest_ancestor_is_rejected(tmp_path):
    import experiment_archive as archive
    manifest(tmp_path)
    base = tmp_path / "backtest"
    target = tmp_path / "other"
    base.rename(target)
    base.symlink_to(target, target_is_directory=True)
    result = archive.scan_experiment_archives(tmp_path)
    assert not result["experiments"] and result["errors"]


def test_metadata_refresh_merges_owned_assets_and_preserves_dependencies(tmp_path):
    import experiment_archive as archive
    import hashlib
    path = manifest(tmp_path, inventory_kind="asset_snapshot", title="Editorial title",
        configs=[{"path": "backtest/experiments/_shared/config.yaml", "shared": True, "sha256": "a" * 64}],
        reports=["backtest/experiments/reports/hub.html"],
        data=["backtest/experiments/_shared/source.json"], description="Keep this")
    for relative, payload in (("configs/a.yaml", "config"), ("data/source.json", "{}"),
                              ("metrics/eval.json", "{}"), ("docs/note.md", "note")):
        asset = path.parent / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text(payload)
    value = archive.ensure_experiment_metadata(tmp_path, {"exp_id": "group/arm", "phase": "M",
        "portable_eval_output": "backtest/experiments/models/group/arm/metrics/eval.json"})
    assert value["title"] == "Editorial title" and value["description"] == "Keep this"
    assert value["configs"][0]["shared"] is True
    actual_config = next(item for item in value["configs"] if item["path"].endswith("configs/a.yaml"))
    assert actual_config["sha256"] == hashlib.sha256(b"config").hexdigest()
    assert "backtest/experiments/reports/hub.html" in value["reports"]
    assert "backtest/experiments/_shared/source.json" in value["data"]
    assert "backtest/experiments/models/group/arm/data/source.json" in value["data"]
    assert "backtest/experiments/models/group/arm/docs/note.md" in value["docs"]
    assert value["metrics_source"]["path"] == "metrics/eval.json"


@pytest.mark.parametrize("new_path", ["metrics/first.json", "metrics/second.json"])
def test_formal_refresh_updates_latest_metrics_source_and_hash(tmp_path, new_path):
    import experiment_archive as archive
    import hashlib
    path = manifest(tmp_path, metrics_source={"path": "metrics/first.json", "sha256": "0" * 64})
    asset = path.parent / new_path
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b'{"new":2}')
    row = {"exp_id": "group/arm", "phase": "M", "portable_eval_output":
           "backtest/experiments/models/group/arm/" + new_path}
    metadata = archive.ensure_experiment_metadata(tmp_path, row)
    assert metadata["metrics_source"] == {"path": new_path, "sha256": hashlib.sha256(b'{"new":2}').hexdigest()}


@pytest.mark.parametrize("portable", [None, "", {}, "backtest/experiments/_shared/source.json",
    "backtest/experiments/models/group/arm/metrics/../unsafe.json"])
def test_authoritative_portable_source_never_falls_back_to_old_eval_output(tmp_path, portable):
    import experiment_archive as archive
    path = manifest(tmp_path, metrics_source={"path": "metrics/old.json", "sha256": "0" * 64})
    fallback = path.parent / "metrics/fallback.json"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("{}")
    metadata = archive.ensure_experiment_metadata(tmp_path, {"exp_id": "group/arm", "phase": "M",
        "portable_eval_output": portable,
        "eval_output": "backtest/experiments/models/group/arm/metrics/fallback.json"})
    assert "metrics_source" not in metadata


def test_authoritative_missing_local_source_clears_hash_and_keeps_declared_path(tmp_path):
    import experiment_archive as archive
    manifest(tmp_path, metrics_source={"path": "metrics/missing.json", "sha256": "0" * 64})
    metadata = archive.ensure_experiment_metadata(tmp_path, {"exp_id": "group/arm", "phase": "M",
        "portable_eval_output": "backtest/experiments/models/group/arm/metrics/missing.json",
        "eval_output": "backtest/experiments/models/group/arm/metrics/fallback.json"})
    assert metadata["metrics_source"] == {"path": "metrics/missing.json"}


def test_formal_refresh_converts_local_inventory_to_repository_scope(tmp_path):
    import experiment_archive as archive
    path = manifest(tmp_path, reports=["backtest/report.html"],
        configs=[{"path": "configs/a.yaml", "sha256": "a" * 64}],
        sources={"nested": [{"role": "source", "path": "docs/source.md"}]})
    report = path.parent / "backtest/report.html"
    report.parent.mkdir(parents=True)
    report.write_text("local report")
    metadata = archive.ensure_experiment_metadata(tmp_path, {"exp_id": "group/arm", "phase": "M"})
    assert metadata["inventory_kind"] == "asset_snapshot"
    assert metadata["reports"] == ["backtest/experiments/models/group/arm/backtest/report.html"]
    assert metadata["configs"][0]["path"] == "backtest/experiments/models/group/arm/configs/a.yaml"
    assert metadata["sources"]["nested"][0]["path"] == "backtest/experiments/models/group/arm/docs/source.md"
    assert not archive.scan_experiment_archives(tmp_path)["errors"]


def test_local_inventory_conversion_preserves_resource_annotations(tmp_path):
    import experiment_archive as archive
    labels = ["baseline", "reviewed"]
    annotations = {"path": "editorial source description", "values": ["first", "second"]}
    manifest(tmp_path, configs=[{"path": "configs/a.yaml", "labels": labels, "annotations": annotations}])
    metadata = archive.ensure_experiment_metadata(tmp_path, {"exp_id": "group/arm", "phase": "M"})
    config = metadata["configs"][0]
    assert config["path"] == "backtest/experiments/models/group/arm/configs/a.yaml"
    assert config["labels"] == labels
    assert config["annotations"] == annotations


@pytest.mark.parametrize("row, expected", [
    ({"phase": "S"}, ("strategy", "csi1000")),
    ({"phase": "M", "report_kind": "phase_m_v1_bt_baseline"}, ("strategy", "all_a")),
    ({"phase_m_protocol": "v1"}, ("model", "all_a")),
    ({"exp_id": "diag/eval-window-edge-lookahead", "report_kind": "eval_protocol_diag",
      "baseline_ref": "baseline/phase-m-v1-v2"}, ("model", "all_a")),
    ({"phase": "M"}, ("model", "csi1000")),
])
def test_historical_classification_follows_research_protocol(row, expected):
    import experiment_archive as archive
    actual = archive.classify_experiment(row)
    assert (actual["kind"], actual["track"]) == expected
