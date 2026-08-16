"""Shared inputs for RankIC-based early stopping on the fixed validation set."""

from __future__ import annotations

from typing import Optional

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
DEFAULT_PROTOCOL_ID = "official-v1"
_PROTOCOLS = {
    DEFAULT_PROTOCOL_ID: (VALID_SEGMENT, SAFE_VALID_END, TEST_SEGMENT),
    "post2020-forward-v1": (
        ("2023-01-03", "2024-06-28"),
        "2024-06-26",
        ("2024-07-01", "2026-07-16"),
    ),
    # regime-adapt 计划 v3: valid = test = 冻结分层采样日集（用户 2026-08-09
    # 明示豁免"禁止用 test 调参"，仅限早停轮数）；须配合 valid_dates_csv 使用。
    "regime-adapt-v1": (
        ("2020-08-03", "2026-07-31"),
        "2026-07-31",
        ("2020-08-03", "2026-07-31"),
    ),
}


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
        protocol_id: str = DEFAULT_PROTOCOL_ID,
        valid_segment: Optional[tuple[str, str]] = None,
        test_segment: Optional[tuple[str, str]] = None,
        valid_dates_csv: Optional[str] = None,
        **kwargs,
    ):
        expected_valid, _, expected_test = _protocol(protocol_id)
        if valid_segment is None:
            valid_segment = expected_valid
        if test_segment is None:
            test_segment = expected_test
        base_model = kwargs.get("base_model", "gbm")
        loss = kwargs.get("loss", "mse")
        early_stopping_rounds = kwargs.get("early_stopping_rounds")
        if base_model != "gbm":
            raise ValueError("base_model must be gbm")
        if loss != "mse":
            raise ValueError("loss must be mse")
        if early_stopping_rounds is None or early_stopping_rounds <= 0:
            raise ValueError("early_stopping_rounds must be positive")
        if not _segment_matches(valid_segment, expected_valid):
            raise ValueError("valid_segment must use the fixed boundary")
        if not _segment_matches(test_segment, expected_test):
            raise ValueError("test_segment must use the fixed boundary")

        super().__init__(**kwargs)
        self.protocol_id = protocol_id
        self.valid_dates = load_valid_dates(valid_dates_csv, expected_valid)
        self.rankic_evals_result: list[dict] = []

    def fit(self, dataset: DatasetH):
        df_train = dataset.prepare(
            "train",
            col_set=["feature", "label"],
            data_key=DataHandlerLP.DK_L,
        )
        valid_kwargs = {} if self.valid_dates is None else {"valid_dates": self.valid_dates}
        df_valid = fixed_next_day_valid_frame(
            dataset,
            protocol_id=self.protocol_id,
            **valid_kwargs,
        )
        self.rankic_evals_result = []
        return super().fit(_PreparedTrainValidDataset(df_train, df_valid))

    def fit_prepared(self, df_train: pd.DataFrame, df_valid: pd.DataFrame):
        """分块准备架构入口（regime-adapt 计划 7.2 节）：直接用预组装的
        train（DK_L, feature+label）与 valid（feature + 次日 label）帧训练，
        绕过全跨度 handler；早停逻辑与 fit 完全一致。"""
        if not df_train.columns.get_level_values(0).isin(["feature", "label"]).all():
            raise ValueError("df_train must carry feature and label column groups")
        train_feats = df_train["feature"].columns
        valid_feats = df_valid["feature"].columns
        if not train_feats.equals(valid_feats):
            raise ValueError("train/valid feature columns must match exactly (names and order)")
        self.rankic_evals_result = []
        return DEnsembleModel.fit(self, _PreparedTrainValidDataset(df_train, df_valid))

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


def load_valid_dates(
    valid_dates_csv: Optional[str],
    valid_segment: tuple[str, str],
) -> Optional[pd.DatetimeIndex]:
    """读取非连续 valid 日期清单（date 列，支持 # 注释头），校验落在 valid 段内。"""
    if valid_dates_csv is None:
        return None
    df = pd.read_csv(valid_dates_csv, comment="#")
    if "date" not in df.columns:
        raise ValueError(f"valid dates csv must contain a 'date' column: {valid_dates_csv}")
    dates = pd.DatetimeIndex(pd.to_datetime(df["date"])).unique().sort_values()
    if dates.empty:
        raise ValueError(f"valid dates csv is empty: {valid_dates_csv}")
    lo, hi = (pd.Timestamp(v) for v in valid_segment)
    if dates.min() < lo or dates.max() > hi:
        raise ValueError("valid dates must fall inside the protocol valid segment")
    return dates


def _segment_matches(segment: tuple[str, str], expected: tuple[str, str]) -> bool:
    return tuple(pd.Timestamp(value) for value in segment) == tuple(pd.Timestamp(value) for value in expected)


def _protocol(protocol_id: str):
    try:
        return _PROTOCOLS[protocol_id]
    except KeyError as exc:
        raise ValueError(f"unknown protocol_id: {protocol_id!r}") from exc


def _as_datetime_instrument_index(index: pd.MultiIndex) -> pd.MultiIndex:
    if not isinstance(index, pd.MultiIndex) or set(index.names) != {"datetime", "instrument"}:
        raise ValueError("label index must have datetime and instrument levels")
    return index.reorder_levels(["datetime", "instrument"])


def fixed_next_day_valid_frame(
    dataset: DatasetH,
    *,
    protocol_id: str = DEFAULT_PROTOCOL_ID,
    valid_dates: Optional[pd.DatetimeIndex] = None,
) -> pd.DataFrame:
    """Return fixed-safe valid features with their unprocessed next-day labels.

    Labels are fetched directly from Qlib rather than from the training handler,
    so RankIC early stopping always uses the common one-day evaluation target.
    When ``valid_dates`` is given, the frame is restricted to that non-contiguous
    date list (regime-adapt sampled anchor).
    """
    valid_segment, safe_valid_end, test_segment = _protocol(protocol_id)
    if not _segment_matches(dataset.segments.get("valid", ()), valid_segment) or not _segment_matches(
        dataset.segments.get("test", ()), test_segment
    ):
        raise ValueError("dataset must use the fixed valid/test segments")

    features = dataset.prepare(
        slice(valid_segment[0], safe_valid_end),
        col_set="feature",
        data_key=DataHandlerLP.DK_I,
    )
    if not isinstance(features.index, pd.MultiIndex) or features.index.names != ["datetime", "instrument"]:
        raise ValueError("feature index must have datetime and instrument levels")
    if not features.index.is_unique:
        raise ValueError("feature index must be unique")
    if valid_dates is not None:
        keep = features.index.get_level_values("datetime").isin(valid_dates)
        features = features[keep]
        if features.empty:
            raise ValueError("no valid rows remain after applying valid_dates")
        got = features.index.get_level_values("datetime").nunique()
        if got < 0.9 * len(valid_dates):
            raise ValueError(
                f"valid_dates coverage too low: {got}/{len(valid_dates)} days present"
            )

    instruments = features.index.get_level_values("instrument").unique().tolist()
    label_frame = D.features(
        instruments,
        [EVAL_LABEL_EXPR],
        start_time=valid_segment[0],
        end_time=valid_segment[1],
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
