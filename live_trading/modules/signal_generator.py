"""Signal generation: load model, prepare features, predict scores."""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from qlib.data.dataset.handler import DataHandlerLP
from qlib.utils import init_instance_by_config

from live_trading.modules.model_artifact import load_model_artifact

_BACKTEST_SCRIPTS = Path(__file__).resolve().parents[2] / "backtest" / "scripts"
if str(_BACKTEST_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKTEST_SCRIPTS))
from ensemble_preds import blend_score_series  # noqa: E402

logger = logging.getLogger("live_trading.signal")
# 每晚只出一天信号。Qlib 会按表达式自动向前取滚动窗，这里只决定输出多少天。
_DEFAULT_INFERENCE_LOOKBACK_DAYS = 150


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
        self._models = None
        self._handler = None
        self._features = None
        self._handler_end_date = None

    def load_model(self):
        if self._models is not None:
            return

        model_cfg = self.config["model"]
        members = model_cfg.get("members")
        if members is None:
            specs = [{
                "model_path": model_cfg.get("model_path"),
                "sha256": model_cfg.get("sha256"),
            }]
        else:
            if not isinstance(members, list) or not members:
                raise ValueError("model.members must be a non-empty list")
            specs = members

        # 先校验整份成员清单再碰磁盘：否则第一个成员的路径问题会先抛，
        # 把后面成员漏配 sha256 这种配置错误掩盖到下一次运行。
        for spec in specs:
            relative_path = spec.get("model_path")
            if not relative_path:
                raise ValueError(
                    "model_path is required; live models must be loaded "
                    "from the Git-tracked model directory"
                )
            if not spec.get("sha256"):
                raise ValueError(
                    f"sha256 is required for live model integrity: {relative_path}"
                )

        models = []
        for spec in specs:
            relative_path = spec["model_path"]
            expected_sha256 = spec["sha256"]
            model, model_path = load_model_artifact(
                relative_path,
                expected_sha256,
                project_root=self.project_root,
            )
            models.append(model)
            logger.info(
                "Model loaded from %s (sha256=%s)", model_path, expected_sha256
            )
        self._models = models

    def _inference_start_time(self, end_date: str) -> str:
        """日历日回看。YAML 里的 handler.start_time 只是研究配方，不用于加载。"""
        lookback = self.config["handler"].get(
            "inference_lookback_days", _DEFAULT_INFERENCE_LOOKBACK_DAYS
        )
        days = int(lookback)
        if days <= 0:
            raise ValueError("handler.inference_lookback_days must be positive")
        start = pd.Timestamp(end_date) - pd.Timedelta(days=days)
        return start.strftime("%Y-%m-%d")

    def _ensure_handler(self, end_date: str):
        """Create or extend the handler so it covers up to end_date."""
        if self._handler is not None and self._handler_end_date >= end_date:
            return

        handler_cfg = self.config["handler"]
        data_cfg = self.config["data"]
        start_time = self._inference_start_time(end_date)

        logger.info(
            "Initializing %s handler (start_time=%s end_date=%s)...",
            handler_cfg["class"], start_time, end_date,
        )
        handler_kwargs = {
            "instruments": data_cfg["instruments"],
            "start_time": start_time,
            "end_time": end_date,
            "fit_start_time": handler_cfg["fit_start_time"],
            "fit_end_time": handler_cfg["fit_end_time"],
            "infer_processors": handler_cfg["infer_processors"],
        }
        if "feature_groups" in handler_cfg:
            handler_kwargs["feature_groups"] = handler_cfg["feature_groups"]
        if "filter_pipe" in handler_cfg:
            handler_kwargs["filter_pipe"] = handler_cfg["filter_pipe"]
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
        """Score one day through each frozen model, then blend as research does.

        单成员也走 ``blend_score_series``：一个成员时日截面 z-score 是单调变换，不改变
        任何排序，而排序是下游唯一读取的东西，所以只留一条代码路径。
        """
        day_features = day_features.dropna(how="all")
        stamp = pd.Timestamp(target_date)
        member_scores = []
        for model in self._models:
            raw_scores = model.predict(_InferenceDataset(day_features), segment="test")
            if not isinstance(raw_scores, pd.Series):
                raise TypeError("model.predict must return a pandas Series")
            if raw_scores.index.has_duplicates:
                raise ValueError("model prediction index contains duplicates")
            if not raw_scores.index.equals(day_features.index):
                raise ValueError(
                    "model prediction index must exactly match feature index"
                )
            member = raw_scores.astype(float)
            member = member[np.isfinite(member)]
            member.index = pd.MultiIndex.from_arrays(
                [[stamp] * len(member), member.index],
                names=["datetime", "instrument"],
            )
            member_scores.append(member.rename("score"))

        blended = blend_score_series(member_scores)
        scores = blended.droplevel("datetime").rename("score")
        finite = scores[np.isfinite(scores)]
        if scores.size and finite.empty:
            # 日截面 z-score 的标准差是 ddof=1，截面只剩一只票时它是 NaN，
            # 整条信号会静默清空成「今天没有候选」。宁可炸掉也不能不下单。
            raise ValueError(
                f"cross-section too small to standardize on {target_date}: "
                f"{scores.size} instrument(s)"
            )
        scores = finite

        if scores.empty:
            logger.warning("Generated no finite predictions for %s", target_date)
        else:
            logger.info(
                "Generated predictions for %s: %d instruments from %d model(s), "
                "top=%.6f, bottom=%.6f",
                target_date, len(scores), len(self._models),
                scores.max(), scores.min(),
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
