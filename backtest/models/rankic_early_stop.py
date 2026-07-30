"""Shared inputs for RankIC-based early stopping on the fixed validation set."""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

from qlib.contrib.model.double_ensemble import DEnsembleModel
from qlib.data import D
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP

from backtest.scripts.eval_protocol import daily_ic

VALID_SEGMENT = ("2020-01-13", "2021-07-15")
TEST_SEGMENT = ("2021-07-16", "2026-07-16")
SAFE_VALID_END = "2021-07-13"
EVAL_LABEL_EXPR = "Ref($close, -2)/Ref($close, -1)-1"


class _PreparedTrainValidDataset:
    def __init__(self, train: pd.DataFrame, valid: pd.DataFrame):
        self.train = train
        self.valid = valid

    def prepare(self, segments, *, col_set, data_key):
        if segments != ["train", "valid"]:
            raise ValueError("adapter only provides train and valid frames")
        if col_set != ["feature", "label"] or data_key != DataHandlerLP.DK_L:
            raise ValueError("adapter only provides learn features and labels")
        return self.train, self.valid


class RankICEarlyStoppingDEnsembleModel(DEnsembleModel):
    """DoubleEnsemble whose GBM submodels stop on fixed-valid daily RankIC."""

    def __init__(
        self,
        *,
        valid_segment: tuple[str, str] = VALID_SEGMENT,
        test_segment: tuple[str, str] = TEST_SEGMENT,
        **kwargs,
    ):
        base_model = kwargs.get("base_model", "gbm")
        loss = kwargs.get("loss", "mse")
        early_stopping_rounds = kwargs.get("early_stopping_rounds")
        if base_model != "gbm":
            raise ValueError("base_model must be gbm")
        if loss != "mse":
            raise ValueError("loss must be mse")
        if early_stopping_rounds is None or early_stopping_rounds <= 0:
            raise ValueError("early_stopping_rounds must be positive")
        if not _segment_matches(valid_segment, VALID_SEGMENT):
            raise ValueError("valid_segment must use the fixed boundary")
        if not _segment_matches(test_segment, TEST_SEGMENT):
            raise ValueError("test_segment must use the fixed boundary")

        super().__init__(**kwargs)
        self.rankic_evals_result: list[dict] = []

    def fit(self, dataset: DatasetH):
        df_train = dataset.prepare(
            "train",
            col_set=["feature", "label"],
            data_key=DataHandlerLP.DK_L,
        )
        df_valid = fixed_next_day_valid_frame(dataset)
        self.rankic_evals_result = []
        return super().fit(_PreparedTrainValidDataset(df_train, df_valid))

    def train_submodel(self, df_train, df_valid, weights, features):
        dtrain, dvalid = self._prepare_data_gbm(df_train, df_valid, weights, features)
        valid_index = df_valid.index
        evals_result = {}

        def rankic_feval(pred, eval_data):
            score = mean_daily_rank_ic(pred, eval_data.get_label(), valid_index)
            return "daily_rank_ic", score, True

        model = lgb.train(
            params={**self.params, "objective": "mse", "metric": "None"},
            train_set=dtrain,
            num_boost_round=self.epochs,
            valid_sets=[dvalid],
            valid_names=["valid"],
            feval=rankic_feval,
            callbacks=[
                lgb.log_evaluation(20),
                lgb.record_evaluation(evals_result),
                lgb.early_stopping(
                    self.early_stopping_rounds,
                    first_metric_only=True,
                ),
            ],
        )
        self.rankic_evals_result.append(
            {
                "best_iteration": model.best_iteration,
                "best_score": model.best_score["valid"]["daily_rank_ic"],
                "valid_days": valid_index.get_level_values("datetime").nunique(),
            }
        )
        return model


def mean_daily_rank_ic(
    pred: np.ndarray,
    label: np.ndarray,
    index: pd.MultiIndex,
    min_count: int = 20,
) -> float:
    """Return the equally weighted mean of finite daily RankIC values."""
    if len(pred) != len(label) or len(pred) != len(index):
        raise ValueError("prediction, label, and index lengths must match")
    pred_s = pd.Series(np.asarray(pred, dtype=float), index=index, name="pred")
    label_s = pd.Series(np.asarray(label, dtype=float), index=index, name="label")
    daily = daily_ic(pred_s, label_s, min_count=min_count)
    values = daily["rank_ic"].dropna()
    if values.empty:
        raise ValueError("valid RankIC contains no finite trading days")
    return float(values.mean())


def _segment_matches(segment: tuple[str, str], expected: tuple[str, str]) -> bool:
    return tuple(pd.Timestamp(value) for value in segment) == tuple(pd.Timestamp(value) for value in expected)


def _as_datetime_instrument_index(index: pd.MultiIndex) -> pd.MultiIndex:
    if not isinstance(index, pd.MultiIndex) or set(index.names) != {"datetime", "instrument"}:
        raise ValueError("label index must have datetime and instrument levels")
    return index.reorder_levels(["datetime", "instrument"])


def fixed_next_day_valid_frame(dataset: DatasetH) -> pd.DataFrame:
    """Return fixed-safe valid features with their unprocessed next-day labels.

    Labels are fetched directly from Qlib rather than from the training handler,
    so RankIC early stopping always uses the common one-day evaluation target.
    """
    if not _segment_matches(dataset.segments.get("valid", ()), VALID_SEGMENT) or not _segment_matches(
        dataset.segments.get("test", ()), TEST_SEGMENT
    ):
        raise ValueError("dataset must use the fixed valid/test segments")

    features = dataset.prepare(
        slice(VALID_SEGMENT[0], SAFE_VALID_END),
        col_set="feature",
        data_key=DataHandlerLP.DK_I,
    )
    if not isinstance(features.index, pd.MultiIndex) or features.index.names != ["datetime", "instrument"]:
        raise ValueError("feature index must have datetime and instrument levels")
    if not features.index.is_unique:
        raise ValueError("feature index must be unique")

    instruments = features.index.get_level_values("instrument").unique().tolist()
    label_frame = D.features(
        instruments,
        [EVAL_LABEL_EXPR],
        start_time=VALID_SEGMENT[0],
        end_time=VALID_SEGMENT[1],
    )
    if label_frame.shape[1] != 1:
        raise ValueError("expected exactly one evaluation label column")
    label = label_frame.iloc[:, 0].copy()
    label.index = _as_datetime_instrument_index(label.index)
    label = label.loc[label.index.isin(features.index)]
    if not label.index.is_unique:
        raise ValueError("label index must be unique")
    label = label.sort_index()
    if not label.index.equals(features.index.sort_values()):
        raise ValueError("label index must exactly match feature index")
    label = label.reindex(features.index)

    frame = features.copy()
    if not isinstance(frame.columns, pd.MultiIndex):
        frame.columns = pd.MultiIndex.from_product([["feature"], frame.columns])
    frame[("label", "LABEL0")] = label
    frame = frame.dropna(subset=[("label", "LABEL0")])
    counts = frame.groupby(level="datetime").size()
    if counts.empty or (counts < 20).any():
        raise ValueError("valid frame requires at least 20 valid instruments per day")
    return frame
