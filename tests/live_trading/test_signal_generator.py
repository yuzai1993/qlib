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
    gen._models = [DummyQlibModel()]
    return gen


def test_nan_features_passed_through_not_filled_with_zero():
    gen = _make_generator()
    df = pd.DataFrame(
        {"F1": [1.0, np.nan], "F2": [np.nan, 2.0]},
        index=pd.Index(["SH600000", "SZ000001"], name="instrument"),
    )
    scores = gen._score_features(df, "2026-07-10")

    received = gen._models[0].last_features
    assert received is not None
    # 核心断言：NaN 不能被替换为 0
    assert received.isna().to_numpy().sum() == 2
    assert (received.to_numpy() == 0).sum() == 0
    assert list(scores.index) == ["SH600000", "SZ000001"]


def test_all_nan_rows_are_dropped():
    gen = _make_generator()
    df = pd.DataFrame(
        {"F1": [1.0, np.nan, 3.0], "F2": [2.0, np.nan, 4.0]},
        index=pd.Index(["SH600000", "SZ000001", "SH600519"], name="instrument"),
    )
    scores = gen._score_features(df, "2026-07-10")
    # 全 NaN 行（长期停牌/退市残留）仍应剔除
    assert list(scores.index) == ["SH600000", "SH600519"]


def test_model_output_must_match_feature_index_exactly():
    class WrongIndexModel:
        def predict(self, dataset, segment="test"):
            return pd.Series([1.0], index=pd.Index(["SH999999"]))

    gen = SignalGenerator(config={}, project_root=Path("."))
    gen._models = [WrongIndexModel()]
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
            return pd.Series([1.0, np.inf, 3.0], index=features.index)

    gen = SignalGenerator(config={}, project_root=Path("."))
    gen._models = [NonFiniteModel()]
    features = pd.DataFrame(
        {"F1": [1.0, 2.0, 3.0]},
        index=pd.Index(["SH600000", "SZ000001", "SH600519"], name="instrument"),
    )

    scores = gen._score_features(features, "2026-07-10")

    assert list(scores.index) == ["SH600000", "SH600519"]


def test_single_name_cross_section_fails_loudly_instead_of_blanking():
    """截面只剩一只票时 z-score 是 NaN，不能静默变成「今天没有候选」。"""
    gen = _make_generator()
    features = pd.DataFrame(
        {"F1": [1.0]},
        index=pd.Index(["SH600000"], name="instrument"),
    )

    with pytest.raises(ValueError, match="cross-section too small"):
        gen._score_features(features, "2026-07-10")


def _generator_with_features(last_date="2026-07-14"):
    gen = _make_generator()
    gen._handler = object()
    gen._handler_end_date = "2099-12-31"
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(last_date), "SH600000"), (pd.Timestamp(last_date), "SZ000001")],
        names=["datetime", "instrument"],
    )
    gen._features = pd.DataFrame({"F1": [1.0, 2.0]}, index=index)
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
    assert list(scores.index) == ["SH600000", "SZ000001"]


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

    assert [type(model) for model in gen._models] == [DummyQlibModel]


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


class _FakeModel:
    """按 instrument 顺序返回固定分数，用于验证合成算术。"""

    def __init__(self, values):
        self._values = values

    def predict(self, dataset, segment="test"):
        features = dataset.prepare(segment, col_set="feature", data_key="infer")
        return pd.Series(self._values, index=features.index, dtype=float)


def _generator():
    return SignalGenerator({"model": {}, "handler": {}, "data": {}}, Path("."))


def _install(gen, models, names, date):
    gen._models = models
    gen._handler = object()
    gen._features = pd.DataFrame(
        {"f": np.arange(len(names), dtype=float)},
        index=pd.MultiIndex.from_product(
            [[pd.Timestamp(date)], names], names=["datetime", "instrument"],
        ),
    )
    gen._handler_end_date = date


def test_ensemble_zscores_each_member_before_averaging():
    gen = _generator()
    names = ["SH600000", "SZ000001", "SH600519"]
    # 成员 A 与成员 B 排序相反，等权合成后应完全抵平
    _install(
        gen, [_FakeModel([1.0, 2.0, 3.0]), _FakeModel([3.0, 2.0, 1.0])],
        names, "2026-08-20",
    )

    scores = gen.predict("2026-08-20")

    assert list(scores.index) == names
    np.testing.assert_allclose(scores.to_numpy(), [0.0, 0.0, 0.0], atol=1e-9)


