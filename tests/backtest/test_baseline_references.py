"""A merged baseline receipt remains a frozen reference, not an active experiment."""

import hashlib
import json

import pytest


def receipt(root, row=None):
    group = root / "backtest/experiments/models/training-target/horizons"
    path = group / "baseline-records/v1.json"
    path.parent.mkdir(parents=True)
    value = row or {"exp_id": "regime-adapt/h20-v1", "phase": "M", "phase_m_protocol": "v1",
                    "baseline_version": "v1", "state": "completed", "metrics": {"net_ann": .2933}}
    path.write_text(json.dumps(value))
    metadata = {"schema_version": 2, "entity_type": "experiment", "kind": "model", "track": "all_a",
                "exp_id": "training-target/horizons", "title": "目标期限汇总",
                "historical_baselines": [{"path": "baseline-records/v1.json",
                                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}
    (group / "metadata.json").write_text(json.dumps(metadata))
    return path, value


def test_receipt_rows_are_returned_unchanged_and_not_registered(tmp_path):
    from backtest.scripts.baseline_references import load_historical_baselines

    _, row = receipt(tmp_path)
    assert load_historical_baselines(tmp_path) == [row]
    assert not (tmp_path / "backtest/experiments/registry.jsonl").exists()


@pytest.mark.parametrize("failure", ["missing", "sha", "kind", "version", "symlink"])
def test_invalid_historical_baseline_does_not_silently_remove_protection(tmp_path, failure):
    from backtest.scripts.baseline_references import load_historical_baselines

    bad = {"exp_id": "regime-adapt/h20-v1", "phase": "M", "phase_m_protocol": "v1",
           "baseline_version": "v1", "state": "completed"}
    if failure == "kind":
        bad.update(phase="S", report_kind="phase_m_v1_bt_baseline")
    elif failure == "version":
        bad.pop("baseline_version")
    path, _ = receipt(tmp_path, bad)
    if failure == "missing":
        path.unlink()
    elif failure == "sha":
        path.write_text(path.read_text() + " ")
    elif failure == "symlink":
        outside = tmp_path / "original.json"
        path.rename(outside)
        path.symlink_to(outside)
    with pytest.raises((ValueError, OSError)):
        load_historical_baselines(tmp_path)
