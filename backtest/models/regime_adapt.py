"""Regime-weighted models for the regime-adapt experiment (计划 v3 第 3 节).

风格平衡训练权重：day 级权重 CSV（date,weight，由实验准备脚本按 D/F/T 目标占比
与时间衰减预生成并冻结）在训练前乘入样本权重。

两种模型臂：
- RegimeWeightedDEnsembleModel：B6-M 冻结超参的 DoubleEnsemble（阶段 2 确认用，
  权重与其自身 sample reweight 机制复合）；
- RegimeSingleLGBMModel：B3-M 冻结超参的单 LightGBM + cs-rank-norm 标签
  （阶段 1 快速筛选用，用户 2026-08-12 指示；单跑分钟级）。
"""

from __future__ import annotations

from typing import Optional

import lightgbm as lgb
import numpy as np
import pandas as pd

from qlib.contrib.model.gbdt import LGBModel

from backtest.models.rankic_early_stop import (
    RankICEarlyStoppingDEnsembleModel,
    _protocol,
    load_valid_dates,
    mean_daily_rank_ic,
)


def load_day_weights(path: str) -> pd.Series:
    """读取 day 级权重 CSV（date,weight，支持 # 注释头）。"""
    df = pd.read_csv(path, comment="#")
    if not {"date", "weight"}.issubset(df.columns):
        raise ValueError(f"day weights csv must contain date,weight columns: {path}")
    s = pd.Series(
        df["weight"].astype(float).to_numpy(),
        index=pd.DatetimeIndex(pd.to_datetime(df["date"])),
    ).sort_index()
    if s.index.has_duplicates:
        raise ValueError(f"duplicate dates in day weights csv: {path}")
    if (s <= 0).any() or not np.isfinite(s).all():
        raise ValueError(f"day weights must be positive finite: {path}")
    return s


def compose_day_weights(
    index: pd.MultiIndex,
    base_weights: pd.Series,
    day_weights: pd.Series,
) -> pd.Series:
    """把 day 级权重按样本日期乘入 base 权重（保持 base 的位置索引）。

    训练样本日期不在权重表内属于配置错误（权重表必须覆盖全部训练日），直接报错
    而不是静默取 1，避免风格平衡悄悄失效。
    """
    dts = index.get_level_values("datetime")
    mapped = day_weights.reindex(dts)
    if mapped.isna().any():
        missing = pd.DatetimeIndex(dts[mapped.isna().to_numpy()]).unique()
        raise ValueError(
            f"day weights missing {len(missing)} training dates, e.g. {list(missing[:3])}"
        )
    return pd.Series(
        np.asarray(base_weights, dtype=float) * mapped.to_numpy(),
        index=base_weights.index,
    )


class RegimeWeightedDEnsembleModel(RankICEarlyStoppingDEnsembleModel):
    """RankIC 早停 DoubleEnsemble + day 级风格平衡训练权重。"""

    def __init__(self, *, day_weights_csv: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.day_weights = (
            load_day_weights(day_weights_csv) if day_weights_csv is not None else None
        )

    def train_submodel(self, df_train, df_valid, weights, features):
        if self.day_weights is not None:
            weights = compose_day_weights(df_train.index, weights, self.day_weights)
        return super().train_submodel(df_train, df_valid, weights, features)


def _validate_prepared_frames(df_train: pd.DataFrame, df_valid: pd.DataFrame) -> None:
    if not df_train.columns.get_level_values(0).isin(["feature", "label"]).all():
        raise ValueError("df_train must carry feature and label column groups")
    if not df_train["feature"].columns.equals(df_valid["feature"].columns):
        raise ValueError("train/valid feature columns must match exactly (names and order)")


class RegimeSingleLGBMModel(LGBModel):
    """B3-M 单 LightGBM + cs-rank-norm 标签 + day 级权重 + RankIC 早停（阶段 1 筛选臂）。

    - 超参冻结自 B3-M（feature-b2/range，lr=0.2）；
    - 标签处理沿用分块缓存（DropnaLabel + CSRankNorm on H40），即 loss-design/cs-rank-norm；
    - 早停锚点与 DoubleEnsemble 臂一致：冻结 70% 分层日集上的日度 RankIC；
    - predict 走 qlib LGBModel 原生路径（dataset DK_I），与评估脚本兼容。
    """

    def __init__(
        self,
        *,
        protocol_id: str = "regime-adapt-v1",
        valid_dates_csv: Optional[str] = None,
        day_weights_csv: Optional[str] = None,
        early_stopping_rounds: int = 20,
        num_boost_round: int = 200,
        **kwargs,
    ):
        if early_stopping_rounds is None or early_stopping_rounds <= 0:
            raise ValueError("early_stopping_rounds must be positive")
        super().__init__(
            loss="mse",
            early_stopping_rounds=early_stopping_rounds,
            num_boost_round=num_boost_round,
            **kwargs,
        )
        expected_valid, _, _ = _protocol(protocol_id)
        self.protocol_id = protocol_id
        self.valid_dates = load_valid_dates(valid_dates_csv, expected_valid)
        self.day_weights = (
            load_day_weights(day_weights_csv) if day_weights_csv is not None else None
        )
        self.rankic_evals_result: list[dict] = []

    def fit_prepared(self, df_train: pd.DataFrame, df_valid: pd.DataFrame):
        _validate_prepared_frames(df_train, df_valid)
        y_train = np.squeeze(df_train["label"].values)
        weights = pd.Series(np.ones(len(df_train), dtype=float))
        if self.day_weights is not None:
            weights = compose_day_weights(df_train.index, weights, self.day_weights)
        dtrain = lgb.Dataset(
            df_train["feature"].values, label=y_train, weight=weights.to_numpy()
        )
        dvalid = lgb.Dataset(df_valid["feature"].values, label=np.squeeze(df_valid["label"].values))
        valid_index = df_valid.index
        evals_result: dict = {}

        def rankic_feval(pred, eval_data):
            score = mean_daily_rank_ic(pred, eval_data.get_label(), valid_index)
            return "daily_rank_ic", score, True

        self.model = lgb.train(
            params={**self.params, "objective": "mse", "metric": "None"},
            train_set=dtrain,
            num_boost_round=self.num_boost_round,
            valid_sets=[dvalid],
            valid_names=["valid"],
            feval=rankic_feval,
            callbacks=[
                lgb.log_evaluation(20),
                lgb.record_evaluation(evals_result),
                lgb.early_stopping(self.early_stopping_rounds, first_metric_only=True),
            ],
        )
        self.rankic_evals_result = [
            {
                "best_iteration": self.model.best_iteration,
                "best_score": self.model.best_score["valid"]["daily_rank_ic"],
                "valid_days": valid_index.get_level_values("datetime").nunique(),
            }
        ]
        return self.model
