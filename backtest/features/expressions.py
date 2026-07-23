"""
扩展特征表达式定义（纯 Python，不依赖已编译的 qlib Cython 扩展）。

供 Alpha158Ext Handler 与校验脚本共用。
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Set, Tuple

FEATURE_GROUP_NAMES = ("mom", "boll", "trend")

MOM_PREFIXES = ("MOMRA", "MOMACC", "NHIGH", "NLOW", "VWMOM")
BOLL_PREFIXES = ("BOLLPB", "BOLLBW", "BOLLXU", "BOLLXD")
TREND_PREFIXES = ("TRSTR", "CHOP", "MABULL", "MABEAR", "ABOVEMA", "KER")

GROUP_PREFIXES = {
    "mom": MOM_PREFIXES,
    "boll": BOLL_PREFIXES,
    "trend": TREND_PREFIXES,
}


def get_feature_group_names(group: str) -> Tuple[str, ...]:
    """返回某特征组使用的字段名前缀。"""
    if group not in GROUP_PREFIXES:
        raise ValueError(f"未知特征组: {group}, 可选: {FEATURE_GROUP_NAMES}")
    return GROUP_PREFIXES[group]


def normalize_groups(feature_groups: Optional[Iterable[str]]) -> Set[str]:
    if feature_groups is None:
        return set()
    groups = {str(g).strip().lower() for g in feature_groups}
    unknown = groups - set(FEATURE_GROUP_NAMES)
    if unknown:
        raise ValueError(f"未知特征组: {sorted(unknown)}, 可选: {FEATURE_GROUP_NAMES}")
    return groups


def build_mom_features(windows: Sequence[int] = (5, 10, 20, 60)) -> Tuple[List[str], List[str]]:
    """动量特征组。"""
    fields: List[str] = []
    names: List[str] = []

    for d in windows:
        fields.append(
            f"($close/Ref($close,{d})-1)/(Std($close/Ref($close,1)-1,{d})+1e-12)"
        )
        names.append(f"MOMRA{d}")

    fields.append("($close/Ref($close,5)-1)-($close/Ref($close,20)-1)")
    names.append("MOMACC5_20")
    fields.append("($close/Ref($close,10)-1)-($close/Ref($close,60)-1)")
    names.append("MOMACC10_60")

    for d in (20, 60):
        fields.append(f"$close/Max($high,{d})")
        names.append(f"NHIGH{d}")
        fields.append(f"$close/Min($low,{d})")
        names.append(f"NLOW{d}")

    for d in windows:
        fields.append(
            f"Sum(($close/Ref($close,1)-1)*$volume,{d})/(Sum($volume,{d})+1e-12)"
        )
        names.append(f"VWMOM{d}")

    return fields, names


def build_boll_features(windows: Sequence[int] = (20, 60), smooth: int = 5) -> Tuple[List[str], List[str]]:
    """布林线穿越特征组。

    注意：qlib 的 Greater 是 element-wise max，不是比较运算符；
    穿越事件使用 ``>`` / ``<=``（映射到 Gt/Le）。
    """
    fields: List[str] = []
    names: List[str] = []

    for d in windows:
        mid = f"Mean($close,{d})"
        std = f"Std($close,{d})"
        up = f"({mid}+2*{std})"
        down = f"({mid}-2*{std})"
        up_lag = f"(Ref({mid},1)+2*Ref({std},1))"
        down_lag = f"(Ref({mid},1)-2*Ref({std},1))"

        fields.append(f"($close-{down})/({up}-{down}+1e-12)")
        names.append(f"BOLLPB{d}")

        fields.append(f"(4*{std})/({mid}+1e-12)")
        names.append(f"BOLLBW{d}")

        cross_up = f"(($close>{up})*(Ref($close,1)<={up_lag}))"
        fields.append(cross_up)
        names.append(f"BOLLXU{d}")

        cross_down = f"(($close<{down})*(Ref($close,1)>={down_lag}))"
        fields.append(cross_down)
        names.append(f"BOLLXD{d}")

        fields.append(f"Mean({cross_up},{smooth})")
        names.append(f"BOLLXU{d}_C{smooth}")
        fields.append(f"Mean({cross_down},{smooth})")
        names.append(f"BOLLXD{d}_C{smooth}")

    return fields, names


def build_trend_features(windows: Sequence[int] = (5, 10, 20, 60)) -> Tuple[List[str], List[str]]:
    """趋势状态特征组。"""
    fields: List[str] = []
    names: List[str] = []

    for d in windows:
        fields.append(f"Slope($close,{d})*Rsquare($close,{d})/$close")
        names.append(f"TRSTR{d}")

        fields.append(f"1-Rsquare($close,{d})")
        names.append(f"CHOP{d}")

        fields.append(
            f"Abs($close-Ref($close,{d}))/(Sum(Abs($close-Ref($close,1)),{d})+1e-12)"
        )
        names.append(f"KER{d}")

    fields.append(
        "(Mean($close,5)>Mean($close,10))*(Mean($close,10)>Mean($close,20))"
    )
    names.append("MABULL")
    fields.append(
        "(Mean($close,5)<Mean($close,10))*(Mean($close,10)<Mean($close,20))"
    )
    names.append("MABEAR")

    for d in (10, 20):
        fields.append(f"Mean($close>Mean($close,20),{d})")
        names.append(f"ABOVEMA{d}")

    return fields, names


def build_extra_features(feature_groups: Iterable[str]) -> Tuple[List[str], List[str]]:
    """按特征组开关拼接额外特征表达式与字段名。"""
    groups = normalize_groups(feature_groups)
    fields: List[str] = []
    names: List[str] = []

    builders = {
        "mom": build_mom_features,
        "boll": build_boll_features,
        "trend": build_trend_features,
    }
    for group in FEATURE_GROUP_NAMES:
        if group in groups:
            f, n = builders[group]()
            fields.extend(f)
            names.extend(n)

    if len(names) != len(set(names)):
        raise ValueError(f"特征名存在重复: {names}")
    return fields, names
