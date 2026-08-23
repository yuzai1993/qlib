"""After-cost absolute-return metrics for full-period strategy diagnostics.

Official annualized_return is CAGR from the compounded wealth path.
annualized_return_arith (mean × 250) is audit-only. Sharpe still uses the
arithmetic mean in the numerator.
"""

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


def _cagr(end_wealth: float, n_days: int) -> float | None:
    """把一段净值终点折成年化复利（一年 TRADING_DAYS 个交易日）。"""
    if n_days <= 0 or end_wealth <= 0:
        return None
    return _finite(end_wealth ** (TRADING_DAYS / n_days) - 1.0)


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
    n_days = int(len(clean))
    annualized_return_arith = float(net.mean() * TRADING_DAYS)
    benchmark_annualized_return_arith = float(bench.mean() * TRADING_DAYS)
    daily_std = float(net.std(ddof=1)) if n_days > 1 else float("nan")
    annualized_volatility = _finite(daily_std * math.sqrt(TRADING_DAYS))
    sharpe = (
        _finite(annualized_return_arith / annualized_volatility)
        if annualized_volatility not in (None, 0.0)
        else None
    )
    wealth = (1.0 + net).cumprod()
    end_wealth = float(wealth.iloc[-1])
    annualized_return = _cagr(end_wealth, n_days)
    drawdown = wealth / wealth.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    calmar = (
        _finite(annualized_return / abs(max_drawdown))
        if annualized_return is not None and max_drawdown != 0.0
        else None
    )
    bench_wealth = float((1.0 + bench).prod())
    benchmark_cumulative_return = bench_wealth - 1.0
    benchmark_annualized_return = _cagr(bench_wealth, n_days)
    beta = None
    alpha = None
    if n_days > 1:
        bench_var = float(bench.var(ddof=1))
        if math.isfinite(bench_var) and bench_var > 0.0:
            beta = _finite(float(net.cov(bench)))
            if beta is not None:
                beta = _finite(beta / bench_var)
            if beta is not None:
                alpha = _finite((float(net.mean()) - float(beta) * float(bench.mean())) * TRADING_DAYS)
    return {
        "n_days": n_days,
        "annualized_return": annualized_return,
        "annualized_return_arith": _finite(annualized_return_arith),
        "sharpe_ratio": sharpe,
        "alpha": alpha,
        "beta": beta,
        "calmar_ratio": calmar,
        "annualized_volatility": annualized_volatility,
        "max_drawdown": _finite(max_drawdown),
        "annualized_one_way_turnover": _finite(float(clean["turnover"].mean() * TRADING_DAYS / 2.0)),
        "cumulative_return": _finite(end_wealth - 1.0),
        "benchmark_annualized_return": benchmark_annualized_return,
        "benchmark_annualized_return_arith": _finite(benchmark_annualized_return_arith),
        "benchmark_cumulative_return": _finite(benchmark_cumulative_return),
    }


def assign_month_regimes(
    index: pd.DatetimeIndex, labels: pd.Series
) -> pd.Series:
    """把交易日映射到月度风格标签（标签索引为月末日期）。"""
    if labels.empty:
        return pd.Series(index=index, dtype=object)
    month_map = pd.Series(
        labels.astype(str).to_numpy(),
        index=pd.DatetimeIndex(labels.index).to_period("M"),
    )
    return pd.Series(index.to_period("M"), index=index).map(month_map)


def summarize_regimes(
    report: pd.DataFrame, labels: pd.Series
) -> dict[str, dict[str, Any]]:
    """按月度风格切日收益，对每个风格调用 summarize_period。"""
    report = _validate_report(report)
    assigned = assign_month_regimes(report.index, labels)
    regimes: dict[str, dict[str, Any]] = {}
    for name in sorted({str(v) for v in assigned.dropna().unique()}):
        group = report[assigned == name]
        if group.empty:
            continue
        metrics = summarize_period(group)
        metrics["start"] = str(group.index.min().date())
        metrics["end"] = str(group.index.max().date())
        regimes[name] = metrics
    return regimes


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


def load_regime_labels(path) -> pd.Series:
    frame = pd.read_csv(path, comment="#")
    if "datetime" not in frame.columns or "regime3" not in frame.columns:
        raise ValueError("regime labels need datetime,regime3")
    return pd.Series(
        frame["regime3"].astype(str).to_numpy(),
        index=pd.to_datetime(frame["datetime"]),
        name="regime3",
    )


def summarize_stability(
    report: pd.DataFrame, regime_labels: pd.Series | None = None
) -> dict[str, Any]:
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
        "regimes": summarize_regimes(report, regime_labels) if regime_labels is not None else {},
    }
