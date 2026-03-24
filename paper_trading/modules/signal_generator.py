"""Signal generation: load model, prepare features, predict scores."""

import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import qlib
from qlib.data import D
from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset.handler import DataHandlerLP

logger = logging.getLogger("paper_trading.signal")


class SignalGenerator:
    """Loads a trained model and generates prediction scores."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self._model = None  # qlib LGBModel wrapper
        self._lgb_model = None  # underlying lightgbm.Booster

    def load_model(self):
        if self._model is not None:
            return

        model_cfg = self.config["model"]
        mlruns_dir = self.project_root / model_cfg["mlruns_dir"]
        exp_id = model_cfg["experiment_id"]
        rec_id = model_cfg["recorder_id"]
        model_path = mlruns_dir / exp_id / rec_id / "artifacts" / "trained_model"

        if not model_path.exists():
            raise FileNotFoundError(
                f"Model artifact not found at {model_path}. "
                f"Check experiment_id={exp_id}, recorder_id={rec_id}"
            )

        with open(model_path, "rb") as f:
            self._model = pickle.load(f)

        # Extract the underlying lightgbm Booster for direct prediction
        # LGBModel.model is the lgb.Booster instance
        self._lgb_model = self._model.model
        logger.info("Model loaded from %s", model_path)

    def predict(self, target_date: str) -> pd.Series:
        """Generate prediction scores for all instruments on target_date.

        Uses the underlying lightgbm Booster directly to avoid the need
        for a DatasetH object (which LGBModel.predict() requires).

        Returns a Series indexed by instrument with prediction scores.
        """
        self.load_model()

        handler_cfg = self.config["handler"]
        data_cfg = self.config["data"]

        handler = Alpha158(
            instruments=data_cfg["instruments"],
            start_time=handler_cfg["start_time"],
            end_time=target_date,
            fit_start_time=handler_cfg["start_time"],
            fit_end_time="2020-01-10",
        )

        features = handler.fetch(col_set="feature", data_key=DataHandlerLP.DK_I)

        date_index = features.index.get_level_values(0)
        target_ts = pd.Timestamp(target_date)

        if target_ts in date_index:
            day_features = features.loc[target_ts]
        else:
            last_date = date_index.max()
            logger.warning(
                "Target date %s not in features, using last available: %s",
                target_date, last_date,
            )
            day_features = features.loc[last_date]

        # Drop rows with all NaN (e.g. suspended stocks)
        day_features = day_features.dropna(how="all")
        # Fill remaining NaN with 0 (matching inference pipeline behavior)
        feature_values = day_features.fillna(0).values

        raw_scores = self._lgb_model.predict(feature_values)
        scores = pd.Series(raw_scores, index=day_features.index, name="score")
        scores = scores.dropna()

        logger.info(
            "Generated predictions for %s: %d instruments, top=%.6f, bottom=%.6f",
            target_date, len(scores), scores.max(), scores.min(),
        )
        return scores
