"""Alpha158Ext 特征配置与表达式解析单测（无需行情数据）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

QLIB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QLIB_ROOT))

from backtest.features.expressions import (  # noqa: E402
    FEATURE_GROUP_NAMES,
    build_boll_features,
    build_extra_features,
    build_mom_features,
    build_trend_features,
)
from backtest.features.qlib_stubs import install_cython_stubs  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _stubs():
    install_cython_stubs()


def test_group_sizes_match_plan():
    mom_f, mom_n = build_mom_features()
    boll_f, boll_n = build_boll_features()
    trend_f, trend_n = build_trend_features()

    assert len(mom_f) == len(mom_n) == 14
    assert len(boll_f) == len(boll_n) == 12
    assert len(trend_f) == len(trend_n) == 16  # TRSTR/CHOP/KER*4 + MABULL/MABEAR + ABOVEMA*2


def test_names_unique_across_all_groups():
    fields, names = build_extra_features(FEATURE_GROUP_NAMES)
    assert len(names) == len(set(names))
    assert len(fields) == len(names)
    assert len(names) == 14 + 12 + 16


def test_expressions_parse():
    from qlib.data.base import Feature  # noqa: F401
    from qlib.data.ops import Operators  # noqa: F401
    from qlib.utils import parse_field

    fields, names = build_extra_features(FEATURE_GROUP_NAMES)
    for name, field in zip(names, fields):
        expr = eval(parse_field(field))  # noqa: S307
        assert expr is not None, name
        assert str(expr)


def test_unknown_group_raises():
    with pytest.raises(ValueError):
        build_extra_features(["mom", "unknown"])


def test_handler_feature_config_appends():
    from backtest.features.handler import Alpha158Ext

    h = Alpha158Ext.__new__(Alpha158Ext)
    h.feature_groups = ["mom"]
    fields, names = h.get_feature_config()
    assert "MOMRA5" in names
    assert "BOLLPB20" not in names
    assert "ROC5" in names
    assert "BETA20" in names


def test_handler_all_groups():
    from backtest.features.handler import Alpha158Ext

    h = Alpha158Ext.__new__(Alpha158Ext)
    h.feature_groups = list(FEATURE_GROUP_NAMES)
    _, names = h.get_feature_config()
    assert "MOMRA5" in names
    assert "BOLLPB20" in names
    assert "TRSTR20" in names
    assert "MABULL" in names
    assert "KER60" in names
