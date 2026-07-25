"""Pre-registered technical feature groups layered on top of Alpha158."""

from __future__ import annotations

from collections.abc import Sequence

from qlib.contrib.data.handler import Alpha158


def _bollinger_features() -> tuple[list[str], list[str]]:
    fields: list[str] = []
    names: list[str] = []
    for window in (20, 60):
        mean = f"Mean($close, {window})"
        std = f"Std($close, {window})"
        upper = f"({mean}+2*{std})"
        lower = f"({mean}-2*{std})"
        previous_upper = f"Ref({upper}, 1)"
        previous_lower = f"Ref({lower}, 1)"
        fields.extend(
            [
                f"($close-{mean})/(2*{std}+1e-12)",
                f"4*{std}/({mean}+1e-12)",
                f"($close-{upper})/($close+1e-12)",
                f"($close-{lower})/($close+1e-12)",
                f"($close>{upper})*(Ref($close, 1)<={previous_upper})",
                f"($close<{lower})*(Ref($close, 1)>={previous_lower})",
                f"($close<{upper})*(Ref($close, 1)>={previous_upper})",
                f"($close>{lower})*(Ref($close, 1)<={previous_lower})",
            ]
        )
        names.extend(
            [
                f"BB_POS{window}",
                f"BB_WIDTH{window}",
                f"BB_UP_DIST{window}",
                f"BB_LOW_DIST{window}",
                f"BB_CROSS_UP{window}",
                f"BB_CROSS_DOWN{window}",
                f"BB_REENTER_UP{window}",
                f"BB_REENTER_LOW{window}",
            ]
        )
    return fields, names


def _momentum_features() -> tuple[list[str], list[str]]:
    fields = [
        "$close/(Ref($close, 3)+1e-12)-1",
        "($close/(Ref($close, 5)+1e-12)-1)-($close/(Ref($close, 20)+1e-12)-1)",
        "($close/(Ref($close, 10)+1e-12)-1)-($close/(Ref($close, 30)+1e-12)-1)",
        "($close/(Ref($close, 5)+1e-12)-1)/(Std($close/(Ref($close, 1)+1e-12)-1, 20)+1e-12)",
        "($close/(Ref($close, 20)+1e-12)-1)/(Std($close/(Ref($close, 1)+1e-12)-1, 60)+1e-12)",
    ]
    names = ["MOM_RET3", "MOM_DIFF5_20", "MOM_DIFF10_30", "MOM_RISK5", "MOM_RISK20"]
    return fields, names


def _trend_features() -> tuple[list[str], list[str]]:
    ma5 = "Mean($close, 5)"
    ma20 = "Mean($close, 20)"
    ma60 = "Mean($close, 60)"
    fields = [
        f"1.0*({ma5}>{ma20})*({ma20}>{ma60})-1.0*({ma5}<{ma20})*({ma20}<{ma60})",
    ]
    names = ["TREND_MA_ALIGN"]
    for window in (20, 60):
        fields.extend(
            [
                f"Sign($close-Ref($close, {window}))",
                f"($close-Ref($close, {window}))/(Sum(Abs($close-Ref($close, 1)), {window})+1e-12)",
                f"Slope($close, {window})/(Std($close, {window})+1e-12)",
            ]
        )
        names.extend(
            [
                f"TREND_DIR{window}",
                f"TREND_EFF{window}",
                f"TREND_STRENGTH{window}",
            ]
        )
    return fields, names


_FEATURE_BUILDERS = {
    "bollinger": _bollinger_features,
    "momentum": _momentum_features,
    "trend": _trend_features,
}


def technical_feature_config(groups: Sequence[str]) -> tuple[list[str], list[str]]:
    """Return Qlib expressions and names for validated technical feature groups."""
    selected = tuple(groups)
    if not selected:
        raise ValueError("feature_groups must contain at least one group")
    if len(set(selected)) != len(selected):
        raise ValueError("feature_groups contains a duplicate group")
    unknown = [group for group in selected if group not in _FEATURE_BUILDERS]
    if unknown:
        raise ValueError(f"unknown feature group: {unknown[0]}")

    fields: list[str] = []
    names: list[str] = []
    for group in selected:
        group_fields, group_names = _FEATURE_BUILDERS[group]()
        fields.extend(group_fields)
        names.extend(group_names)
    return fields, names


class Alpha158Technical(Alpha158):
    """Alpha158 plus one or more controlled technical feature groups."""

    def __init__(self, feature_groups: Sequence[str], **kwargs):
        technical_feature_config(feature_groups)
        self.feature_groups = tuple(feature_groups)
        super().__init__(**kwargs)

    def get_feature_config(self):
        fields, names = super().get_feature_config()
        extra_fields, extra_names = technical_feature_config(self.feature_groups)
        return fields + extra_fields, names + extra_names
