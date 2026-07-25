from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_backtest as rb  # noqa: E402


class _FakeModel:
    def __init__(self):
        self.fit_calls = []

    def fit(self, dataset):
        self.fit_calls.append(dataset)


class _FakeRecorder:
    id = "train-recorder"
    experiment_id = "train-experiment"


class _FakeRunContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeWorkflow:
    def __init__(self):
        self.experiment_names = []
        self.saved = []
        self.params = []

    def start(self, *, experiment_name):
        self.experiment_names.append(experiment_name)
        return _FakeRunContext()

    def log_params(self, **kwargs):
        self.params.append(kwargs)

    def save_objects(self, **kwargs):
        self.saved.append(kwargs)

    @staticmethod
    def get_recorder():
        return _FakeRecorder()


def test_train_only_fits_and_saves_without_strategy_records(tmp_path, monkeypatch):
    model = _FakeModel()
    dataset = object()
    workflow = _FakeWorkflow()
    saved_reports = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("train_only constructed a strategy record")

    monkeypatch.setattr(rb, "R", workflow)
    monkeypatch.setattr(
        rb,
        "init_instance_by_config",
        lambda cfg: model if cfg["kind"] == "model" else dataset,
    )
    monkeypatch.setattr(rb, "SignalRecord", forbidden)
    monkeypatch.setattr(rb, "PortAnaRecord", forbidden)
    monkeypatch.setattr(
        rb,
        "_save_train_only_report",
        lambda **kwargs: saved_reports.append(kwargs),
    )

    result = rb.run_train_only_once(
        run_idx=1,
        n_runs=1,
        session_dir=tmp_path,
        session_name="session",
        note="model-test",
        task={
            "model": {"kind": "model", "alpha": 1},
            "dataset": {"kind": "dataset"},
        },
    )

    assert model.fit_calls == [dataset]
    assert workflow.experiment_names == ["train_session_run01"]
    assert workflow.saved == [{"trained_model": model}]
    assert result["status"] == "success"
    assert result["train_experiment_id"] == "train-experiment"
    assert result["train_recorder_id"] == "train-recorder"
    assert "backtest_experiment_id" not in result
    assert len(saved_reports) == 1
    assert saved_reports[0]["mlruns_link"] == {
        "train_experiment_name": "train_session_run01",
        "train_experiment_id": "train-experiment",
        "train_recorder_id": "train-recorder",
        "train_artifacts": "mlruns/train-experiment/train-recorder",
    }
