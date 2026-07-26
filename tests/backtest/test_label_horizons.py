from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.label_design import horizons  # noqa: E402
from backtest.label_design.horizons import (  # noqa: E402
    common_self_eval_end,
    cumulative_label,
    select_horizon_anchors,
    survival_weighted_label,
)
from backtest.scripts.freeze_label_horizon_manifest import build_manifest  # noqa: E402


def test_cumulative_label_uses_next_close_as_entry():
    assert cumulative_label(1) == "Ref($close, -2)/Ref($close, -1)-1"
    assert cumulative_label(20) == "Ref($close, -21)/Ref($close, -1)-1"


def test_cumulative_label_rejects_nonpositive_horizon():
    with pytest.raises(ValueError, match="positive"):
        cumulative_label(0)


def test_survival_weighted_label_normalizes_and_orders_terms():
    expression, weights = survival_weighted_label(
        {2: 0.5, 1: 1.0},
        max_horizon=2,
    )

    assert weights == {1: pytest.approx(2 / 3), 2: pytest.approx(1 / 3)}
    assert expression == (
        "0.666666666667*(Ref($close, -2)/Ref($close, -1)-1)"
        "+0.333333333333*(Ref($close, -3)/Ref($close, -2)-1)"
    )


def test_survival_weighted_label_requires_every_age():
    with pytest.raises(ValueError, match="age 2"):
        survival_weighted_label({1: 1.0, 3: 0.2}, max_horizon=3)


def test_survival_power_weights_apply_power_before_normalizing():
    expression, weights = horizons.survival_power_weighted_label(
        {1: 1.0, 2: 0.25},
        max_horizon=2,
        power=0.5,
    )

    assert weights == {1: pytest.approx(2 / 3), 2: pytest.approx(1 / 3)}
    assert expression == (
        "0.666666666667*(Ref($close, -2)/Ref($close, -1)-1)"
        "+0.333333333333*(Ref($close, -3)/Ref($close, -2)-1)"
    )


def test_survival_power_one_matches_standard_survival_label():
    survival = {1: 1.0, 2: 0.5, 3: 0.25}

    assert horizons.survival_power_weighted_label(
        survival,
        max_horizon=3,
        power=1.0,
    ) == survival_weighted_label(survival, max_horizon=3)


@pytest.mark.parametrize("power", [0.0, -1.0, float("nan"), float("inf")])
def test_survival_power_rejects_nonpositive_or_nonfinite_power(power):
    with pytest.raises(ValueError, match="power"):
        horizons.survival_power_weighted_label(
            {1: 1.0},
            max_horizon=1,
            power=power,
        )


def test_anchor_selection_maps_p50_p75_p90_to_distinct_nearest_values():
    selected = select_horizon_anchors(
        {"p50": 18.0, "p75": 37.0, "p90": 58.0},
        anchors=(5, 10, 20, 30, 40, 60),
    )

    assert selected == [20, 40, 60]


def test_anchor_selection_resolves_duplicate_with_nearest_unused_anchor():
    selected = select_horizon_anchors(
        {"p50": 19.0, "p75": 21.0, "p90": 39.0},
        anchors=(5, 10, 20, 30, 40, 60),
    )

    assert selected == [20, 30, 40]


def test_common_self_eval_end_purges_horizon_plus_one_dates():
    calendar = pd.bdate_range("2026-06-29", "2026-07-16")

    assert common_self_eval_end(
        calendar,
        official_end="2026-07-16",
        max_horizon=3,
    ) == "2026-07-10"


def test_manifest_freezes_candidates_before_test_evaluation():
    calendar = pd.bdate_range("2025-12-01", "2026-07-16")
    diagnostic = {
        "data_version": "2026-07-24",
        "pooled": {
            "p50": 18.0,
            "p75": 37.0,
            "p90": 58.0,
            "survival": {
                str(age): 1.0 if age <= 20 else 0.5
                for age in range(1, 61)
            },
        },
    }

    manifest = build_manifest(
        diagnostic,
        calendar=calendar,
        diagnostic_sha256="abc123",
        generated_at="2026-07-25T23:00:00",
    )

    assert manifest["baseline_ref"] == "B1 v1.0"
    assert manifest["selected_horizons"] == [20, 40, 60]
    assert manifest["max_horizon"] == 60
    assert manifest["diagnostic_sha256"] == "abc123"
    assert manifest["common_self_eval_end"] == "2026-04-22"
    assert [candidate["variant"] for candidate in manifest["candidates"]] == [
        "cum-h20",
        "cum-h40",
        "cum-h60",
        "survival-weighted-h60",
    ]
    assert manifest["candidates"][0]["label"] == (
        "Ref($close, -21)/Ref($close, -1)-1"
    )
    assert manifest["candidates"][-1]["label_horizon"] == 60
    assert manifest["test_metrics_opened"] is False
