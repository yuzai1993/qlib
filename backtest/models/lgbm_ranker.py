"""LightGBM lambdarank model with one query group per trading day.

Used by the loss-design/lambdarank experiment: replaces mse regression with a
learning-to-rank objective. Continuous labels are discretised into
``num_grades`` within-day quantile relevance grades (0 = worst), as LightGBM
ranking objectives require non-negative integer labels.

DoubleEnsemble's SR module needs per-sample loss curves, which ranking
objectives do not provide, so this model is a single LGBM (architecture
control: B3-M).
"""

from __future__ import annotations

from typing import Text, Union

import lightgbm as lgb
import numpy as np
import pandas as pd

from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.model.base import Model
from qlib.model.interpret.base import LightGBMFInt
from qlib.workflow import R


class LGBRanker(Model, LightGBMFInt):
    """LightGBM lambdarank over daily cross-sections."""

    def __init__(
        self,
        num_grades=5,
        ndcg_eval_at=100,
        early_stopping_rounds=50,
        num_boost_round=1000,
        **kwargs,
    ):
        self.num_grades = int(num_grades)
        if self.num_grades < 2:
            raise ValueError("num_grades must be >= 2")
        self.params = {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [int(ndcg_eval_at)],
            "verbosity": -1,
        }
        self.params.update(kwargs)
        self.early_stopping_rounds = early_stopping_rounds
        self.num_boost_round = num_boost_round
        self.model = None

    def _make_group_data(self, df: pd.DataFrame):
        """Return (features, integer grades, per-day group sizes)."""
        df = df.sort_index()  # ranking groups must be contiguous blocks
        y = df["label"]
        if y.values.ndim != 2 or y.values.shape[1] != 1:
            raise ValueError("LGBRanker expects a single-column label")
        label = pd.Series(np.squeeze(y.values), index=df.index)
        # level 0 of the prepared frame is datetime
        rank_pct = label.groupby(level=0).rank(pct=True)
        grades = np.ceil(rank_pct.to_numpy() * self.num_grades) - 1
        grades = np.clip(grades, 0, self.num_grades - 1).astype(int)
        group_sizes = df.groupby(level=0).size().to_numpy()
        return df["feature"].values, grades, group_sizes

    def fit(
        self,
        dataset: DatasetH,
        num_boost_round=None,
        early_stopping_rounds=None,
        verbose_eval=20,
        evals_result=None,
        **kwargs,
    ):
        if evals_result is None:
            evals_result = {}
        ds_l = []
        assert "train" in dataset.segments
        for key in ["train", "valid"]:
            if key in dataset.segments:
                df = dataset.prepare(key, col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
                if df.empty:
                    raise ValueError("Empty data from dataset, please check your dataset config.")
                x, grades, groups = self._make_group_data(df)
                ds_l.append((lgb.Dataset(x, label=grades, group=groups, free_raw_data=False), key))
        ds, names = list(zip(*ds_l))
        callbacks = [
            lgb.early_stopping(
                self.early_stopping_rounds if early_stopping_rounds is None else early_stopping_rounds
            ),
            lgb.log_evaluation(period=verbose_eval),
            lgb.record_evaluation(evals_result),
        ]
        self.model = lgb.train(
            self.params,
            ds[0],
            num_boost_round=self.num_boost_round if num_boost_round is None else num_boost_round,
            valid_sets=list(ds),
            valid_names=list(names),
            callbacks=callbacks,
            **kwargs,
        )
        for k in names:
            for key, val in evals_result[k].items():
                name = f"{key}.{k}"
                for epoch, m in enumerate(val):
                    R.log_metrics(**{name.replace("@", "_"): m}, step=epoch)

    def predict(self, dataset: DatasetH, segment: Union[Text, slice] = "test"):
        if self.model is None:
            raise ValueError("model is not fitted yet!")
        x_test = dataset.prepare(segment, col_set="feature", data_key=DataHandlerLP.DK_I)
        return pd.Series(self.model.predict(x_test.values), index=x_test.index)
