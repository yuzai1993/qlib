"""Regime day-level broadcast features for the regime-adapt experiment.

Day 级市场状态信号（A0b 产出的 CSV）以广播列形式并入个股特征面板：
同一交易日所有股票共享同一取值，LightGBM 通过与个股特征的交互学习条件映射。
CSV 由实验准备脚本预生成并冻结（PIT：每行仅使用当日及之前的数据）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from qlib.contrib.data.handler import check_transform_proc
from qlib.data.dataset.handler import DataHandlerLP
from qlib.data.dataset.loader import QlibDataLoader

from backtest.features.technical import Alpha158Technical

REGIME_FEATURE_PREFIX = "REGIME_"


def broadcast_day_features(
    df: pd.DataFrame,
    day_features: pd.DataFrame,
    columns: Sequence[str],
    *,
    group: str = "feature",
    prefix: str = REGIME_FEATURE_PREFIX,
) -> pd.DataFrame:
    """将 day 级特征按 datetime 广播到 (datetime, instrument) 面板。"""
    missing = [c for c in columns if c not in day_features.columns]
    if missing:
        raise ValueError(f"regime csv missing columns: {missing}")
    if "datetime" not in (df.index.names or []):
        raise ValueError("panel index must contain a datetime level")
    dts = df.index.get_level_values("datetime")
    for col in columns:
        values = day_features[col].reindex(dts).to_numpy()
        if isinstance(df.columns, pd.MultiIndex):
            df[(group, f"{prefix}{col}")] = values
        else:
            df[f"{prefix}{col}"] = values
    return df


class RegimeJoinLoader(QlibDataLoader):
    """QlibDataLoader + day 级 regime 特征广播列。"""

    def __init__(self, regime_csv: str, regime_columns: Optional[Sequence[str]] = None, **kwargs):
        super().__init__(**kwargs)
        self.regime_csv = str(regime_csv)
        self.regime_columns = list(regime_columns) if regime_columns else None

    def load(self, instruments=None, start_time=None, end_time=None) -> pd.DataFrame:
        df = super().load(instruments, start_time, end_time)
        day = pd.read_csv(
            Path(self.regime_csv).expanduser(),
            index_col=0,
            parse_dates=True,
            comment="#",
        )
        columns = self.regime_columns or list(day.columns)
        return broadcast_day_features(df, day, columns)


class Alpha158RegimeTechnical(Alpha158Technical):
    """Alpha158 + technical feature groups + day 级 regime 广播特征。

    与 Alpha158Technical 的唯一区别是 data_loader 换成 RegimeJoinLoader；
    构造流程复刻 qlib.contrib.data.handler.Alpha158.__init__（本地版本已锁定）。
    """

    def __init__(
        self,
        regime_csv: str,
        feature_groups: Sequence[str],
        regime_columns: Optional[Sequence[str]] = None,
        instruments="csi500",
        start_time=None,
        end_time=None,
        freq="day",
        infer_processors=[],
        learn_processors=None,
        fit_start_time=None,
        fit_end_time=None,
        process_type=DataHandlerLP.PTYPE_A,
        filter_pipe=None,
        inst_processors=None,
        **kwargs,
    ):
        from backtest.features.technical import technical_feature_config

        technical_feature_config(feature_groups)
        self.feature_groups = tuple(feature_groups)
        self.regime_csv = str(regime_csv)
        self.regime_columns = list(regime_columns) if regime_columns else None
        if learn_processors is None:
            raise ValueError("learn_processors must be given explicitly (与基线配置一致)")

        infer_processors = check_transform_proc(infer_processors, fit_start_time, fit_end_time)
        learn_processors = check_transform_proc(learn_processors, fit_start_time, fit_end_time)

        data_loader = {
            "class": "RegimeJoinLoader",
            "module_path": "backtest.features.regime",
            "kwargs": {
                "regime_csv": self.regime_csv,
                "regime_columns": self.regime_columns,
                "config": {
                    "feature": self.get_feature_config(),
                    "label": kwargs.pop("label", self.get_label_config()),
                },
                "filter_pipe": filter_pipe,
                "freq": freq,
                "inst_processors": inst_processors,
            },
        }
        DataHandlerLP.__init__(
            self,
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
            data_loader=data_loader,
            infer_processors=infer_processors,
            learn_processors=learn_processors,
            process_type=process_type,
            **kwargs,
        )
