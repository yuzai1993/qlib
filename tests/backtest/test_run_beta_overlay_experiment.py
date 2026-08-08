from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from run_beta_overlay_experiment import (  # noqa: E402
    build_registry_row,
    decide_promotion,
    run_experiment,
)


def _synthetic_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    index = pd.bdate_range("2022-04-01", "2026-07-31")
    rng = np.random.default_rng(7)
    bench = pd.Series(rng.normal(0.0002, 0.01, len(index)), index=index)
    report = pd.DataFrame(
        {
            "return": 0.55 * bench + 0.0004,
            "cost": 0.0001,
            "bench": bench,
            "turnover": 0.05,
            "value": 2_500_000.0,
            "cash": 300_000.0,
        },
        index=index,
    )
    im_index = index[index >= pd.Timestamp("2022-07-22")]
    im = pd.DataFrame(
        {
            "settle": 7000.0,
            "fut_ret": bench.reindex(im_index).to_numpy(),
        },
        index=im_index,
    )
    return report, im


def test_promotion_requires_strictly_higher_sharpe():
    assert decide_promotion(overlay_sharpe=1.5, baseline_sharpe=1.4)["eligible"] is True
    assert (
        decide_promotion(overlay_sharpe=1.4, baseline_sharpe=1.4)["eligible"] is False
    )
    assert (
        decide_promotion(overlay_sharpe=1.3, baseline_sharpe=1.4)["eligible"] is False
    )


def test_promotion_rejects_missing_sharpe():
    result = decide_promotion(overlay_sharpe=None, baseline_sharpe=1.4)
    assert result["eligible"] is False
    assert "finite" in result["reason"]


def test_registry_row_marks_im_window_mode(tmp_path):
    payload = {
        "evaluation_mode": "im_window_in_sample",
        "im_window": ["2022-07-22", "2026-07-31"],
        "account": 2_800_000,
        "baseline": {"sharpe_ratio": 1.0},
        "overlay_continuous": {"sharpe_ratio": 1.2},
        "promote": {"eligible": True, "reason": "sharpe_ratio 1.2 > 1.0"},
    }
    out = tmp_path / "a.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    row = build_registry_row(payload, out)
    assert row["exp_id"] == "strategy-beta-overlay/b2s-im-target1-roll60"
    assert row["evaluation_mode"] == "im_window_in_sample"
    assert row["baseline_ref"] == "B2-S v1.0"
    assert row["direction"] == "strategy-beta-overlay"
    assert row["data_version"] == "2026-07-31"
    assert row["conclusion"] == "accepted_pending_promotion"
    assert row["cleanup_retention_eligible"] is False
    assert row["result_sha256"]


def test_run_experiment_returns_required_metrics_and_basis_note():
    report, im = _synthetic_inputs()
    im["basis"] = 0.01
    payload = run_experiment(report, im)
    assert payload["evaluation_mode"] == "im_window_in_sample"
    assert payload["im_window"] == ["2022-07-22", "2026-07-31"]
    assert payload["account"] == 2_800_000
    assert payload["baseline"]["n_days"] == payload["overlay_continuous"]["n_days"]
    assert payload["overlay_discrete"]["n_days"] == payload["baseline"]["n_days"]
    assert payload["basis_note"]["n_observations"] == len(im)
    assert isinstance(payload["promote"]["eligible"], bool)


def test_run_experiment_rejects_interior_missing_futures_return():
    report, im = _synthetic_inputs()
    im.iloc[5, im.columns.get_loc("fut_ret")] = np.nan
    with pytest.raises(ValueError, match="fut_ret.*incomplete"):
        run_experiment(report, im)


def test_run_experiment_allows_only_listing_day_missing_futures_return():
    report, im = _synthetic_inputs()
    im.iloc[0, im.columns.get_loc("fut_ret")] = np.nan
    payload = run_experiment(report, im)
    assert payload["evaluated_window"][0] == "2022-07-25"


def test_run_experiment_rejects_truncated_futures_calendar():
    report, im = _synthetic_inputs()
    with pytest.raises(ValueError, match="settle.*incomplete"):
        run_experiment(report, im.iloc[:-1])


def test_run_experiment_rejects_report_ending_before_im_window_end():
    report, im = _synthetic_inputs()
    truncated_report = report.loc[:"2026-07-30"]
    with pytest.raises(ValueError, match="report.*2026-07-31"):
        run_experiment(truncated_report, im)


@pytest.mark.parametrize("column", ["return", "bench"])
def test_run_experiment_rejects_interior_missing_report_value(column):
    report, im = _synthetic_inputs()
    report.loc[pd.Timestamp("2024-01-15"), column] = np.nan
    with pytest.raises(ValueError, match=column):
        run_experiment(report, im)


def test_run_experiment_rejects_missing_beta_inside_im_window():
    report, im = _synthetic_inputs()
    report = report.loc["2022-07-22":]
    with pytest.raises(ValueError, match="beta_hat"):
        run_experiment(report, im)
