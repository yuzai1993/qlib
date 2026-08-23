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
from backtest.scripts.eval_ic_multi_pool import daily_head_panel, summarize_head_series

ES_METRICS = ("daily_rank_ic", "top5_h5_net_ann", "top3_h5_net_ann")
HEAD_ES_METRICS = ("top5_h5_net_ann", "top3_h5_net_ann")


def topk_h_net_ann(
    pred: np.ndarray,
    label: np.ndarray,
    index: pd.MultiIndex,
    *,
    k: int,
    h: int,
    tradable: Optional[pd.Series] = None,
) -> float:
    """Valid 上 top-k × h 扣费净年化；与评估主格同一套 summarize。"""
    pred_s = pd.Series(np.asarray(pred, dtype=float), index=index, name="pred")
    label_s = pd.Series(np.asarray(label, dtype=float), index=index, name="label")
    cell = daily_head_panel(pred_s, label_s, [k], tradable=tradable)[int(k)]
    out = summarize_head_series(
        cell["excess"],
        int(h),
        sets=cell.get("sets"),
        k=int(k),
        port=cell.get("port"),
        bench=cell.get("bench"),
    )
    score = out.get("net_ann")
    if score is None or not np.isfinite(score):
        return float("-inf")
    return float(score)


def top3_h5_net_ann(
    pred: np.ndarray,
    label: np.ndarray,
    index: pd.MultiIndex,
    tradable: Optional[pd.Series] = None,
) -> float:
    """Valid 上 top3×h5 扣费净年化（现行官方主格）。"""
    return topk_h_net_ann(pred, label, index, k=3, h=5, tradable=tradable)


def top5_h5_net_ann(
    pred: np.ndarray,
    label: np.ndarray,
    index: pd.MultiIndex,
    tradable: Optional[pd.Series] = None,
) -> float:
    """Valid 上 top5×h5 扣费净年化（v2 历史早停尺子）。"""
    return topk_h_net_ann(pred, label, index, k=5, h=5, tradable=tradable)


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


def pack_loss_curve_edges(loss_curve: np.ndarray) -> tuple[np.ndarray, int]:
    """只保留 SR 真正用到的前/后 10% 树；列顺序为 [start|end]。"""
    arr = np.asarray(loss_curve)
    if arr.ndim != 2:
        raise ValueError("loss_curve must be 2-D")
    _n, trees = arr.shape
    part = max(int(trees * 0.1), 1)
    packed = np.concatenate([arr[:, :part], arr[:, -part:]], axis=1)
    return packed, part


def compact_sample_reweight(
    packed_curve,
    loss_values,
    k_th: int,
    *,
    part: int,
    alpha1: float,
    alpha2: float,
    bins_sr: int,
    decay: float,
) -> pd.Series:
    """与 DEnsembleModel.sample_reweight 同一套公式，只对 start/end 列做 rank。"""
    arr = np.asarray(packed_curve, dtype=np.float64)
    if arr.shape[1] != 2 * part:
        raise ValueError(f"packed curve must have 2*part columns, got {arr.shape[1]} vs {2 * part}")
    n = arr.shape[0]
    start = np.column_stack(
        [pd.Series(arr[:, j]).rank(pct=True).to_numpy() for j in range(part)]
    )
    end = np.column_stack(
        [pd.Series(arr[:, part + j]).rank(pct=True).to_numpy() for j in range(part)]
    )
    l_start = start.mean(axis=1)
    l_end = end.mean(axis=1)
    h1 = pd.Series(np.asarray(-np.asarray(loss_values), dtype=float)).rank(pct=True).to_numpy()
    h2 = pd.Series(l_end / l_start).rank(pct=True).to_numpy()
    h = pd.DataFrame({"h_value": alpha1 * h1 + alpha2 * h2})
    h["bins"] = pd.cut(h["h_value"], bins_sr)
    h_avg = h.groupby("bins", group_keys=False, observed=False)["h_value"].mean()
    weights = pd.Series(np.zeros(n, dtype=float))
    for b in h_avg.index:
        weights[h["bins"] == b] = 1.0 / (decay**k_th * h_avg[b] + 0.1)
    return weights


