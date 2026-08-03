"""SignalGenerator 推理口径测试：NaN 必须原样传给 LightGBM，不允许 fillna(0)。"""
import hashlib
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.signal_generator import SignalGenerator


class DummyQlibModel:
    def __init__(self):
        self.last_features = None

    def predict(self, dataset, segment="test"):
        self.last_features = dataset.prepare(
            segment,
            col_set="feature",
            data_key="infer",
        ).copy()
        return pd.Series(
            np.arange(len(self.last_features), dtype=float),
            index=self.last_features.index,
        )


def _make_generator():
    gen = SignalGenerator(config={}, project_root=Path("."))
    gen._model = DummyQlibModel()
    return gen


def test_nan_features_passed_through_not_filled_with_zero():
    gen = _make_generator()
    df = pd.DataFrame(
        {"F1": [1.0, np.nan], "F2": [np.nan, 2.0]},
        index=pd.Index(["SH600000", "SZ000001"], name="instrument"),
    )
    scores = gen._score_features(df, "2026-07-10")

    received = gen._model.last_features
    assert received is not None
    # 核心断言：NaN 不能被替换为 0
    assert received.isna().to_numpy().sum() == 2
    assert (received.to_numpy() == 0).sum() == 0
    assert list(scores.index) == ["SH600000", "SZ000001"]


def test_all_nan_rows_are_dropped():
    gen = _make_generator()
    df = pd.DataFrame(
        {"F1": [1.0, np.nan], "F2": [2.0, np.nan]},
        index=pd.Index(["SH600000", "SZ000001"], name="instrument"),
    )
    scores = gen._score_features(df, "2026-07-10")
    # 全 NaN 行（长期停牌/退市残留）仍应剔除
    assert list(scores.index) == ["SH600000"]


def test_model_output_must_match_feature_index_exactly():
    class WrongIndexModel:
        def predict(self, dataset, segment="test"):
            return pd.Series([1.0], index=pd.Index(["SH999999"]))

    gen = SignalGenerator(config={}, project_root=Path("."))
    gen._model = WrongIndexModel()
    features = pd.DataFrame(
        {"F1": [1.0]},
        index=pd.Index(["SH600000"], name="instrument"),
    )

    with pytest.raises(ValueError, match="index must exactly match"):
        gen._score_features(features, "2026-07-10")


def test_non_finite_model_scores_are_excluded():
    class NonFiniteModel:
        def predict(self, dataset, segment="test"):
            features = dataset.prepare(
                segment, col_set="feature", data_key="infer",
            )
            return pd.Series([1.0, np.inf], index=features.index)

    gen = SignalGenerator(config={}, project_root=Path("."))
    gen._model = NonFiniteModel()
    features = pd.DataFrame(
        {"F1": [1.0, 2.0]},
        index=pd.Index(["SH600000", "SZ000001"], name="instrument"),
    )

    scores = gen._score_features(features, "2026-07-10")

    assert scores.to_dict() == {"SH600000": 1.0}


def _generator_with_features(last_date="2026-07-14"):
    gen = _make_generator()
    gen._handler = object()
    gen._handler_end_date = "2099-12-31"
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(last_date), "SH600000")],
        names=["datetime", "instrument"],
    )
    gen._features = pd.DataFrame({"F1": [1.0]}, index=index)
    return gen


def test_predict_strict_rejects_stale_feature_date():
    gen = _generator_with_features()
    with pytest.raises(ValueError, match="not in features"):
        gen.predict("2026-07-15", allow_stale=False)


def test_predict_default_rejects_stale_feature_date():
    gen = _generator_with_features()

    with pytest.raises(ValueError, match="not in features"):
        gen.predict("2026-07-15")


def test_predict_explicit_diagnostic_allows_stale_fallback():
    gen = _generator_with_features()
    scores = gen.predict("2026-07-15", allow_stale=True)
    assert list(scores.index) == ["SH600000"]


def test_handler_uses_explicit_training_fit_window(monkeypatch):
    captured = {}

    class DummyHandler:
        def fetch(self, **kwargs):
            return pd.DataFrame()

    def fake_init(config):
        captured.update(config)
        return DummyHandler()

    monkeypatch.setattr(
        "live_trading.modules.signal_generator.init_instance_by_config",
        fake_init,
    )
    gen = SignalGenerator(
        config={
            "data": {"instruments": "csi1000"},
            "handler": {
                "class": "Alpha158Technical",
                "module": "backtest.features.technical",
                "start_time": "2003-01-02",
                "fit_start_time": "2006-01-02",
                "fit_end_time": "2020-01-10",
                "infer_processors": [{"class": "ProcessInf"}],
                "feature_groups": ["range"],
            },
        },
        project_root=Path("."),
    )

    gen._ensure_handler("2026-07-22")

    kwargs = captured["kwargs"]
    assert kwargs["fit_start_time"] == "2006-01-02"
    assert kwargs["fit_end_time"] == "2020-01-10"
    assert kwargs["infer_processors"] == [{"class": "ProcessInf"}]
    assert kwargs["feature_groups"] == ["range"]


def test_load_model_from_git_tracked_relative_path(tmp_path):
    model_path = tmp_path / "live_trading/models/b1_m/test/trained_model"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(pickle.dumps(DummyQlibModel()))
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    gen = SignalGenerator(
        config={
            "model": {
                "model_path": "live_trading/models/b1_m/test/trained_model",
                "sha256": digest,
            }
        },
        project_root=tmp_path,
    )

    gen.load_model()

    assert isinstance(gen._model, DummyQlibModel)


def test_load_model_rejects_sha256_mismatch(tmp_path):
    model_path = tmp_path / "live_trading/models/b1_m/test/trained_model"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(pickle.dumps(DummyQlibModel()))
    gen = SignalGenerator(
        config={
            "model": {
                "model_path": "live_trading/models/b1_m/test/trained_model",
                "sha256": "0" * 64,
            }
        },
        project_root=tmp_path,
    )

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        gen.load_model()


def test_load_model_requires_tracked_model_path(tmp_path):
    gen = SignalGenerator(
        config={"model": {"experiment_id": "legacy", "recorder_id": "legacy"}},
        project_root=tmp_path,
    )

    with pytest.raises(ValueError, match="model_path"):
        gen.load_model()
