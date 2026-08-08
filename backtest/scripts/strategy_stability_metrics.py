"""After-cost absolute-return metrics for full-period strategy diagnostics."""

from __future__ import annotations

import math
from statistics import median
from typing import Any

import pandas as pd

TRADING_DAYS = 250
REQUIRED_COLUMNS = {"return", "cost", "bench", "turnover"}
COMPLETE_YEARS = {2021, 2022, 2023, 2024, 2025}


class IncompletePortfolioError(ValueError):
    """The continuous portfolio contains a day without usable account data."""


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _validate_report(report: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_COLUMNS - set(report.columns))
    if missing:
        raise ValueError(f"report missing columns: {missing}")
    if not isinstance(report.index, pd.DatetimeIndex):
        raise ValueError("report index must be a DatetimeIndex")
    if report.index.has_duplicates:
        raise ValueError("report contains duplicate dates")
    return report.sort_index()


def summarize_period(report: pd.DataFrame) -> dict[str, float | int | None]:
    report = _validate_report(report)
    incomplete = report[list(REQUIRED_COLUMNS)].isna().any(axis=1)
    if incomplete.any():
        first = report.index[incomplete][0]
        raise IncompletePortfolioError(
            f"continuous portfolio is incomplete from {first.date()}"
        )
    clean = report
    net = clean["return"].astype(float) - clean["cost"].astype(float)
    bench = clean["bench"].astype(float)
    annualized_return = float(net.mean() * TRADING_DAYS)
    benchmark_annualized_return = float(bench.mean() * TRADING_DAYS)
    daily_std = float(net.std(ddof=1)) if len(net) > 1 else float("nan")
    annualized_volatility = _finite(daily_std * math.sqrt(TRADING_DAYS))
    sharpe = (
        _finite(annualized_return / annualized_volatility)
        if annualized_volatility not in (None, 0.0)
        else None
    )
    wealth = (1.0 + net).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    calmar = (
        _finite(annualized_return / abs(max_drawdown))
        if max_drawdown != 0.0
        else None
    )
    benchmark_cumulative_return = float((1.0 + bench).prod() - 1.0)
    beta = None
    alpha = None
    if len(net) > 1:
        bench_var = float(bench.var(ddof=1))
        if math.isfinite(bench_var) and bench_var > 0.0:
            beta = _finite(float(net.cov(bench)))
            if beta is not None:
                beta = _finite(beta / bench_var)
            if beta is not None:
                alpha = _finite((float(net.mean()) - float(beta) * float(bench.mean())) * TRADING_DAYS)
    return {
        "n_days": int(len(clean)),
        "annualized_return": _finite(annualized_return),
        "sharpe_ratio": sharpe,
        "alpha": alpha,
        "beta": beta,
        "calmar_ratio": calmar,
        "annualized_volatility": annualized_volatility,
        "max_drawdown": _finite(max_drawdown),
        "annualized_one_way_turnover": _finite(float(clean["turnover"].mean() * TRADING_DAYS / 2.0)),
        "cumulative_return": _finite(float(wealth.iloc[-1] - 1.0)),
        "benchmark_annualized_return": _finite(benchmark_annualized_return),
        "benchmark_cumulative_return": _finite(benchmark_cumulative_return),
    }


def summarize_years(report: pd.DataFrame) -> dict[str, dict[str, Any]]:
    report = _validate_report(report)
    years: dict[str, dict[str, Any]] = {}
    for year, group in report.groupby(report.index.year):
        metrics = summarize_period(group)
        metrics["start"] = str(group.index.min().date())
        metrics["end"] = str(group.index.max().date())
        metrics["partial_year"] = int(year) not in COMPLETE_YEARS
        years[str(int(year))] = metrics
    return years


def summarize_stability(report: pd.DataFrame) -> dict[str, Any]:
    years = summarize_years(report)
    complete = [year for year in sorted(years) if int(year) in COMPLETE_YEARS]
    finite_sharpes = [
        float(years[year]["sharpe_ratio"])
        for year in complete
        if years[year].get("sharpe_ratio") is not None
    ]
    drawdowns = [
        float(years[year]["max_drawdown"])
        for year in complete
        if years[year].get("max_drawdown") is not None
    ]
    return {
        "full_period": summarize_period(report),
        "years": years,
        "complete_years": complete,
        "positive_complete_years": sum(
            float(years[year]["annualized_return"]) > 0.0 for year in complete
        ),
        "complete_year_sharpe_median": _finite(median(finite_sharpes)) if finite_sharpes else None,
        "worst_complete_year_max_drawdown": _finite(min(drawdowns)) if drawdowns else None,
    }
