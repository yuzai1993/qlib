"""Tests for the fixed-valid RankIC early-stopping inputs."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.models.rankic_early_stop import (
    EVAL_LABEL_EXPR,
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
