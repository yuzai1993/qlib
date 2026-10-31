"""Lossless registry indexing and fail-closed detail reads."""

import hashlib
import json
import math
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backtest/scripts"))

import experiment_registry as registry


def rows():
    return [
        {"exp_id": "same", "state": "preregistered", "seeds": [42, 1000]},
        {"exp_id": "same", "state": "completed", "metrics_summary": {"x": [0.125, -0.0, 7, 7.0]}, "中文": "保留"},
    ]


def test_index_roundtrip_preserves_order_duplicates_types_and_values(tmp_path):
    path = tmp_path / "registry.jsonl"
    registry.write_registry(path, rows(), indexed=True)
    index = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(index) == 2
    assert "metrics_summary" not in index[1]
    assert index[0]["detail_path"] != index[1]["detail_path"]
    assert registry.load_registry(path) == rows()
    values = registry.load_registry(path)[1]["metrics_summary"]["x"]
    assert math.copysign(1, values[1]) == -1
    assert type(values[2]) is int and type(values[3]) is float
    registry.append_registry(path, {"exp_id": "new", "metrics": {"x": 0.3}})
    assert all("detail_path" in json.loads(line) for line in path.read_text().splitlines())
    registry.write_registry(path, registry.load_registry(path))
    assert len(registry.load_registry(path)) == 3


def test_legacy_and_mixed_registry_compatibility(tmp_path):
    path = tmp_path / "registry.jsonl"
    registry.write_registry(path, rows())
    original = path.read_bytes()
    assert "detail_path" not in json.loads(path.read_text().splitlines()[0])
    registry.append_registry(path, {"exp_id": "new"})
    assert path.read_bytes().startswith(original)
    indexed = registry.encode_registry_row(path, {"exp_id": "external"}, indexed=True)
    path.write_text(path.read_text() + indexed + "\n")
    assert [r["exp_id"] for r in registry.load_registry(path)] == ["same", "same", "new", "external"]
    registry.append_registry(path, {"exp_id": "later"})
    assert "detail_path" in json.loads(path.read_text().splitlines()[-1])


def _write_v1_event(path, row):
    payload = (json.dumps(row, ensure_ascii=False, indent=2) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    detail = path.parent / "registry_records" / f"{digest}.json"
    detail.parent.mkdir(parents=True)
    detail.write_bytes(payload)
    index = {
        "_registry_schema": "qlib-experiment-index-v1",
        "exp_id": row["exp_id"],
        "state": row["state"],
        "detail_path": f"registry_records/{digest}.json",
        "detail_sha256": digest,
    }
    path.write_text(json.dumps(index) + "\n")
    return index


def test_v1_reader_and_mixed_v1_v2_history_are_compatible(tmp_path):
    path = tmp_path / "registry.jsonl"
    row = {"exp_id": "group/experiment", "state": "preregistered", "metrics": {"score": 1.0}}
    _write_v1_event(path, row)
    original = path.read_bytes()
    assert registry.load_registry(path) == [row]

    completed = {**row, "state": "completed", "metrics": {"score": 2.0}}
    registry.append_registry(path, completed)
    assert path.read_bytes().startswith(original)
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert entries[1]["_registry_schema"] == "qlib-experiment-index-v2"
    assert entries[1]["detail_path"].startswith("group/experiment/records/")
    assert registry.load_registry(path) == [row, completed]
    registry.write_registry(path, registry.load_registry(path))
    assert all(json.loads(line)["_registry_schema"] == "qlib-experiment-index-v2" for line in path.read_text().splitlines())
    assert registry.load_registry(path) == [row, completed]


def test_v1_metadata_consistency_stays_strict(tmp_path):
    path = tmp_path / "registry.jsonl"
    index = _write_v1_event(path, {"exp_id": "old", "state": "completed", "metrics": {"score": 1.0}})
    index["state"] = "preregistered"
    path.write_text(json.dumps(index) + "\n")
    with pytest.raises(ValueError, match="metadata"):
        registry.load_registry(path)


def test_v2_groups_each_experiments_events_in_its_own_records_directory(tmp_path):
    path = tmp_path / "registry.jsonl"
    events = [
        {"exp_id": "direction/arm-a", "state": "preregistered"},
        {"exp_id": "direction/arm-b", "metrics": {"score": -0.0}},
        {"exp_id": "direction/arm-a", "state": "completed", "metrics": {"score": 1.0}},
    ]
    registry.write_registry(path, events, indexed=True)
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    for row, index in zip(events, entries):
        assert index["_registry_schema"] == "qlib-experiment-index-v2"
        assert index["detail_path"] == f'{row["exp_id"]}/records/{index["detail_sha256"]}.json'
    assert len(list((tmp_path / "direction/arm-a/records").glob("*.json"))) == 2
    assert not (tmp_path / "registry_records").exists()
    assert registry.load_registry(path) == events


@pytest.mark.parametrize("exp_id", [None, 1, "", ".", "..", "/root", "a/..", "../a", "a/./b", "a//b", "a/", "a\\b", "a/\x00b", "a b", "a:b", ".hidden"])
def test_v2_rejects_unsafe_experiment_ids(tmp_path, exp_id):
    path = tmp_path / "registry.jsonl"
    with pytest.raises(ValueError, match="exp_id"):
        registry.encode_registry_row(path, {"exp_id": exp_id}, indexed=True)
    assert not path.exists()
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("ancestor", ["group", "group/experiment", "group/experiment/records"])
def test_v2_rejects_symlinked_ancestors_on_read_and_write(tmp_path, ancestor):
    path = tmp_path / "registry.jsonl"
    row = {"exp_id": "group/experiment", "metrics": {"score": 1.0}}
    registry.write_registry(path, [row], indexed=True)
    source = tmp_path / ancestor
    target = tmp_path / "unrelated-target"
    source.rename(target)
    source.symlink_to(target, target_is_directory=True)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="symlink"):
        registry.load_registry(path)
    with pytest.raises(ValueError, match="symlink"):
        registry.encode_registry_row(path, row, indexed=True)
    assert path.read_bytes() == before


