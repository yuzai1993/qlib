"""Signal generation: load model, prepare features, predict scores."""

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from qlib.data.dataset.handler import DataHandlerLP
from qlib.utils import init_instance_by_config

from live_trading.modules.model_artifact import load_model_artifact

logger = logging.getLogger("live_trading.signal")


class _InferenceDataset:
    """Minimal DatasetH-compatible view over one inference feature frame."""

    def __init__(self, features: pd.DataFrame):
        self.features = features

    def prepare(self, segment, *, col_set, data_key):
        if col_set != "feature" or data_key != DataHandlerLP.DK_I:
            raise ValueError("inference dataset only provides infer features")
        return self.features


class SignalGenerator:
    """Loads a trained model and generates prediction scores.

    Caches the handler across calls so that batch runs
    (multiple dates) only load and process data once.
    """

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self._model = None
        self._handler = None
        self._features = None
        self._handler_end_date = None

    def load_model(self):
        if self._model is not None:
            return

        model_cfg = self.config["model"]
        relative_path = model_cfg.get("model_path")
        if not relative_path:
            raise ValueError(
                "model.model_path is required; live models must be loaded "
                "from the Git-tracked model directory"
            )
        expected_sha256 = model_cfg.get("sha256")
        if not expected_sha256:
            raise ValueError("model.sha256 is required for live model integrity")
        self._model, model_path = load_model_artifact(
            relative_path,
            expected_sha256,
            project_root=self.project_root,
        )
        logger.info("Model loaded from %s (sha256=%s)", model_path, expected_sha256)

    def _ensure_handler(self, end_date: str):
        """Create or extend the handler so it covers up to end_date."""
        if self._handler is not None and self._handler_end_date >= end_date:
            return

        handler_cfg = self.config["handler"]
        data_cfg = self.config["data"]

        logger.info(
            "Initializing %s handler (end_date=%s)...", handler_cfg["class"], end_date
        )
        handler_kwargs = {
            "instruments": data_cfg["instruments"],
            "start_time": handler_cfg["start_time"],
            "end_time": end_date,
            "fit_start_time": handler_cfg["fit_start_time"],
            "fit_end_time": handler_cfg["fit_end_time"],
            "infer_processors": handler_cfg["infer_processors"],
        }
        if "feature_groups" in handler_cfg:
            handler_kwargs["feature_groups"] = handler_cfg["feature_groups"]
        self._handler = init_instance_by_config({
            "class": handler_cfg["class"],
            "module_path": handler_cfg["module"],
            "kwargs": handler_kwargs,
        })
        self._features = self._handler.fetch(
            col_set="feature", data_key=DataHandlerLP.DK_I
        )
        self._handler_end_date = end_date
        logger.info("Handler initialized, features shape: %s", self._features.shape)

    def prepare_for_dates(self, end_date: str):
        """Pre-load handler covering all dates up to end_date.

        Call this before a batch run so that predict() reuses cached data.
        """
        self.load_model()
        self._ensure_handler(end_date)

    def _score_features(self, day_features: pd.DataFrame, target_date: str) -> pd.Series:
        """Score one day through the frozen Qlib model's public interface."""
        day_features = day_features.dropna(how="all")
        raw_scores = self._model.predict(_InferenceDataset(day_features), segment="test")
        if not isinstance(raw_scores, pd.Series):
            raise TypeError("model.predict must return a pandas Series")
        if raw_scores.index.has_duplicates:
            raise ValueError("model prediction index contains duplicates")
        if not raw_scores.index.equals(day_features.index):
            raise ValueError("model prediction index must exactly match feature index")
        scores = raw_scores.astype(float).rename("score")
        scores = scores[np.isfinite(scores)]

        if scores.empty:
            logger.warning("Generated no finite predictions for %s", target_date)
        else:
            logger.info(
                "Generated predictions for %s: %d instruments, top=%.6f, bottom=%.6f",
                target_date, len(scores), scores.max(), scores.min(),
            )
        return scores

    def predict(self, target_date: str, allow_stale: bool = False) -> pd.Series:
        """Generate prediction scores for all instruments on target_date.

        Reuses cached handler/features when available.
        """
        self.load_model()
        self._ensure_handler(target_date)

        date_index = self._features.index.get_level_values(0)
        target_ts = pd.Timestamp(target_date)

        if target_ts in date_index:
            day_features = self._features.loc[target_ts]
        else:
            last_date = date_index.max()
            if not allow_stale:
                raise ValueError(
                    f"Target date {target_date} not in features; "
                    f"last available is {last_date}"
                )
            logger.warning(
                "Target date %s not in features, using last available: %s",
                target_date, last_date,
            )
            day_features = self._features.loc[last_date]

        return self._score_features(day_features, target_date)
