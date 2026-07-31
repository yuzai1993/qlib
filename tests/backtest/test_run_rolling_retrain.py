from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_rolling_retrain as rolling  # noqa: E402


def _config() -> dict:
    return {
        "_config_path": "/repo/backtest/configs/train-schedule/example.yaml",
        "run": {"mode": "train_only", "note": "rolling-s42"},
        "data": {
            "provider_uri": "~/.qlib/qlib_data/cn_data",
            "instruments": "csi1000",
            "benchmark": "SH000852",
            "handler": {
                "class": "Alpha158Technical",
                "module_path": "backtest.features.technical",
                "start_time": "2019-01-01",
                "end_time": "2020-01-24",
                "fit_start_time": "2020-01-01",
                "fit_end_time": "2020-01-03",
            },
        },
        "segments": {
            "train": ["2020-01-01", "2020-01-03"],
            "valid": ["2020-01-06", "2020-01-10"],
            "test": ["2020-01-13", "2020-01-24"],
        },
        "model": {
            "class": "FakeModel",
            "kwargs": {"seed": 42},
        },
        "dataset": {
            "class": "PurgedHorizonDataset",
            "kwargs": {"label_horizon": 40},
        },
    }


def test_build_expanding_folds_shifts_train_end_and_valid_window():
    calendar = pd.bdate_range("2020-01-01", periods=18)

    folds = rolling.build_expanding_folds(_config(), calendar, step=4)

    assert folds == [
        {
            "fold": 1,
            "segments": {
                "train": ["2020-01-01", "2020-01-03"],
                "valid": ["2020-01-06", "2020-01-10"],
                "test": ["2020-01-13", "2020-01-16"],
            },
        },
        {
            "fold": 2,
            "segments": {
                "train": ["2020-01-01", "2020-01-09"],
                "valid": ["2020-01-10", "2020-01-16"],
                "test": ["2020-01-17", "2020-01-22"],
            },
        },
        {
            "fold": 3,
            "segments": {
                "train": ["2020-01-01", "2020-01-15"],
                "valid": ["2020-01-16", "2020-01-22"],
                "test": ["2020-01-23", "2020-01-24"],
            },
        },
    ]


def test_apply_fold_updates_dates_without_mutating_base_config():
    cfg = _config()
    fold = {
        "fold": 2,
        "segments": {
            "train": ["2020-01-01", "2020-01-09"],
            "valid": ["2020-01-10", "2020-01-16"],
            "test": ["2020-01-17", "2020-01-22"],
        },
    }

    actual = rolling.apply_fold(cfg, fold)

    assert actual["segments"] == fold["segments"]
    assert actual["data"]["handler"]["fit_start_time"] == "2020-01-01"
    assert actual["data"]["handler"]["fit_end_time"] == "2020-01-09"
    assert actual["data"]["handler"]["end_time"] == "2020-01-22"
    assert actual["model"] == cfg["model"]
    assert cfg["segments"]["test"] == ["2020-01-13", "2020-01-24"]
    assert cfg["data"]["handler"]["fit_end_time"] == "2020-01-03"


def test_run_rolling_session_records_every_fold_and_returns_failure(
    tmp_path, monkeypatch
):
    cfg = _config()
    calendar = pd.bdate_range("2020-01-01", periods=18)
    calls = []

    def fake_train(run_idx, n_runs, session_dir, session_name, note, task):
        calls.append((run_idx, n_runs, task["dataset"]["kwargs"]["segments"]))
        return {
            "run": run_idx,
            "status": "failed" if run_idx == 2 else "success",
            "train_experiment_name": f"train_{run_idx}",
            "train_experiment_id": str(100 + run_idx),
            "train_recorder_id": f"rec-{run_idx}",
        }

    monkeypatch.setattr(rolling, "run_train_only_once", fake_train)
    monkeypatch.setattr(
        rolling,
        "build_task",
        lambda fold_cfg: {
            "model": fold_cfg["model"],
            "dataset": {"kwargs": {"segments": fold_cfg["segments"]}},
        },
    )

    exit_code = rolling.run_rolling_session(
        cfg,
        calendar=calendar,
        step=4,
        session_dir=tmp_path,
    )

    assert exit_code == 1
    assert [call[0] for call in calls] == [1, 2, 3]
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["mode"] == "rolling_train_only"
    assert meta["seed"] == 42
    assert meta["step"] == 4
    assert meta["expected_fold_count"] == 3
    assert [row["status"] for row in meta["runs"]] == [
        "success",
        "failed",
        "success",
    ]
    assert meta["rolling_folds"][2]["segments"]["test"] == [
        "2020-01-23",
        "2020-01-24",
    ]