def test_future_dynamic_writes_canonicalize_only_exact_asset_paths(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    catalog = path.parent / "_catalog"
    catalog.mkdir(parents=True)
    legacy_report = "backtest/experiments/future_report.html"
    legacy_config = "backtest/configs/group/arm.yaml"
    report = "backtest/experiments/group/arm/reports/report.html"
    config = "backtest/experiments/group/arm/configs/config.yaml"
    (catalog / "path_map.json").write_text(json.dumps({
        "schema_version": 1,
        "paths": {legacy_report: report, legacy_config: config},
    }))
    row = {
        "exp_id": "group/arm",
        "detail_report": f"backtest/experiments/{'future_report'}.html",
        "configs": [legacy_config],
        "nested": {"source": str(tmp_path / "repo" / legacy_report)},
        "eval_protocol": f"Read {legacy_config}; preserve protocol text",
        "metrics": {"int": 1, "float": 1.0, "zero": -0.0},
    }
    registry.write_registry(path, [row], indexed=True)
    actual = registry.load_registry(path)[0]
    assert actual["detail_report"] == report
    assert actual["configs"] == [config]
    assert actual["nested"]["source"] == str(tmp_path / "repo" / report)
    assert actual["eval_protocol"] == row["eval_protocol"]
    assert row["detail_report"] == legacy_report
    assert actual["exp_id"] == row["exp_id"]
    assert type(actual["metrics"]["int"]) is int
    assert type(actual["metrics"]["float"]) is float
    assert actual["metrics"]["zero"].hex() == (-0.0).hex()


def test_path_map_does_not_change_historical_reads_or_unrelated_registries(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    catalog = path.parent / "_catalog"
    catalog.mkdir(parents=True)
    original = {"exp_id": "group/arm", "detail_report": "backtest/experiments/old.html"}
    (catalog / "path_map.json").write_text(json.dumps({"schema_version": 1, "paths": {
        original["detail_report"]: "backtest/experiments/group/arm/reports/report.html",
    }}))
    path.write_text(json.dumps(original) + "\n")
    before = path.read_bytes()
    assert registry.load_registry(path) == [original]
    registry.append_registry(path, original)
    assert path.read_bytes().startswith(before)
    assert registry.load_registry(path)[0] == original
    assert registry.load_registry(path)[1]["detail_report"].endswith("group/arm/reports/report.html")
    unrelated = tmp_path / "other.jsonl"
    registry.write_registry(unrelated, [original], indexed=True)
    assert registry.load_registry(unrelated) == [original]


@pytest.mark.parametrize("corruption", ["missing", "hash", "metadata", "schema", "schema_type", "traversal", "absolute", "symlink", "oversize"])
def test_corrupt_detail_refuses_reads_and_writes(tmp_path, corruption):
    path = tmp_path / "registry.jsonl"
    registry.write_registry(path, rows(), indexed=True)
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    item = entries[1]
    detail = path.parent / item["detail_path"]
    if corruption == "missing":
        detail.unlink()
    elif corruption == "hash":
        detail.write_text('{"exp_id":"tampered"}')
    elif corruption == "metadata":
        item["state"] = "wrong"
    elif corruption == "schema":
        item["_registry_schema"] = "unknown"
    elif corruption == "schema_type":
        item["_registry_schema"] = []
    elif corruption == "traversal":
        item["detail_path"] = "../" + detail.name
    elif corruption == "absolute":
        item["detail_path"] = str(detail)
    elif corruption == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_bytes(detail.read_bytes())
        detail.unlink()
        detail.symlink_to(outside)
    else:
        detail.write_bytes(b" " * (registry.MAX_JSON_BYTES + 1))
    path.write_text("\n".join(json.dumps(item) for item in entries) + "\n")
    before = path.read_bytes()
    with pytest.raises((ValueError, OSError)):
        registry.load_registry(path)
    with pytest.raises((ValueError, OSError)):
        registry.append_registry(path, {"exp_id": "next"})
    assert path.read_bytes() == before


def test_existing_details_immutable_and_index_replace_failure_keeps_old_file(tmp_path, monkeypatch):
    path = tmp_path / "registry.jsonl"
    registry.write_registry(path, rows(), indexed=True)
    before = path.read_bytes()
    real_replace = registry.os.replace

    def fail_index(src, dst):
        if Path(dst) == path:
            raise OSError("injected index failure")
        return real_replace(src, dst)

    monkeypatch.setattr(registry.os, "replace", fail_index)
    with pytest.raises(OSError):
        registry.write_registry(path, rows() + [{"exp_id": "extra"}])
    assert path.read_bytes() == before
    assert registry.load_registry(path) == rows()


def test_nonfinite_legacy_numbers_are_not_rewritten_to_null(tmp_path):
    path = tmp_path / "registry.jsonl"
    row = {"exp_id": "old", "metrics": [float("nan"), float("inf"), -float("inf")]}
    registry.write_registry(path, [row], indexed=True)
    actual = registry.load_registry(path)[0]["metrics"]
    assert math.isnan(actual[0]) and actual[1:] == [float("inf"), -float("inf")]


def test_concurrent_appends_keep_every_event(tmp_path, monkeypatch):
    path = tmp_path / "registry.jsonl"
    registry.write_registry(path, [{"exp_id": "initial"}], indexed=True)
    real_write = registry._atomic_write

    def delayed_write(target, payload):
        if target == path:
            time.sleep(0.005)
        real_write(target, payload)

    monkeypatch.setattr(registry, "_atomic_write", delayed_write)
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda n: registry.append_registry(path, {"exp_id": f"event-{n}"}), range(20)))
    actual = registry.load_registry(path)
    assert len(actual) == 21
    assert {row["exp_id"] for row in actual} == {"initial", *(f"event-{n}" for n in range(20))}


