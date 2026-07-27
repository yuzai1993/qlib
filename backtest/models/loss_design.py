"""DoubleEnsemble variants for the loss-design experiment direction.

Both classes keep the B4-M DoubleEnsemble recipe intact and change exactly one
thing each:

- ``HuberDEnsembleModel``: LightGBM objective mse -> huber.
- ``HeadWeightedDEnsembleModel``: static head-of-ranking sample weights
  multiplied into the SR dynamic weights.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qlib.contrib.model.double_ensemble import DEnsembleModel


class HuberDEnsembleModel(DEnsembleModel):
    """DoubleEnsemble whose LightGBM sub-models train with the huber objective.

    The parent's SR module only consumes per-sample-loss *ranks*; huber loss is
    monotone in |error| so SR behaves identically to mse. The FS module uses
    loss magnitudes, so ``get_loss`` implements the true per-sample huber loss.

    ``huber_alpha`` maps to LightGBM's ``alpha`` parameter (the huber delta);
    it is named differently to avoid clashing with DoubleEnsemble's
    ``alpha1``/``alpha2`` SR coefficients.
    """

    def __init__(self, loss="huber", huber_alpha=0.9, **kwargs):
        if loss != "huber":
            raise ValueError("HuberDEnsembleModel only supports loss='huber'")
        super().__init__(loss=loss, **kwargs)
        self.huber_alpha = float(huber_alpha)
        self.params["alpha"] = self.huber_alpha

    def get_loss(self, label, pred):
        err = np.asarray(label, dtype=float) - np.asarray(pred, dtype=float)
        abs_err = np.abs(err)
        delta = self.huber_alpha
        return np.where(
            abs_err <= delta,
            0.5 * err**2,
            delta * (abs_err - 0.5 * delta),
        )


class HeadWeightedDEnsembleModel(DEnsembleModel):
    """DoubleEnsemble with static sample weights favouring the daily label head.

    w = 1 + head_weight_gain * max(0, (r - head_quantile) / (1 - head_quantile))

    where ``r`` is the label's daily cross-sectional rank pct. With the default
    head_quantile=0.8 / head_weight_gain=2.0, the top 20% ramps linearly from
    1x up to 3x. The static weights are multiplied with the SR dynamic weights
    right before each sub-model's ``lgb.Dataset`` is constructed, so the SR
    difficulty adjustment is preserved.
    """

    def __init__(self, head_quantile=0.8, head_weight_gain=2.0, **kwargs):
        super().__init__(**kwargs)
        self.head_quantile = float(head_quantile)
        self.head_weight_gain = float(head_weight_gain)
        if not 0.0 < self.head_quantile < 1.0:
            raise ValueError("head_quantile must be in (0, 1)")
        if self.head_weight_gain < 0.0:
            raise ValueError("head_weight_gain must be non-negative")

    def _head_weights(self, df_train) -> np.ndarray:
        label = df_train["label"]
        if label.values.ndim != 2 or label.values.shape[1] != 1:
            raise ValueError("HeadWeightedDEnsembleModel expects a single-column label")
        series = pd.Series(np.squeeze(label.values), index=df_train.index)
        # level 0 of the prepared frame is datetime -> daily cross-sectional rank
        rank_pct = series.groupby(level=0).rank(pct=True)
        excess = (rank_pct - self.head_quantile) / (1.0 - self.head_quantile)
        weights = 1.0 + self.head_weight_gain * excess.clip(lower=0.0)
        return weights.to_numpy(dtype=float)

    def _prepare_data_gbm(self, df_train, df_valid, weights, features):
        static = self._head_weights(df_train)
        combined = pd.Series(np.asarray(weights, dtype=float) * static)
        return super()._prepare_data_gbm(df_train, df_valid, combined, features)
