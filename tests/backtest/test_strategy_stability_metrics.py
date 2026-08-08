from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

from strategy_stability_metrics import (  # noqa: E402
    IncompletePortfolioError,
    summarize_period,
    summarize_stability,
)


def _report(dates, returns, costs, *, turnover=None, bench=None):
    size = len(dates)
    return pd.DataFrame(
        {
            "return": returns,
            "cost": costs,
            "turnover": turnover or [0.2] * size,
            "bench": bench or [0.001] * size,
        },
        index=pd.to_datetime(dates),
    )


def test_period_metrics_use_after_cost_absolute_returns():
    report = _report(
        ["2021-01-04", "2021-01-05", "2021-01-06"],
        [0.02, -0.01, 0.01],
        [0.001, 0.001, 0.001],
    )
    net = pd.Series([0.019, -0.011, 0.009])

    metrics = summarize_period(report)

    expected_vol = net.std(ddof=1) * math.sqrt(250)
    expected_return = net.mean() * 250
    wealth = (1.0 + net).cumprod()
    expected_mdd = (wealth / wealth.cummax() - 1.0).min()
    assert metrics["annualized_return"] == pytest.approx(expected_return)
    assert metrics["annualized_volatility"] == pytest.approx(expected_vol)
    assert metrics["sharpe_ratio"] == pytest.approx(expected_return / expected_vol)
    assert metrics["max_drawdown"] == pytest.approx(expected_mdd)
    assert metrics["calmar_ratio"] == pytest.approx(expected_return / abs(expected_mdd))
    assert metrics["annualized_one_way_turnover"] == pytest.approx(25.0)
    assert metrics["benchmark_cumulative_return"] == pytest.approx((1.001**3) - 1.0)
    assert metrics["benchmark_annualized_return"] == pytest.approx(0.001 * 250)
    # constant benchmark => zero variance => beta/alpha unavailable
    assert metrics["beta"] is None
    assert metrics["alpha"] is None


def test_alpha_beta_use_after_cost_returns_versus_benchmark():
    report = _report(
        ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"],
        [0.02, -0.01, 0.015, 0.0],
        [0.001, 0.001, 0.001, 0.001],
        bench=[0.01, -0.005, 0.008, 0.002],
    )
    net = pd.Series([0.019, -0.011, 0.014, -0.001])
    bench = pd.Series([0.01, -0.005, 0.008, 0.002])
    metrics = summarize_period(report)
    expected_beta = float(net.cov(bench) / bench.var(ddof=1))
    expected_alpha = float((net.mean() - expected_beta * bench.mean()) * 250)
    assert metrics["beta"] == pytest.approx(expected_beta)
    assert metrics["alpha"] == pytest.approx(expected_alpha)
    assert metrics["benchmark_cumulative_return"] == pytest.approx(
        float((1.0 + bench).prod() - 1.0)
    )


def test_zero_volatility_and_zero_drawdown_return_null_ratios():
    report = _report(
        ["2021-01-04", "2021-01-05"],
        [0.01, 0.01],
        [0.0, 0.0],
    )

    metrics = summarize_period(report)

    assert metrics["sharpe_ratio"] is None
    assert metrics["calmar_ratio"] is None


def test_calendar_years_keep_continuous_holdings_and_mark_partial_boundaries():
    report = _report(
        [
            "2020-01-13",
            "2020-12-31",
            "2021-01-04",
            "2021-12-31",
            "2025-01-02",
            "2025-12-31",
            "2026-01-05",
            "2026-07-31",
        ],
        [0.01] * 8,
        [0.0] * 8,
    )

    summary = summarize_stability(report)

    assert summary["years"]["2020"]["partial_year"] is True
    assert summary["years"]["2021"]["partial_year"] is False
    assert summary["years"]["2025"]["partial_year"] is False
    assert summary["years"]["2026"]["partial_year"] is True
    assert summary["complete_years"] == ["2021", "2025"]
    assert summary["positive_complete_years"] == 2
    assert summary["complete_year_sharpe_median"] is None
    assert summary["worst_complete_year_max_drawdown"] == pytest.approx(0.0)


def test_missing_required_report_column_is_rejected():
    report = pd.DataFrame({"return": [0.01]}, index=pd.to_datetime(["2021-01-04"]))

    with pytest.raises(ValueError, match="missing columns"):
        summarize_period(report)


def test_nan_portfolio_day_invalidates_continuous_period():
    report = _report(
        ["2020-05-29", "2020-06-01"],
        [0.01, math.nan],
        [0.001, math.nan],
    )

    with pytest.raises(IncompletePortfolioError, match="2020-06-01"):
        summarize_period(report)