class RegimeWeightedDEnsembleModel(RankICEarlyStoppingDEnsembleModel):
    """DoubleEnsemble + day 级风格平衡训练权重 + 与单模同一套早停尺子。"""

    def __init__(
        self,
        *,
        day_weights_csv: Optional[str] = None,
        es_metric: str = "daily_rank_ic",
        tradable_mask: Optional[pd.Series] = None,
        **kwargs,
    ):
        if es_metric not in ES_METRICS:
            raise ValueError(f"es_metric must be one of {ES_METRICS}, got {es_metric!r}")
        super().__init__(**kwargs)
        self.day_weights = (
            load_day_weights(day_weights_csv) if day_weights_csv is not None else None
        )
        self.es_metric = es_metric
        self.tradable_mask = tradable_mask
        self._sr_part: Optional[int] = None

    def train_submodel(self, df_train, df_valid, weights, features):
        if self.day_weights is not None:
            weights = compose_day_weights(df_train.index, weights, self.day_weights)
        dtrain, dvalid = self._prepare_data_gbm(df_train, df_valid, weights, features)
        valid_index = df_valid.index
        tradable = self.tradable_mask
        if tradable is not None:
            tradable = tradable.reindex(valid_index)
        evals_result = {}
        metric_name = self.es_metric

        def _feval(pred, eval_data):
            y = eval_data.get_label()
            if metric_name == "top3_h5_net_ann":
                score = top3_h5_net_ann(pred, y, valid_index, tradable=tradable)
                return metric_name, score, True
            if metric_name == "top5_h5_net_ann":
                score = top5_h5_net_ann(pred, y, valid_index, tradable=tradable)
                return metric_name, score, True
            score = mean_daily_rank_ic(pred, y, valid_index)
            return "daily_rank_ic", score, True

        model = lgb.train(
            params={**self.params, "objective": "mse", "metric": "None"},
            train_set=dtrain,
            num_boost_round=self.epochs,
            valid_sets=[dvalid],
            valid_names=["valid"],
            feval=_feval,
            callbacks=[
                lgb.log_evaluation(20),
                lgb.record_evaluation(evals_result),
                lgb.early_stopping(self.early_stopping_rounds, first_metric_only=True),
            ],
        )
        self.rankic_evals_result.append(
            {
                "es_metric": metric_name,
                "best_iteration": model.best_iteration,
                "best_score": model.best_score["valid"][metric_name],
                "valid_days": valid_index.get_level_values("datetime").nunique(),
            }
        )
        return model

    def retrieve_loss_curve(self, model, df_train, features):
        """只物化 SR 需要的前/后 10% 树，float32，避免全A 上 NxT rank 把 16G 机器换死。"""
        if self.base_model != "gbm":
            raise ValueError("not implemented yet")
        num_trees = model.num_trees()
        x_train, y_train = df_train["feature"].loc[:, features], df_train["label"]
        if y_train.values.ndim == 2 and y_train.values.shape[1] == 1:
            y_train = np.squeeze(y_train.values)
        else:
            raise ValueError("LightGBM doesn't support multi-label training")
        n = x_train.shape[0]
        part = max(int(num_trees * 0.1), 1)
        packed = np.zeros((n, 2 * part), dtype=np.float32)
        pred_tree = np.zeros(n, dtype=float)
        x_values = x_train.to_numpy(copy=False)
        print(
            f"[sr] retrieve_loss_curve trees={num_trees} keep={2 * part} rows={n}",
            flush=True,
        )
        for i_tree in range(num_trees):
            pred_tree += model.predict(x_values, start_iteration=i_tree, num_iteration=1)
            if i_tree < part:
                packed[:, i_tree] = self.get_loss(y_train, pred_tree)
            if i_tree >= num_trees - part:
                packed[:, part + (i_tree - (num_trees - part))] = self.get_loss(
                    y_train, pred_tree
                )
        self._sr_part = part
        return pd.DataFrame(packed)

    def sample_reweight(self, loss_curve, loss_values, k_th):
        part = self._sr_part
        if part is None:
            return super().sample_reweight(loss_curve, loss_values, k_th)
        print(f"[sr] compact reweight k={k_th} part={part}", flush=True)
        return compact_sample_reweight(
            loss_curve,
            loss_values,
            k_th,
            part=part,
            alpha1=self.alpha1,
            alpha2=self.alpha2,
            bins_sr=self.bins_sr,
            decay=self.decay,
        )


