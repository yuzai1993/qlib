"""SignalGenerator 推理口径测试：NaN 必须原样传给 LightGBM，不允许 fillna(0)。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.signal_generator import SignalGenerator


class DummyLGB:
    """记录 predict 收到的矩阵。"""

    def __init__(self):
        self.last_X = None

    def predict(self, X):
        self.last_X = np.asarray(X, dtype=float)
        return np.arange(len(X), dtype=float)


def _make_generator():
    gen = SignalGenerator(config={}, project_root=Path("."))
    gen._lgb_model = DummyLGB()
    return gen


def test_nan_features_passed_through_not_filled_with_zero():
    gen = _make_generator()
    df = pd.DataFrame(
        {"F1": [1.0, np.nan], "F2": [np.nan, 2.0]},
        index=pd.Index(["SH600000", "SZ000001"], name="instrument"),
    )
    scores = gen._score_features(df, "2026-07-10")

    assert gen._lgb_model.last_X is not None
    # 核心断言：NaN 不能被替换为 0
    assert np.isnan(gen._lgb_model.last_X).sum() == 2
    assert (gen._lgb_model.last_X == 0).sum() == 0
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


def _generator_with_features(last_date="2026-07-14"):
    gen = _make_generator()
    gen._model = object()
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


def test_predict_default_keeps_stale_fallback():
    gen = _generator_with_features()
    scores = gen.predict("2026-07-15")
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
            "data": {"instruments": "csi300"},
            "handler": {
                "class": "Alpha158",
                "module": "qlib.contrib.data.handler",
                "start_time": "2003-01-02",
                "fit_start_time": "2006-01-02",
                "fit_end_time": "2020-01-10",
                "infer_processors": [{"class": "ProcessInf"}],
            },
        },
        project_root=Path("."),
    )

    gen._ensure_handler("2026-07-22")

    kwargs = captured["kwargs"]
    assert kwargs["fit_start_time"] == "2006-01-02"
    assert kwargs["fit_end_time"] == "2020-01-10"
    assert kwargs["infer_processors"] == [{"class": "ProcessInf"}]
