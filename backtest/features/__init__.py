"""自定义特征扩展（基于 Alpha158）。

注意：不要在此模块顶层导入 ``handler.Alpha158Ext``，
否则会在 Cython 扩展未编译时过早触发 ``qlib.data.ops`` 导入失败。
需要 Handler 时请显式：

    from backtest.features.handler import Alpha158Ext
"""

from .expressions import FEATURE_GROUP_NAMES, get_feature_group_names

__all__ = [
    "FEATURE_GROUP_NAMES",
    "get_feature_group_names",
]
