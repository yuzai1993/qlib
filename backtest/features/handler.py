"""
Alpha158 扩展特征 Handler。

在 Alpha158 基础上按开关追加三组特征：
- mom:   风险调整动量 / 动量加速度 / 新高新低位置 / 量能加权动量
- boll:  布林线 %B、带宽、穿越事件及平滑频率
- trend: 趋势强度、震荡度、均线多空排列、站上均线比例、Kaufman ER
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset.handler import DataHandlerLP

from .expressions import (
    FEATURE_GROUP_NAMES,
    GROUP_PREFIXES,
    MOM_PREFIXES,
    BOLL_PREFIXES,
    TREND_PREFIXES,
    build_boll_features,
    build_extra_features,
    build_mom_features,
    build_trend_features,
    get_feature_group_names,
    normalize_groups,
)

__all__ = [
    "Alpha158Ext",
    "FEATURE_GROUP_NAMES",
    "GROUP_PREFIXES",
    "MOM_PREFIXES",
    "BOLL_PREFIXES",
    "TREND_PREFIXES",
    "build_boll_features",
    "build_extra_features",
    "build_mom_features",
    "build_trend_features",
    "get_feature_group_names",
    "normalize_groups",
]


class Alpha158Ext(Alpha158):
    """在 Alpha158 基础上按需追加 MOM / BOLL / TREND 特征组。"""

    def __init__(
        self,
        instruments="csi500",
        start_time=None,
        end_time=None,
        freq="day",
        infer_processors=(),
        learn_processors=None,
        fit_start_time=None,
        fit_end_time=None,
        process_type=DataHandlerLP.PTYPE_A,
        filter_pipe=None,
        inst_processors=None,
        feature_groups: Optional[Iterable[str]] = None,
        **kwargs,
    ):
        # 必须在调用父类 __init__（内部会调 get_feature_config）之前设置
        self.feature_groups = sorted(normalize_groups(feature_groups))

        # 兼容 Alpha158 默认 learn_processors（可变默认参数陷阱）
        if learn_processors is None:
            learn_processors = [
                {"class": "DropnaLabel"},
                {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}},
            ]
        if infer_processors is None or infer_processors == ():
            infer_processors = []

        super().__init__(
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
            freq=freq,
            infer_processors=list(infer_processors),
            learn_processors=learn_processors,
            fit_start_time=fit_start_time,
            fit_end_time=fit_end_time,
            process_type=process_type,
            filter_pipe=filter_pipe,
            inst_processors=inst_processors,
            **kwargs,
        )

    def get_feature_config(self):
        fields, names = super().get_feature_config()
        extra_fields, extra_names = build_extra_features(getattr(self, "feature_groups", []))
        return fields + extra_fields, names + extra_names

    def list_extra_feature_names(self) -> List[str]:
        """仅返回扩展特征名（不含 Alpha158 基线）。"""
        _, names = build_extra_features(self.feature_groups)
        return names