def test_independent_processes_append_without_losing_events(tmp_path):
    path = tmp_path / "registry.jsonl"
    registry.write_registry(path, [{"exp_id": "initial"}], indexed=True)
    script_dir = Path(registry.__file__).parent
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[2]); "
        "from experiment_registry import append_registry; "
        "[append_registry(Path(sys.argv[1]), {'exp_id':sys.argv[3]+'-'+str(n)}) for n in range(8)]"
    )
    workers = [
        subprocess.Popen([sys.executable, "-c", code, str(path), str(script_dir), prefix])
        for prefix in ("a", "b")
    ]
    assert [worker.wait(timeout=15) for worker in workers] == [0, 0]
    assert len(registry.load_registry(path)) == 17


def test_repository_writes_typed_manifest_and_v3_transport(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    row = {"exp_id": "new/strategy", "phase": "S", "display_name": "New strategy"}
    registry.write_registry(path, [row])
    entry = json.loads(path.read_text())
    assert entry["_registry_schema"] == "qlib-experiment-index-v3"
    assert entry["_archive_kind"] == "strategy"
    assert entry["detail_path"].startswith("strategies/new/strategy/records/")
    metadata_path = path.parent / "strategies/new/strategy/metadata.json"
    metadata = json.loads(metadata_path.read_text())
    assert metadata["schema_version"] == 2 and metadata["entity_type"] == "experiment"
    assert metadata["title"] == "New strategy"
    metadata["description"] = "Keep editorial content"
    metadata_path.write_text(json.dumps(metadata))
    registry.append_registry(path, {**row, "state": "completed"})
    assert json.loads(metadata_path.read_text())["description"] == "Keep editorial content"
    assert registry.load_registry(path) == [row, {**row, "state": "completed"}]


def test_v3_tampered_kind_and_transport_in_detail_are_rejected(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    registry.write_registry(path, [{"exp_id": "group/arm", "phase": "M"}])
    entry = json.loads(path.read_text())
    entry["_archive_kind"] = "strategy"
    path.write_text(json.dumps(entry) + "\n")
    with pytest.raises(ValueError):
        registry.load_registry(path)
    with pytest.raises(ValueError):
        registry.encode_registry_row(path, {"exp_id": "x", "_archive_kind": "model"})


def test_repository_rewrite_preserves_immutable_history_and_original_paths(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    row = {"exp_id": "old/arm", "detail_report": "backtest/experiments/old.html", "score": -0.0}
    registry.write_registry(path, [row])
    before_index = path.read_bytes()
    entry = json.loads(before_index)
    detail = path.parent / entry["detail_path"]
    before_detail = detail.read_bytes()
    catalog = path.parent / "_catalog"
    catalog.mkdir()
    (catalog / "path_map.json").write_text(json.dumps({"schema_version": 1, "paths": {
        row["detail_report"]: "backtest/experiments/models/old/arm/reports/new.html"}}))
    registry.write_registry(path, registry.load_registry(path))
    assert path.read_bytes() == before_index and detail.read_bytes() == before_detail


def test_explicit_indexing_of_repository_inline_history_is_supported(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    path.parent.mkdir(parents=True)
    row = {"exp_id": "old/inline", "phase": "M"}
    path.write_text(json.dumps(row) + "\n")
    registry.write_registry(path, registry.load_registry(path), indexed=True)
    assert json.loads(path.read_text())["detail_path"].startswith("models/old/inline/records/")
    assert registry.load_registry(path) == [row]


def test_migrated_v3_details_remain_byte_exact_on_append_and_rewrite(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    payload = b'{"exp_id":"old/arm","phase":"M","metrics":[7,7.0,-0.0,1.2345678901234567]}\n'
    digest = hashlib.sha256(payload).hexdigest()
    relative = f"models/old/arm/records/{digest}.json"
    detail = path.parent / relative
    detail.parent.mkdir(parents=True)
    detail.write_bytes(payload)
    entry = {"_registry_schema": "qlib-experiment-index-v3", "_archive_kind": "model",
             "exp_id": "old/arm", "phase": "M", "detail_path": relative, "detail_sha256": digest}
    path.write_text(json.dumps(entry) + "\n")
    registry.append_registry(path, {"exp_id": "old/arm", "phase": "M", "state": "completed"})
    registry.write_registry(path, registry.load_registry(path))
    assert detail.read_bytes() == payload
    assert json.loads(path.read_text().splitlines()[0])["detail_sha256"] == digest
    records = json.loads((detail.parent.parent / "metadata.json").read_text())["records"]
    assert len(records) == 2 and f"backtest/experiments/{relative}" in records


def test_v3_rejects_nonstring_transport_kind_as_value_error(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    registry.write_registry(path, [{"exp_id": "group/arm"}])
    entry = json.loads(path.read_text())
    entry["_archive_kind"] = []
    path.write_text(json.dumps(entry) + "\n")
    with pytest.raises(ValueError):
        registry.load_registry(path)


def test_rewrite_unchanged_repository_history_repairs_missing_manifest(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    registry.write_registry(path, [{"exp_id": "group/arm"}])
    metadata = path.parent / "models/group/arm/metadata.json"
    metadata.unlink()
    before = path.read_bytes()
    registry.write_registry(path, registry.load_registry(path))
    assert metadata.exists() and path.read_bytes() == before


def _categorized_registry(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    row = {"exp_id": "stable/old-id", "phase": "M", "phase_m_protocol": "v1", "score": -0.0}
    registry.write_registry(path, [row])
    before = path.read_bytes()
    entry = json.loads(before)
    old = path.parent / "models/stable/old-id"
    new = path.parent / "models/training-target/h1"
    new.parent.mkdir(parents=True)
    old.rename(new)
    metadata_path = new / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    analysis = {"conclusion": "Editorial", "limitations": "Synthetic evidence",
                "evidence": [], "review_status": "draft", "updated_at": None}
    metadata.update(archive_path="backtest/experiments/models/training-target/h1",
                    experiment_type="training_target", analysis=analysis, title="Preserved title")
    metadata_path.write_text(json.dumps(metadata))
    catalog = path.parent / "_catalog"
    catalog.mkdir()
    (catalog / "path_map.json").write_text(json.dumps({"schema_version": 1, "paths": {}, "directories": {
        "backtest/experiments/models/stable/old-id": "backtest/experiments/models/training-target/h1"}}))
    return path, row, entry, before, old, new, analysis


def test_category_alias_reads_and_writes_keep_logical_detail_identity_and_editorial_metadata(tmp_path):
    path, row, entry, before, old, new, analysis = _categorized_registry(tmp_path)
    detail = new / "records" / f"{entry['detail_sha256']}.json"
    payload = detail.read_bytes()
    assert registry.load_registry(path) == [row]

    registry.write_registry(path, registry.load_registry(path))
    assert path.read_bytes() == before and detail.read_bytes() == payload
    registry.append_registry(path, {**row, "state": "completed"})

    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert entries[0] == entry
    assert all(item["detail_path"].startswith("models/stable/old-id/records/") for item in entries)
    assert registry.load_registry(path) == [row, {**row, "state": "completed"}]
    assert not old.exists() and len(list((new / "records").glob("*.json"))) == 2
    metadata = json.loads((new / "metadata.json").read_text())
    assert metadata["title"] == "Preserved title" and metadata["analysis"] == analysis
    assert metadata["experiment_type"] == "training_target"
    assert metadata["archive_path"] == "backtest/experiments/models/training-target/h1"


@pytest.mark.parametrize("target", ["backtest/experiments/strategies/foreign",
                                    "backtest/experiments/_shared/foreign", "docs/foreign"])
def test_category_detail_alias_cannot_escape_its_archive_kind(tmp_path, target):
    path, row, _, before, _, _, _ = _categorized_registry(tmp_path)
    mapping = path.parent / "_catalog/path_map.json"
    value = json.loads(mapping.read_text())
    value["directories"]["backtest/experiments/models/stable/old-id"] = target
    mapping.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        registry.load_registry(path)
    with pytest.raises(ValueError):
        registry.encode_registry_row(path, {**row, "state": "completed"})
    assert path.read_bytes() == before


def test_category_detail_alias_rejects_symlinked_physical_ancestor(tmp_path):
    path, row, _, before, _, new, _ = _categorized_registry(tmp_path)
    target = new.parent / "other"
    new.rename(target)
    new.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        registry.load_registry(path)
    with pytest.raises(ValueError, match="symlink"):
        registry.encode_registry_row(path, {**row, "state": "completed"})
    assert path.read_bytes() == before


def test_new_alias_target_gets_declared_metadata_without_recreating_logical_directory(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    catalog = path.parent / "_catalog"
    catalog.mkdir(parents=True)
    old = "backtest/experiments/models/stable/old-id"
    new = "backtest/experiments/models/evaluation/window-edge"
    (catalog / "path_map.json").write_text(json.dumps({"schema_version": 1, "paths": {}, "directories": {old: new}}))
    registry.append_registry(path, {"exp_id": "stable/old-id", "phase": "M"})
    assert not (tmp_path / "repo" / old).exists()
    metadata = json.loads((tmp_path / "repo" / new / "metadata.json").read_text())
    assert metadata["archive_path"] == new
    assert registry.load_registry(path)[0]["exp_id"] == "stable/old-id"


def test_writer_rejects_true_duplicate_metadata_before_creating_a_detail(tmp_path):
    path, row, _, before, old, new, _ = _categorized_registry(tmp_path)
    metadata = json.loads((new / "metadata.json").read_text())
    metadata.pop("archive_path")
    old.mkdir(parents=True)
    (old / "metadata.json").write_text(json.dumps(metadata))
    before_details = list((new / "records").iterdir())
    with pytest.raises(ValueError, match="duplicate"):
        registry.encode_registry_row(path, {**row, "state": "completed"})
    assert list((new / "records").iterdir()) == before_details
    assert path.read_bytes() == before


@pytest.mark.parametrize("ancestor", ["backtest", "backtest/experiments", "backtest/experiments/models"])
def test_repository_detail_roots_cannot_be_hidden_behind_symlinks(tmp_path, ancestor):
    root = tmp_path / "repo"
    path = root / "backtest/experiments/registry.jsonl"
    row = {"exp_id": "group/arm", "phase": "M"}
    registry.write_registry(path, [row])
    source, target = root / ancestor, root / "elsewhere"
    source.rename(target)
    source.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        registry.load_registry(path)
    with pytest.raises(ValueError, match="symlink"):
        registry.encode_registry_row(path, {**row, "state": "completed"})


def _unbound_manifest(tmp_path):
    path = tmp_path / "repo/backtest/experiments/registry.jsonl"
    physical = path.parent / "models/model-structure/new-model"
    physical.mkdir(parents=True)
    analysis = {"conclusion": "", "limitations": "", "evidence": [],
                "review_status": "draft", "updated_at": None}
    metadata = {"schema_version": 2, "entity_type": "experiment", "exp_id": "example/new-model",
                "kind": "model", "track": "all_a", "title": "Editorial title",
                "archive_path": "backtest/experiments/models/model-structure/new-model",
                "experiment_type": "model_structure", "analysis": analysis}
    (physical / "metadata.json").write_text(json.dumps(metadata))
    row = {"exp_id": "example/new-model", "phase": "M", "phase_m_protocol": "v1"}
    return path, physical, metadata, row


@pytest.mark.parametrize("method", ["append", "rewrite"])
def test_formal_writer_binds_new_manifest_directory_without_manual_path_map(tmp_path, method):
    path, physical, metadata, row = _unbound_manifest(tmp_path)
    if method == "append":
        registry.append_registry(path, row)
    else:
        registry.write_registry(path, [row])

    entry = json.loads(path.read_text())
    assert entry["detail_path"].startswith("models/example/new-model/records/")
    assert (physical / "records" / f"{entry['detail_sha256']}.json").is_file()
    assert not (path.parent / "models/example/new-model").exists()
    assert registry.load_registry(path) == [row]
    mapping = json.loads((path.parent / "_catalog/path_map.json").read_text())
    assert mapping["directories"]["backtest/experiments/models/example/new-model"] == metadata["archive_path"]
    actual = json.loads((physical / "metadata.json").read_text())
    for key in ("title", "experiment_type", "analysis", "archive_path"):
        assert actual[key] == metadata[key]
    before = path.read_bytes()
    registry.write_registry(path, registry.load_registry(path))
    assert path.read_bytes() == before


def test_unlocked_encoder_does_not_silently_publish_a_new_directory_binding(tmp_path):
    path, physical, _, row = _unbound_manifest(tmp_path)
    with pytest.raises(ValueError, match="alias"):
        registry.encode_registry_row(path, row)
    assert not (path.parent / "_catalog/path_map.json").exists()
    assert not (physical / "records").exists()


def test_auto_binding_never_overrides_an_existing_conflicting_alias(tmp_path):
    path, _, _, row = _unbound_manifest(tmp_path)
    mapping = path.parent / "_catalog/path_map.json"
    mapping.parent.mkdir()
    mapping.write_text(json.dumps({"schema_version": 1, "paths": {}, "directories": {
        "backtest/experiments/models/example/new-model": "backtest/experiments/models/elsewhere/other"}}))
    before = mapping.read_bytes()
    with pytest.raises(ValueError, match="conflict"):
        registry.append_registry(path, row)
    assert mapping.read_bytes() == before and not path.exists()


@pytest.mark.parametrize("reverse", ["directory", "exact-detail"])
def test_auto_binding_rejects_reverse_alias_before_publishing_a_cycle(tmp_path, reverse):
    path, _, metadata, row = _unbound_manifest(tmp_path)
    registry.write_registry(path, [{"exp_id": "unrelated/history", "phase": "S"}])
    before_index = path.read_bytes()
    mapping = path.parent / "_catalog/path_map.json"
    mapping.parent.mkdir()
    logical = "backtest/experiments/models/example/new-model"
    if reverse == "directory":
        document = {"schema_version": 1, "paths": {}, "directories": {metadata["archive_path"]: logical}}
    else:
        digest = hashlib.sha256(registry._json_bytes(row)).hexdigest()
        suffix = f"/records/{digest}.json"
        document = {"schema_version": 1, "directories": {}, "paths": {
            metadata["archive_path"] + suffix: logical + suffix}}
    mapping.write_text(json.dumps(document))
    before = mapping.read_bytes()
    with pytest.raises(ValueError, match="conflict|cycle"):
        registry.append_registry(path, row)
    assert mapping.read_bytes() == before and path.read_bytes() == before_index


@pytest.mark.parametrize("entry", ["file", "ancestor"])
def test_auto_binding_rejects_symlinked_map_and_map_ancestor(tmp_path, entry):
    path, _, _, row = _unbound_manifest(tmp_path)
    catalog = path.parent / "_catalog"
    target = path.parent / "other-catalog"
    target.mkdir()
    (target / "path_map.json").write_text(json.dumps({"schema_version": 1, "paths": {}}))
    if entry == "ancestor":
        catalog.symlink_to(target, target_is_directory=True)
    else:
        catalog.mkdir()
        (catalog / "path_map.json").symlink_to(target / "path_map.json")
    with pytest.raises(ValueError, match="symlink"):
        registry.append_registry(path, row)
    assert not path.exists()


def test_auto_binding_refuses_to_hide_populated_old_logical_archive(tmp_path):
    path, _, _, row = _unbound_manifest(tmp_path)
    old = path.parent / "models/example/new-model/records"
    old.mkdir(parents=True)
    (old / "unmigrated.json").write_text("unmigrated original")
    with pytest.raises(ValueError, match="populated"):
        registry.append_registry(path, row)
    assert not (path.parent / "_catalog/path_map.json").exists()
    assert (old / "unmigrated.json").read_text() == "unmigrated original"


def test_concurrent_formal_appends_preserve_each_new_directory_binding(tmp_path):
    path, physical, metadata, row = _unbound_manifest(tmp_path)
    other = physical.parent / "second-model"
    other.mkdir()
    second = {**row, "exp_id": "example/second-model"}
    (other / "metadata.json").write_text(json.dumps({**metadata, "exp_id": second["exp_id"],
        "archive_path": "backtest/experiments/models/model-structure/second-model"}))
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda value: registry.append_registry(path, value), [row, second]))
    assert {item["exp_id"] for item in registry.load_registry(path)} == {row["exp_id"], second["exp_id"]}
    mapping = json.loads((path.parent / "_catalog/path_map.json").read_text())
    assert len(mapping["directories"]) == 2
