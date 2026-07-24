from __future__ import annotations

import importlib

import pytest


def _selector():
    return importlib.import_module("backtest.scripts.select_valid_model")


def _valid_result():
    return {
        "eval_segment_name": "valid",
        "eval_segment": ["2020-01-13", "2021-07-15"],
        "sessions": [
            {"session": "full_s42", "seed": 42},
            {"session": "full_s1000", "seed": 1000},
            {"session": "full_s2000", "seed": 2000},
        ],
        "pools": {
            "csi1000": {
                "seeds": {
                    "42": {"rank_ic_mean": 0.02, "rank_icir": 0.2},
                    "1000": {"rank_ic_mean": 0.03, "rank_icir": 0.1},
                    "2000": {"rank_ic_mean": 0.03, "rank_icir": 0.25},
                }
            }
        },
    }


def test_selects_highest_valid_rankic_then_rankicir():
    selection = _selector().select_best_valid_model(
        _valid_result(), pool="csi1000"
    )

    assert selection["seed"] == 2000
    assert selection["session"] == "full_s2000"
    assert selection["selection_metric"] == "valid_rank_ic_mean"
    assert selection["rank_ic_mean"] == 0.03
    assert selection["rank_icir"] == 0.25


def test_rejects_test_segment_to_prevent_seed_tuning():
    result = _valid_result()
    result["eval_segment_name"] = "test"

    with pytest.raises(ValueError, match="valid"):
        _selector().select_best_valid_model(result, pool="csi1000")

