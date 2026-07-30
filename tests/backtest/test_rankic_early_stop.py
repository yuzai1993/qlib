"""Tests for the fixed-valid RankIC early-stopping inputs."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.models.rankic_early_stop import (
    EVAL_LABEL_EXPR,
    RankICEarlyStoppingDEnsembleModel,
    SAFE_VALID_END,
    fixed_next_day_valid_frame,
    mean_daily_rank_ic,
)
from backtest.scripts.eval_protocol import daily_ic


def test_mean_daily_rank_ic_is_equal_weighted_by_day():
    """Fails if RankIC is averaged over rows instead of trading days."""
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2020-01-13", "2020-01-14"]), ["A", "B", "C"]],
        names=["datetime", "instrument"],
    )
    pred = np.array([1, 2, 3, 1, 2, 3], dtype=float)
    label = np.array([1, 2, 3, 3, 2, 1], dtype=float)

    assert mean_daily_rank_ic(pred, label, index, min_count=3) == pytest.approx(0.0)


def test_mean_daily_rank_ic_matches_daily_ic_with_ties_nan_and_shuffled_rows():
    """Fails if the metric diverges from the shared daily-IC protocol."""
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2020-01-14"), "C"),
            (pd.Timestamp("2020-01-13"), "B"),
            (pd.Timestamp("2020-01-14"), "A"),
            (pd.Timestamp("2020-01-13"), "A"),
            (pd.Timestamp("2020-01-14"), "B"),
            (pd.Timestamp("2020-01-13"), "C"),
            (pd.Timestamp("2020-01-13"), "D"),
        ],
        names=["datetime", "instrument"],
    )
    pred_series = pd.Series([3, 1, 1, 1, 2, 3, np.nan], index=index)
    label_series = pd.Series([1, 1, 3, 1, 2, 3, 4], index=index)
    expected = daily_ic(pred_series, label_series, min_count=3)["rank_ic"].mean()

    actual = mean_daily_rank_ic(
        pred_series.to_numpy(),
        label_series.to_numpy(),
        pred_series.index,
        min_count=3,
    )

    assert actual == pytest.approx(expected)


@pytest.mark.parametrize(
    "pred,label,index",
    [
        (np.array([1.0]), np.array([], dtype=float), pd.Index([0])),
        (np.array([1.0]), np.array([1.0]), pd.Index([], dtype=int)),
    ],
)
def test_mean_daily_rank_ic_rejects_mismatched_input_lengths(pred, label, index):
    """Fails if mismatched arrays silently construct a malformed series."""
    with pytest.raises(ValueError, match="lengths must match"):
        mean_daily_rank_ic(pred, label, index)


def test_mean_daily_rank_ic_rejects_when_no_day_has_finite_rank_ic():
    """Fails if a metric with no usable day becomes a deceptive NaN score."""
    index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2020-01-13")], ["A", "B", "C"]],
        names=["datetime", "instrument"],
    )

    with pytest.raises(ValueError, match="no finite trading days"):
        mean_daily_rank_ic(np.array([1.0, 2.0, 3.0]), np.array([np.nan, np.nan, np.nan]), index, min_count=3)


class _FakeDataset:
    def __init__(self, features: pd.DataFrame, *, valid=("2020-01-13", "2021-07-15"), test=("2021-07-16", "2026-07-16")):
        self.segments = {"valid": valid, "test": test}
        self.features = features
        self.prepared_segments = []

    def prepare(self, segment, *, col_set, data_key):
        self.prepared_segments.append(segment)
        assert col_set == "feature"
        assert data_key == "infer"
        return self.features


class _FakeDataProvider:
    def __init__(self, label_frame: pd.DataFrame):
        self.label_frame = label_frame
        self.feature_calls = []

    def calendar(self, *, start_time, end_time):
        assert start_time == "2020-01-13"
        assert end_time == "2021-07-15"
        return pd.date_range(start_time, end_time, freq="B")

    def features(self, instruments, fields, *, start_time, end_time):
        self.feature_calls.append((instruments, fields, start_time, end_time))
        return self.label_frame


def _features(*, instruments=21, days=("2020-01-13", "2021-07-13")):
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(days), [f"S{i:03d}" for i in range(instruments)]],
        names=["datetime", "instrument"],
    )
    columns = pd.MultiIndex.from_tuples([("feature", "f0")])
    return pd.DataFrame(np.arange(len(index), dtype=float), index=index, columns=columns)


def _provider_for(features: pd.DataFrame, *, nan_at=None, label_index=None):
    index = features.index if label_index is None else label_index
    if label_index is None:
        post_anchor = pd.MultiIndex.from_product(
            [
                pd.to_datetime(["2021-07-14", "2021-07-15"]),
                features.index.get_level_values("instrument").unique(),
            ],
            names=["datetime", "instrument"],
        )
        index = index.append(post_anchor)
    if index.names == ["datetime", "instrument"]:
        index = index.swaplevel().set_names(["instrument", "datetime"])
    values = np.arange(len(index), dtype=float)
    if nan_at is not None:
        values[nan_at] = np.nan
    return _FakeDataProvider(pd.DataFrame({EVAL_LABEL_EXPR: values}, index=index))


def test_fixed_next_day_valid_frame_uses_safe_fixed_boundary_and_returns_aligned_label(monkeypatch):
    """Fails if valid data leaks into the next-day label period or uses test data."""
    from backtest.models import rankic_early_stop

    features = _features()
    dataset = _FakeDataset(features)
    provider = _provider_for(features, nan_at=0)
    monkeypatch.setattr(rankic_early_stop, "D", provider)

    frame = fixed_next_day_valid_frame(dataset)

    assert frame.index.get_level_values("datetime").max() == pd.Timestamp(SAFE_VALID_END)
    assert dataset.prepared_segments == [slice("2020-01-13", "2021-07-13")]
    assert "test" not in dataset.prepared_segments
    assert frame.columns.tolist() == [("feature", "f0"), ("label", "LABEL0")]
    assert frame.index.equals(features.index[1:])
    assert frame[("label", "LABEL0")].notna().all()
    assert provider.feature_calls == [
        ([f"S{i:03d}" for i in range(21)], [EVAL_LABEL_EXPR], "2020-01-13", "2021-07-15"),
    ]


def test_fixed_next_day_valid_frame_discards_post_anchor_label_rows(monkeypatch):
    """Fails if the 7/14--7/15 label-query rows prevent safe-frame alignment."""
    from backtest.models import rankic_early_stop

    features = _features()
    dataset = _FakeDataset(features)
    provider = _provider_for(features)
    monkeypatch.setattr(rankic_early_stop, "D", provider)

    frame = fixed_next_day_valid_frame(dataset)

    assert len(provider.label_frame) == len(features) + 2 * 21
    assert frame.index.equals(features.index)


@pytest.mark.parametrize(
    "segments",
    [
        {"valid": ("2020-01-14", "2021-07-15"), "test": ("2021-07-16", "2026-07-16")},
        {"valid": ("2020-01-13", "2021-07-15"), "test": ("2021-07-15", "2026-07-16")},
    ],
)
def test_fixed_next_day_valid_frame_rejects_changed_valid_or_test_boundaries(monkeypatch, segments):
    """Fails if a configuration change can silently alter early-stopping data."""
    from backtest.models import rankic_early_stop

    features = _features()
    dataset = _FakeDataset(features, valid=segments["valid"], test=segments["test"])
    monkeypatch.setattr(rankic_early_stop, "D", _provider_for(features))

    with pytest.raises(ValueError, match="fixed valid/test segments"):
        fixed_next_day_valid_frame(dataset)


def test_fixed_next_day_valid_frame_rejects_duplicate_feature_index(monkeypatch):
    """Fails if ambiguous feature rows could be paired to a label more than once."""
    from backtest.models import rankic_early_stop

    features = _features()
    duplicate = pd.concat([features, features.iloc[[0]]])
    dataset = _FakeDataset(duplicate)
    monkeypatch.setattr(rankic_early_stop, "D", _provider_for(duplicate))

    with pytest.raises(ValueError, match="feature index must be unique"):
        fixed_next_day_valid_frame(dataset)


def test_fixed_next_day_valid_frame_rejects_days_with_fewer_than_twenty_valid_instruments(monkeypatch):
    """Fails if an early-stopping day cannot produce the required RankIC."""
    from backtest.models import rankic_early_stop

    features = _features(instruments=19)
    dataset = _FakeDataset(features)
    monkeypatch.setattr(rankic_early_stop, "D", _provider_for(features))

    with pytest.raises(ValueError, match="at least 20 valid instruments per day"):
        fixed_next_day_valid_frame(dataset)


def test_fixed_next_day_valid_frame_rejects_unmatched_label_index(monkeypatch):
    """Fails if label rows do not exactly correspond to prepared feature rows."""
    from backtest.models import rankic_early_stop

    features = _features()
    dataset = _FakeDataset(features)
    label_index = features.index[:-1]
    monkeypatch.setattr(rankic_early_stop, "D", _provider_for(features, label_index=label_index))

    with pytest.raises(ValueError, match="label index must exactly match feature index"):
        fixed_next_day_valid_frame(dataset)


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"base_model": "mlp"}, "base_model"),
        ({"loss": "binary"}, "loss"),
        ({"early_stopping_rounds": 0}, "early_stopping_rounds"),
        ({"early_stopping_rounds": -1}, "early_stopping_rounds"),
        ({"valid_segment": ("2020-01-14", "2021-07-15")}, "valid_segment"),
        ({"test_segment": ("2021-07-15", "2026-07-16")}, "test_segment"),
    ],
)
def test_rankic_ensemble_rejects_settings_that_change_the_fixed_protocol(kwargs, error):
    """Fails if model options can bypass GBM/MSE RankIC early-stopping rules."""
    defaults = {"early_stopping_rounds": 20}
    defaults.update(kwargs)

    with pytest.raises(ValueError, match=error):
        RankICEarlyStoppingDEnsembleModel(**defaults)


class _FakeLGBDataset:
    def __init__(self, label):
        self._label = np.asarray(label, dtype=float)

    def get_label(self):
        return self._label


class _FakeBooster:
    best_iteration = 7
    best_score = {"valid": {"daily_rank_ic": 0.25}}


def _model_frame(labels, *, days=("2020-01-13", "2020-01-14")):
    instruments = [f"S{i:03d}" for i in range(20)]
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(days), instruments],
        names=["datetime", "instrument"],
    )
    columns = pd.MultiIndex.from_tuples(
        [("feature", "f0"), ("feature", "f1"), ("label", "LABEL0")]
    )
    values = np.column_stack(
        [
            np.arange(len(index), dtype=float),
            np.arange(len(index), dtype=float) / 2,
            np.asarray(labels, dtype=float),
        ]
    )
    return pd.DataFrame(values, index=index, columns=columns)


def test_rankic_submodel_wires_only_custom_valid_metric_and_saves_diagnostics(monkeypatch):
    """Fails if LightGBM can stop on train/L2 or loses per-model RankIC diagnostics."""
    from backtest.models import rankic_early_stop

    day_up = np.arange(20, dtype=float)
    valid = _model_frame(np.concatenate([day_up, day_up[::-1]]))
    train = _model_frame(np.arange(40, dtype=float), days=("2019-12-02", "2019-12-03"))
    dtrain = _FakeLGBDataset(train[("label", "LABEL0")])
    dvalid = _FakeLGBDataset(valid[("label", "LABEL0")])
    captured = {}

    def fake_train(**kwargs):
        captured["train_kwargs"] = kwargs
        captured["evals_result"]["valid"] = {"daily_rank_ic": [0.1, 0.25]}
        return _FakeBooster()

    def fake_log_evaluation(period):
        captured["log_period"] = period
        return "log-callback"

    def fake_record_evaluation(evals_result):
        captured["evals_result"] = evals_result
        return "record-callback"

    def fake_early_stopping(stopping_rounds, *, first_metric_only=False):
        captured["early_stopping"] = (stopping_rounds, first_metric_only)
        return "early-stopping-callback"

    model = RankICEarlyStoppingDEnsembleModel(
        epochs=200,
        early_stopping_rounds=20,
        num_models=1,
    )
    monkeypatch.setattr(model, "_prepare_data_gbm", lambda *args: (dtrain, dvalid))
    monkeypatch.setattr(rankic_early_stop.lgb, "train", fake_train)
    monkeypatch.setattr(rankic_early_stop.lgb, "log_evaluation", fake_log_evaluation)
    monkeypatch.setattr(rankic_early_stop.lgb, "record_evaluation", fake_record_evaluation)
    monkeypatch.setattr(rankic_early_stop.lgb, "early_stopping", fake_early_stopping)

    booster = model.train_submodel(
        train,
        valid,
        pd.Series(np.ones(len(train))),
        train["feature"].columns,
    )

    call = captured["train_kwargs"]
    assert isinstance(booster, _FakeBooster)
    assert call["params"]["objective"] == "mse"
    assert call["params"]["metric"] == "None"
    assert call["valid_sets"] == [dvalid]
    assert call["valid_names"] == ["valid"]
    assert call["num_boost_round"] == 200
    metric_name, metric_value, higher_is_better = call["feval"](
        np.concatenate([day_up, day_up]),
        dvalid,
    )
    assert metric_name == "daily_rank_ic"
    assert metric_value == pytest.approx(0.0)
    assert higher_is_better is True
    assert captured["log_period"] == 20
    assert captured["early_stopping"] == (20, True)
    assert call["callbacks"] == [
        "log-callback",
        "record-callback",
        "early-stopping-callback",
    ]
    assert model.rankic_evals_result == [
        {"best_iteration": 7, "best_score": 0.25, "valid_days": 2}
    ]


class _FitDataset:
    def __init__(self, train, test_features):
        self.segments = {
            "train": ("2016-01-02", "2020-01-10"),
            "valid": ("2020-01-13", "2021-07-15"),
            "test": ("2021-07-16", "2026-07-16"),
        }
        self.train = train
        self.test_features = test_features
        self.prepare_calls = []

    def prepare(self, segment, *, col_set, data_key):
        self.prepare_calls.append((segment, col_set, data_key))
        if segment == "train":
            assert col_set == ["feature", "label"]
            assert data_key == "learn"
            return self.train
        if segment == "test":
            assert col_set == "feature"
            assert data_key == "infer"
            return self.test_features
        raise AssertionError(f"unexpected segment prepared: {segment!r}")


class _PredictingBooster:
    def __init__(self, value, score):
        self.value = value
        self.best_iteration = int(value)
        self.best_score = {"valid": {"daily_rank_ic": score}}

    def predict(self, values, **kwargs):
        return np.full(len(values), self.value, dtype=float)


def test_rankic_fit_keeps_h40_train_for_three_submodels_and_parent_prediction(monkeypatch):
    """Fails if the H1 valid frame leaks into SR/FS or changes parent prediction."""
    from backtest.models import rankic_early_stop

    h40_labels = np.arange(10, 50, dtype=float)
    h1_labels = np.concatenate([np.arange(20), np.arange(20)[::-1]]).astype(float)
    train = _model_frame(h40_labels, days=("2019-12-02", "2019-12-03"))
    valid = _model_frame(h1_labels)
    test_index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2021-07-16"), "S000"),
            (pd.Timestamp("2021-07-16"), "S001"),
            (pd.Timestamp("2021-07-19"), "S000"),
        ],
        names=["datetime", "instrument"],
    )
    test_features = pd.DataFrame(
        {"f0": [1.0, 2.0, 3.0], "f1": [0.5, 1.0, 1.5]},
        index=test_index,
    )
    dataset = _FitDataset(train, test_features)
    trained_labels = []
    valid_labels = []
    retrieve_labels = []
    sample_losses = []
    feature_labels = []
    boosters = iter(
        [
            _PredictingBooster(1.0, 0.11),
            _PredictingBooster(3.0, 0.22),
            _PredictingBooster(8.0, 0.33),
        ]
    )

    def fake_fixed_valid(actual_dataset):
        assert actual_dataset is dataset
        return valid

    def fake_train(**kwargs):
        trained_labels.append(kwargs["train_set"].get_label().copy())
        valid_labels.append(kwargs["valid_sets"][0].get_label().copy())
        return next(boosters)

    model = RankICEarlyStoppingDEnsembleModel(
        epochs=200,
        early_stopping_rounds=20,
        num_models=3,
        enable_sr=True,
        enable_fs=True,
        decay=0.5,
    )

    def fake_retrieve_loss_curve(submodel, df_train, features):
        retrieve_labels.append(df_train[("label", "LABEL0")].to_numpy().copy())
        return pd.DataFrame(np.ones((len(df_train), 2)))

    def fake_sample_reweight(loss_curve, loss_values, k_th):
        sample_losses.append(loss_values.to_numpy().copy())
        return pd.Series(np.ones(len(loss_values), dtype=float))

    def fake_feature_selection(df_train, loss_values):
        feature_labels.append(df_train[("label", "LABEL0")].to_numpy().copy())
        return df_train["feature"].columns

    monkeypatch.setattr(rankic_early_stop, "fixed_next_day_valid_frame", fake_fixed_valid)
    monkeypatch.setattr(rankic_early_stop.lgb, "train", fake_train)
    monkeypatch.setattr(model, "retrieve_loss_curve", fake_retrieve_loss_curve)
    monkeypatch.setattr(model, "sample_reweight", fake_sample_reweight)
    monkeypatch.setattr(model, "feature_selection", fake_feature_selection)

    model.fit(dataset)

    assert dataset.prepare_calls == [("train", ["feature", "label"], "learn")]
    assert len(model.ensemble) == 3
    assert all(np.array_equal(labels, h40_labels) for labels in trained_labels)
    assert all(np.array_equal(labels, h1_labels) for labels in valid_labels)
    assert all(np.array_equal(labels, h40_labels) for labels in retrieve_labels)
    assert all(np.array_equal(labels, h40_labels) for labels in feature_labels)
    assert np.array_equal(sample_losses[0], (h40_labels - 1.0) ** 2)
    assert np.array_equal(sample_losses[1], (h40_labels - 2.0) ** 2)
    assert [item["best_score"] for item in model.rankic_evals_result] == [0.11, 0.22, 0.33]

    pred = model.predict(dataset)

    assert pred.index.equals(test_index)
    assert pred.to_numpy() == pytest.approx(np.full(len(test_index), 4.0))
