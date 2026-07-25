from __future__ import annotations

import importlib

import pandas as pd


def _sample_dataset():
    return importlib.import_module("backtest.datasets.liquidity_segment")


def _scores() -> pd.Series:
    index = pd.MultiIndex.from_product(
        [
            pd.to_datetime(["2020-01-02", "2020-01-03"]),
            [f"S{i}" for i in range(1, 7)],
        ],
        names=["datetime", "instrument"],
    )
    return pd.Series(list(range(1, 7)) * 2, index=index, dtype=float)


def test_liquidity_expression_is_lagged_before_daily_split():
    module = _sample_dataset()

    assert module.liquidity_expression(lookback=20, lag=1) == (
        "Ref(Mean($vwap*$volume, 20), 1)"
    )


def test_liquidity_thirds_are_daily_mutually_exclusive_and_exhaustive():
    module = _sample_dataset()
    scores = _scores()

    masks = {
        bucket: module.select_daily_bucket(scores, bucket=bucket, n_buckets=3)
        for bucket in ("low", "mid", "high")
    }

    assert all(mask.groupby(level="datetime").sum().tolist() == [2, 2] for mask in masks.values())
    assert ((masks["low"].astype(int) + masks["mid"].astype(int) + masks["high"].astype(int)) == 1).all()
    assert masks["low"][masks["low"]].index.get_level_values("instrument").unique().tolist() == ["S1", "S2"]
    assert masks["high"][masks["high"]].index.get_level_values("instrument").unique().tolist() == ["S5", "S6"]


def test_random_third_is_deterministic_balanced_and_value_independent():
    module = _sample_dataset()
    scores = _scores()

    first = module.select_daily_bucket(
        scores, bucket="random", n_buckets=3, random_salt="fixed"
    )
    second = module.select_daily_bucket(
        scores * -100, bucket="random", n_buckets=3, random_salt="fixed"
    )

    assert first.equals(second)
    assert first.groupby(level="datetime").sum().tolist() == [2, 2]


def test_dataset_filters_train_only(monkeypatch):
    module = _sample_dataset()
    frame = pd.DataFrame(
        {"feature": [1.0, 2.0, 3.0], "label": [0.1, 0.2, 0.3]},
        index=pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2020-01-02"), "S1"),
                (pd.Timestamp("2020-01-02"), "S2"),
                (pd.Timestamp("2020-01-02"), "S3"),
            ],
            names=["datetime", "instrument"],
        ),
    )
    scores = pd.Series([1.0, 2.0, 3.0], index=frame.index)
    monkeypatch.setattr(
        "qlib.data.dataset.DatasetH.prepare",
        lambda self, segments, col_set=None, data_key=None, **kwargs: frame.copy(),
    )

    dataset = module.LiquiditySegmentDatasetH.__new__(
        module.LiquiditySegmentDatasetH
    )
    dataset.liquidity_bucket = "low"
    dataset.n_buckets = 3
    dataset.random_salt = "fixed"
    dataset._load_scores_for_index = lambda index: scores.reindex(index)

    train = dataset.prepare("train")
    valid = dataset.prepare("valid")

    assert train.index.get_level_values("instrument").tolist() == ["S1"]
    pd.testing.assert_frame_equal(valid, frame)