def test_ensemble_is_invariant_to_member_scale():
    gen = _generator()
    names = ["SH600000", "SZ000001", "SH600519"]
    # 同一排序、量纲差 1000 倍：z-score 后两成员完全相同，合成等于单成员
    _install(
        gen, [_FakeModel([1.0, 2.0, 3.0]), _FakeModel([1000.0, 2000.0, 3000.0])],
        names, "2026-08-20",
    )
    blended = gen.predict("2026-08-20")

    gen_single = _generator()
    _install(gen_single, [_FakeModel([1.0, 2.0, 3.0])], names, "2026-08-20")
    single = gen_single.predict("2026-08-20")

    np.testing.assert_allclose(blended.to_numpy(), single.to_numpy(), atol=1e-9)


def test_single_member_path_returns_plain_instrument_index():
    gen = _generator()
    names = ["SH600000", "SZ000001"]
    _install(gen, [_FakeModel([1.0, 2.0])], names, "2026-08-20")

    scores = gen.predict("2026-08-20")

    assert not isinstance(scores.index, pd.MultiIndex)
    assert list(scores.index) == names


def test_load_model_requires_sha256_for_every_member(tmp_path):
    config = {
        "model": {
            "members": [
                {
                    "seed": 42,
                    "model_path": "live_trading/models/x/s42/trained_model",
                    "sha256": "a" * 64,
                },
                {
                    "seed": 1000,
                    "model_path": "live_trading/models/x/s1000/trained_model",
                },
            ]
        },
        "handler": {},
        "data": {},
    }
    gen = SignalGenerator(config, tmp_path)

    with pytest.raises(ValueError, match="sha256"):
        gen.load_model()


def test_load_model_rejects_empty_members_list(tmp_path):
    gen = SignalGenerator(
        {"model": {"members": []}, "handler": {}, "data": {}}, tmp_path,
    )

    with pytest.raises(ValueError, match="members"):
        gen.load_model()


def _stub_handler_factory(captured):
    def fake_init_instance_by_config(cfg):
        captured["kwargs"] = cfg["kwargs"]

        class _H:
            def fetch(self, col_set, data_key):
                return pd.DataFrame(
                    {"f": [1.0]},
                    index=pd.MultiIndex.from_tuples(
                        [(pd.Timestamp("2026-08-20"), "SH600000")],
                        names=["datetime", "instrument"],
                    ),
                )

        return _H()

    return fake_init_instance_by_config


def _handler_config(extra=None):
    handler = {
        "class": "Alpha158Technical",
        "module": "backtest.features.technical",
        "start_time": "2020-02-03",
        "fit_start_time": "2020-02-03",
        "fit_end_time": "2020-08-03",
        "infer_processors": [{"class": "ProcessInf"}],
        "feature_groups": ["range"],
    }
    handler.update(extra or {})
    return {"model": {}, "data": {"instruments": "all"}, "handler": handler}


def test_handler_receives_filter_pipe_when_configured(monkeypatch, tmp_path):
    """配置里的 filter_pipe 必须真的传给 handler，不能被静默忽略。"""
    import live_trading.modules.signal_generator as sg

    captured = {}
    monkeypatch.setattr(
        sg, "init_instance_by_config", _stub_handler_factory(captured),
    )
    filter_pipe = [
        {"filter_type": "NameDFilter", "name_rule_re": "^(SH60|SH68|SZ00|SZ30)"}
    ]
    gen = SignalGenerator(
        _handler_config({"filter_pipe": filter_pipe}), tmp_path,
    )

    gen._ensure_handler("2026-08-20")

    assert captured["kwargs"]["filter_pipe"] == filter_pipe


def test_handler_omits_filter_pipe_when_absent(monkeypatch, tmp_path):
    import live_trading.modules.signal_generator as sg

    captured = {}
    monkeypatch.setattr(
        sg, "init_instance_by_config", _stub_handler_factory(captured),
    )
    gen = SignalGenerator(_handler_config(), tmp_path)

    gen._ensure_handler("2026-08-20")

    assert "filter_pipe" not in captured["kwargs"]
