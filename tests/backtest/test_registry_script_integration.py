from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backtest/scripts"))

from experiment_registry import load_registry, write_registry  # noqa: E402


READERS = [
    ("build_experiment_report", "load_registry", None),
    ("build_phase_m_v1_report", "load_registry", None),
    ("build_phase_m_v1_bt_report", "load_registry", None),
    ("build_strategy_report", "load_registry", None),
    ("cleanup_experiment_artifacts", "load_registry", None),
    ("finalize_strategy_neighborhood", "load_registry", None),
    ("finalize_strategy_neighborhood_full", "load_registry", None),
    ("register_phase_s_experiment", "load_registry", None),
    ("register_strategy_stability", "load_registry", None),
    ("register_regime_m0_labels", "load_registry", "REG"),
    ("register_regime_ablation", "load_registry", "REG"),
    ("promote_b3s_and_refresh_metrics", "_load_registry", "REGISTRY"),
    ("promote_b4s_t22_d2_h2_r090", "_load_registry", "REGISTRY"),
    ("register_b4s_diagnostics", "_load", "REGISTRY"),
]


@pytest.mark.parametrize("module_name,function,constant", READERS)
def test_script_readers_hydrate_ordered_history(tmp_path, monkeypatch, module_name, function, constant):
    path = tmp_path / "registry.jsonl"
    rows = [
        {"exp_id": "historical/a", "state": "completed", "metrics": {"rank_ic": 0.123}},
        {"exp_id": "historical/a", "state": "correction", "metrics": {"rank_ic": 0.124}},
    ]
    write_registry(path, rows, indexed=True)
    module = importlib.import_module(module_name)
    if constant:
        monkeypatch.setattr(module, constant, path)
        actual = getattr(module, function)()
    else:
        actual = getattr(module, function)(path)
    assert actual == rows
    assert all("metrics" not in json.loads(line) for line in path.read_text().splitlines())


def test_cleanup_selects_same_candidate_from_index(tmp_path):
    cleanup = importlib.import_module("cleanup_experiment_artifacts")
    pools = ("csi300", "csi500", "csi1000")
    baseline = {
        "exp_id": "baseline/b6-m", "direction": "baseline", "phase": "M",
        "conclusion": "baseline", "baseline_ref": "B6 v1.0",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "metrics_summary": {pool: {"rank_ic_mean": 0.04, "rank_icir": 0.4} for pool in pools},
    }
    candidate = {
        **baseline, "exp_id": "candidate/better", "direction": "model-design",
        "conclusion": "improve",
        "metrics_summary": {pool: {"rank_ic_mean": 0.05, "rank_icir": 0.5} for pool in pools},
    }
    rows = [baseline, candidate]
    registry = tmp_path / "registry.jsonl"
    write_registry(registry, rows, indexed=True)
    original_plan = cleanup.build_cleanup_plan(tmp_path, rows)
    loaded_plan = cleanup.build_cleanup_plan(tmp_path, cleanup.load_registry(registry))
    assert original_plan["candidate_exp_id"] == "candidate/better"
    assert loaded_plan == original_plan


RAW_WRITERS = [
    ("analyze_b2s_style_regime", "upsert_registry", "target", "completed"),
    ("finalize_strategy_neighborhood", "upsert_registry_transition", None, "test_complete"),
    ("finalize_strategy_neighborhood_full", "upsert_registry_transition", None, "complete"),
    ("register_bt_v5_cutover_diagnostics", "save_record", "target", "completed"),
]


def _raw_writer(tmp_path, monkeypatch, module_name, function, exp_id):
    module = importlib.import_module(module_name)
    exp_id = exp_id or module.EXP_ID
    registry = tmp_path / "backtest/experiments/registry.jsonl"
    if function == "save_record":
        monkeypatch.setattr(module, "ROOT", tmp_path)
        invoke = lambda row: module.save_record(row)
    else:
        invoke = lambda row: getattr(module, function)(registry, row)
    return registry, exp_id, invoke


