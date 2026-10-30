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
