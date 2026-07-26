from __future__ import annotations

import importlib

import pandas as pd
import pytest


def _diagnostic() -> dict:
    return {
        "data_version": "2026-07-24",
        "pooled": {
            "survival": {
                str(age): 1.0 if age <= 20 else 0.25
                for age in range(1, 61)
            },
        },
    }


def test_manifest_freezes_three_h40_survival_power_candidates():
    freezer = importlib.import_module(
        "backtest.scripts.freeze_h40_survival_weight_manifest"
    )

    manifest = freezer.build_manifest(
        _diagnostic(),
        calendar=pd.bdate_range("2026-01-01", "2026-07-16"),
        diagnostic_sha256="valid-only-sha",
        generated_at="2026-07-26T12:00:00",
    )

    assert manifest["baseline_ref"] == "B1 v1.0"
    assert manifest["adaptive_followup"] is True
    assert manifest["primary_test_pool"] == "csi1000"
    assert manifest["test_pools"] == ["csi1000", "csi300", "csi500"]
    assert manifest["label_horizon"] == 40
    assert manifest["purge_trading_days"] == 41
    assert manifest["common_self_eval_end"] == "2026-05-20"
    assert manifest["diagnostic_sha256"] == "valid-only-sha"
    assert manifest["test_metrics_opened"] is False
    assert manifest["seeds"] == [42, 1000, 2000, 3000, 4000]
    assert [
        (candidate["variant"], candidate["power"])
        for candidate in manifest["candidates"]
    ] == [
        ("survival-p05-h40", 0.5),
        ("survival-p10-h40", 1.0),
        ("survival-p20-h40", 2.0),
    ]


def test_manifest_power_changes_weight_shape_without_changing_support():
    freezer = importlib.import_module(
        "backtest.scripts.freeze_h40_survival_weight_manifest"
    )
    manifest = freezer.build_manifest(
        _diagnostic(),
        calendar=pd.bdate_range("2026-01-01", "2026-07-16"),
        diagnostic_sha256="valid-only-sha",
    )

    expected_ratios = [0.5, 0.25, 0.0625]
    for candidate, expected_ratio in zip(
        manifest["candidates"],
        expected_ratios,
        strict=True,
    ):
        weights = candidate["weights"]
        assert list(weights) == [str(age) for age in range(1, 41)]
        assert sum(weights.values()) == pytest.approx(1.0)
        assert weights["21"] / weights["1"] == pytest.approx(expected_ratio)
