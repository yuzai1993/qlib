"""Liquidity-stratified, train-only DatasetH for sample experiments."""

from __future__ import annotations

import hashlib
from typing import Sequence

import pandas as pd

from qlib.data.dataset import DatasetH

LIQUIDITY_BUCKETS = ("low", "mid", "high", "random")


def liquidity_expression(lookback: int = 20, lag: int = 1) -> str:
    """Return a causal average traded-value proxy expression."""
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    if lag < 0:
        raise ValueError("lag must be >= 0")
    base = f"Mean($vwap*$volume, {lookback})"
    return f"Ref({base}, {lag})" if lag else base


def _normalize_index(series: pd.Series) -> pd.Series:
    if not isinstance(series.index, pd.MultiIndex) or series.index.nlevels != 2:
        raise ValueError("scores must use a 2-level datetime/instrument MultiIndex")
    names = list(series.index.names)
    if "datetime" in names and "instrument" in names:
        return series.reorder_levels(["datetime", "instrument"]).sort_index()
    result = series.copy()
    result.index = result.index.set_names(["datetime", "instrument"])
    return result.sort_index()


def _stable_random_values(index: pd.MultiIndex, salt: str) -> pd.Series:
    values = []
    for dt, instrument in index:
        payload = f"{salt}|{pd.Timestamp(dt).date()}|{instrument}".encode()
        digest = hashlib.blake2b(payload, digest_size=8).digest()
        values.append(int.from_bytes(digest, byteorder="big", signed=False))
    return pd.Series(values, index=index, dtype="uint64")


def select_daily_bucket(
    scores: pd.Series,
    *,
    bucket: str,
    n_buckets: int = 3,
    random_salt: str = "csi1000-liquidity-v1",
) -> pd.Series:
    """Select a deterministic daily liquidity third (or a random control third)."""
    if bucket not in LIQUIDITY_BUCKETS:
        raise ValueError(f"unsupported bucket: {bucket}")
    if n_buckets != 3:
        raise ValueError("this experiment requires exactly three buckets")

    scores = _normalize_index(scores)
    eligible = scores.notna()
    ranking_values = (
        _stable_random_values(scores.index, random_salt)
        if bucket == "random"
        else scores
    )
    ranks = ranking_values[eligible].groupby(level="datetime").rank(
        method="first", pct=True
    )
    lower = 1.0 / n_buckets
    upper = 2.0 / n_buckets
    if bucket in ("low", "random"):
        selected = ranks <= lower
    elif bucket == "mid":
        selected = (ranks > lower) & (ranks <= upper)
    else:
        selected = ranks > upper

    mask = pd.Series(False, index=scores.index)
    mask.loc[selected.index] = selected.astype(bool)
    return mask


class LiquiditySegmentDatasetH(DatasetH):
    """DatasetH that filters only the train segment by a daily sample bucket."""

    def __init__(
        self,
        *args,
        liquidity_bucket: str,
        n_buckets: int = 3,
        lookback: int = 20,
        lag: int = 1,
        random_salt: str = "csi1000-liquidity-v1",
        **kwargs,
    ):
        if liquidity_bucket not in LIQUIDITY_BUCKETS:
            raise ValueError(f"unsupported liquidity_bucket: {liquidity_bucket}")
        self.liquidity_bucket = liquidity_bucket
        self.n_buckets = n_buckets
        self.lookback = lookback
        self.lag = lag
        self.random_salt = random_salt
        super().__init__(*args, **kwargs)

    def _load_scores_for_index(self, index: pd.MultiIndex) -> pd.Series:
        if self.liquidity_bucket == "random":
            return pd.Series(1.0, index=index)

        from qlib.data import D

        instruments = self.handler.instruments
        if isinstance(instruments, str):
            instruments = D.instruments(instruments)
        start = str(pd.Timestamp(index.get_level_values("datetime").min()).date())
        end = str(pd.Timestamp(index.get_level_values("datetime").max()).date())
        expression = liquidity_expression(self.lookback, self.lag)
        frame = D.features(
            instruments,
            [expression],
            start_time=start,
            end_time=end,
            disk_cache=0,
        )
        series = frame.iloc[:, 0]
        series.index = series.index.set_names(["instrument", "datetime"])
        series = series.swaplevel().sort_index()
        return series.reindex(index)

    def _filter_train(self, frame: pd.DataFrame) -> pd.DataFrame:
        scores = self._load_scores_for_index(frame.index)
        mask = select_daily_bucket(
            scores,
            bucket=self.liquidity_bucket,
            n_buckets=self.n_buckets,
            random_salt=self.random_salt,
        )
        return frame.loc[mask]

    def prepare(self, segments, col_set="__all", data_key="infer", **kwargs):
        prepared = super().prepare(
            segments, col_set=col_set, data_key=data_key, **kwargs
        )
        if isinstance(segments, str):
            return self._filter_train(prepared) if segments == "train" else prepared
        if isinstance(segments, (list, tuple)) and isinstance(prepared, Sequence):
            return [
                self._filter_train(frame) if name == "train" else frame
                for name, frame in zip(segments, prepared)
            ]
        return prepared
