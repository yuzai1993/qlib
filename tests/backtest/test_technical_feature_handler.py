import re

import pytest

from backtest.features.technical import Alpha158Technical, technical_feature_config


def test_each_group_has_expected_unique_fields_and_names():
    expected_counts = {"bollinger": 16, "momentum": 5, "trend": 7}

    for group, expected_count in expected_counts.items():
        fields, names = technical_feature_config([group])
        assert len(fields) == expected_count
        assert len(names) == expected_count
        assert len(set(names)) == expected_count
        assert all(name.startswith({"bollinger": "BB_", "momentum": "MOM_", "trend": "TREND_"}[group]) for name in names)


def test_combined_features_preserve_declared_group_order():
    fields, names = technical_feature_config(["bollinger", "momentum", "trend"])

    assert len(fields) == 28
    assert len(names) == 28
    assert names[:2] == ["BB_POS20", "BB_WIDTH20"]
    assert names[16] == "MOM_RET3"
    assert names[21] == "TREND_MA_ALIGN"
    assert len(set(names)) == len(names)


def test_features_use_only_current_and_historical_values():
    fields, _ = technical_feature_config(["bollinger", "momentum", "trend"])

    assert not any(re.search(r"Ref\([^,]+,\s*-", field) for field in fields)


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ([], "at least one"),
        (["unknown"], "unknown"),
        (["trend", "trend"], "duplicate"),
    ],
)
def test_invalid_group_selection_is_rejected(groups, message):
    with pytest.raises(ValueError, match=message):
        technical_feature_config(groups)


def test_handler_appends_selected_features_to_alpha158():
    base_fields, base_names = Alpha158Technical.__mro__[1].get_feature_config(None)
    handler = Alpha158Technical.__new__(Alpha158Technical)
    handler.feature_groups = ("momentum",)

    fields, names = handler.get_feature_config()

    assert fields[: len(base_fields)] == base_fields
    assert names[: len(base_names)] == base_names
    assert names[-5:] == [
        "MOM_RET3",
        "MOM_DIFF5_20",
        "MOM_DIFF10_30",
        "MOM_RISK5",
        "MOM_RISK20",
    ]
