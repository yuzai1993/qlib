"""Signal generation: load model, prepare features, predict scores."""

import hashlib
import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import qlib
from qlib.data import D
from qlib.utils import init_instance_by_config
from qlib.data.dataset.handler import DataHandlerLP

logger = logging.getLogger("live_trading.signal")


class SignalGenerator:
    """Loads a trained model and generates prediction scores.

    Caches the handler across calls so that batch runs
    (multiple dates) only load and process data once.
    """

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self._model = None
        self._lgb_model = None
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
        model_path = (self.project_root / relative_path).resolve()
        project_root = self.project_root.resolve()
        if not model_path.is_relative_to(project_root):
            raise ValueError(
                f"model.model_path must stay inside project root: {relative_path}"
            )

        if not model_path.is_file():
            raise FileNotFoundError(f"Model artifact not found at {model_path}")

        model_bytes = model_path.read_bytes()
        expected_sha256 = model_cfg.get("sha256")
        if not expected_sha256:
            raise ValueError("model.sha256 is required for live model integrity")
        actual_sha256 = hashlib.sha256(model_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                "Model SHA-256 mismatch: "
                f"expected={expected_sha256}, actual={actual_sha256}, "
                f"path={model_path}"
            )

        self._model = pickle.loads(model_bytes)

        self._lgb_model = self._model.model
        logger.info("Model loaded from %s (sha256=%s)", model_path, actual_sha256)

    def _ensure_handler(self, end_date: str):
        """Create or extend the handler so it covers up to end_date."""
        if self._handler is not None and self._handler_end_date >= end_date:
            return

        handler_cfg = self.config["handler"]
        data_cfg = self.config["data"]

        logger.info(
            "Initializing %s handler (end_date=%s)...", handler_cfg["class"], end_date
        )
        self._handler = init_instance_by_config({
            "class": handler_cfg["class"],
            "module_path": handler_cfg["module"],
            "kwargs": {
                "instruments": data_cfg["instruments"],
                "start_time": handler_cfg["start_time"],
                "end_time": end_date,
                "fit_start_time": handler_cfg["fit_start_time"],
                "fit_end_time": handler_cfg["fit_end_time"],
                "infer_processors": handler_cfg["infer_processors"],
            },
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
        """对单日特征打分。NaN 原样传给 LightGBM（与训练口径一致，LGB 原生处理缺失）。"""
        day_features = day_features.dropna(how="all")
        raw_scores = self._lgb_model.predict(day_features.values)
        scores = pd.Series(raw_scores, index=day_features.index, name="score")
        scores = scores.dropna()

        logger.info(
            "Generated predictions for %s: %d instruments, top=%.6f, bottom=%.6f",
            target_date, len(scores), scores.max(), scores.min(),
        )
        return scores

    def predict(self, target_date: str, allow_stale: bool = True) -> pd.Series:
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