@pytest.mark.parametrize("module_name,function,exp_id,state", RAW_WRITERS)
def test_raw_writers_keep_index_and_bystander_bytes(tmp_path, monkeypatch, module_name, function, exp_id, state):
    registry, exp_id, invoke = _raw_writer(tmp_path, monkeypatch, module_name, function, exp_id)
    before = [
        {"exp_id": "bystander", "metrics": {"exact": 0.123456789}},
        {"exp_id": exp_id, "state": "preregistered"},
    ]
    write_registry(registry, before, indexed=True)
    lines = registry.read_bytes().splitlines()
    prefix = lines[0] + b"\r\n\n"
    registry.write_bytes(prefix + lines[1])
    completed = {"exp_id": exp_id, "state": state, "metrics": {"net_ann": 0.42}}
    invoke(completed)
    assert registry.read_bytes().startswith(prefix)
    assert load_registry(registry) == [before[0], completed]
    assert all("metrics" not in json.loads(line) for line in registry.read_text().splitlines() if line.strip())


@pytest.mark.parametrize("module_name,function,exp_id,state", RAW_WRITERS)
def test_raw_writers_reject_broken_bystander_details_before_writing(tmp_path, monkeypatch, module_name, function, exp_id, state):
    registry, exp_id, invoke = _raw_writer(tmp_path, monkeypatch, module_name, function, exp_id)
    write_registry(registry, [{"exp_id": "bystander", "metrics": {}}, {"exp_id": exp_id, "state": "preregistered"}], indexed=True)
    bystander = json.loads(registry.read_text().splitlines()[0])
    (registry.parent / bystander["detail_path"]).write_text("{}\n")
    snapshot = {p: p.read_bytes() for p in registry.parent.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="SHA256"):
        invoke({"exp_id": exp_id, "state": state, "metrics": {"net_ann": 0.42}})
    assert snapshot == {p: p.read_bytes() for p in registry.parent.rglob("*") if p.is_file()}


@pytest.mark.parametrize("module_name,state", [
    ("finalize_strategy_neighborhood", "test_complete"),
    ("finalize_strategy_neighborhood_full", "complete"),
])
def test_rejected_completed_transition_creates_no_detail(tmp_path, module_name, state):
    module = importlib.import_module(module_name)
    path = tmp_path / "registry.jsonl"
    original = {"exp_id": module.EXP_ID, "state": state, "metrics": {"net_ann": 0.1}}
    write_registry(path, [original], indexed=True)
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="immutable"):
        module.upsert_registry_transition(path, {**original, "metrics": {"net_ann": 0.2}})
    assert snapshot == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("module_name,function", [
    ("promote_b3s_and_refresh_metrics", "_write_registry"),
    ("promote_b4s_t22_d2_h2_r090", "_write_registry"),
    ("register_b4s_diagnostics", "_write"),
])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_strict_registry_writers_reject_nonfinite_without_changes(tmp_path, monkeypatch, module_name, function, invalid):
    module = importlib.import_module(module_name)
    registry = tmp_path / "registry.jsonl"
    original = {"exp_id": "existing", "metrics": {"value": 0.2}}
    write_registry(registry, [original], indexed=True)
    monkeypatch.setattr(module, "REGISTRY", registry)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        getattr(module, function)([{**original, "metrics": {"value": invalid}}])
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_stability_writer_rejects_nonfinite_before_detail_write(tmp_path, invalid):
    module = importlib.import_module("register_strategy_stability")
    path = tmp_path / "registry.jsonl"
    pending = {"exp_id": "diagnostic", "state": "preregistered"}
    write_registry(path, [pending], indexed=True)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        module.upsert_diagnostic_row(path, {**pending, "state": "complete", "metrics": {"value": invalid}}, expected_previous_state="preregistered")
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