def _validate_prepared_frames(df_train: pd.DataFrame, df_valid: pd.DataFrame) -> None:
    if not df_train.columns.get_level_values(0).isin(["feature", "label"]).all():
        raise ValueError("df_train must carry feature and label column groups")
    if not df_train["feature"].columns.equals(df_valid["feature"].columns):
        raise ValueError("train/valid feature columns must match exactly (names and order)")


class RegimeSingleLGBMModel(LGBModel):
    """B3-M 单 LightGBM + cs-rank-norm 标签 + day 级权重 + 可选早停尺子。

    - 超参冻结自 B3-M（feature-b2/range，lr=0.2）；
    - 标签处理沿用分块缓存（DropnaLabel + CSRankNorm）；
    - 默认早停：冻结 70% 分层日集上的日度 RankIC；
    - `es_metric=top3_h5_net_ann`：全A 评估窗 top3×h5 扣费净年化（现行官方主格）；
    - `es_metric=top5_h5_net_ann`：全A 评估窗 top5×h5 扣费净年化（v2 历史尺子）；
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
        es_metric: str = "daily_rank_ic",
        tradable_mask: Optional[pd.Series] = None,
        **kwargs,
    ):
        if early_stopping_rounds is None or early_stopping_rounds <= 0:
            raise ValueError("early_stopping_rounds must be positive")
        if es_metric not in ES_METRICS:
            raise ValueError(f"es_metric must be one of {ES_METRICS}, got {es_metric!r}")
        super().__init__(
            loss="mse",
            early_stopping_rounds=early_stopping_rounds,
            num_boost_round=num_boost_round,
            **kwargs,
        )
        expected_valid, _, _ = _protocol(protocol_id)
        self.protocol_id = protocol_id
        self.es_metric = es_metric
        self.tradable_mask = tradable_mask
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
        tradable = self.tradable_mask
        if tradable is not None:
            tradable = tradable.reindex(valid_index)
        evals_result: dict = {}
        metric_name = self.es_metric

        def _feval(pred, eval_data):
            y = eval_data.get_label()
            if metric_name == "top3_h5_net_ann":
                score = top3_h5_net_ann(pred, y, valid_index, tradable=tradable)
                return metric_name, score, True
            if metric_name == "top5_h5_net_ann":
                score = top5_h5_net_ann(pred, y, valid_index, tradable=tradable)
                return metric_name, score, True
            score = mean_daily_rank_ic(pred, y, valid_index)
            return "daily_rank_ic", score, True

        self.model = lgb.train(
            params={**self.params, "objective": "mse", "metric": "None"},
            train_set=dtrain,
            num_boost_round=self.num_boost_round,
            valid_sets=[dvalid],
            valid_names=["valid"],
            feval=_feval,
            callbacks=[
                lgb.log_evaluation(20),
                lgb.record_evaluation(evals_result),
                lgb.early_stopping(self.early_stopping_rounds, first_metric_only=True),
            ],
        )
        self.rankic_evals_result = [
            {
                "es_metric": metric_name,
                "best_iteration": self.model.best_iteration,
                "best_score": self.model.best_score["valid"][metric_name],
                "valid_days": valid_index.get_level_values("datetime").nunique(),
            }
        ]
        return self.model
