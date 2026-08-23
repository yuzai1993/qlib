from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import build_regime_m0h20_top5d1_report as report  # noqa: E402
from ensemble_preds import ensemble_preds  # noqa: E402


def _seed_rec(ann: float, sharpe: float, year_ann: float, year: str = "2021") -> dict:
    return {
        "session_dir": "backtest/result/dummy",
        "full_period": {
            "annualized_return": ann,
            "sharpe_ratio": sharpe,
            "alpha": 0.1,
            "beta": 1.0,
            "max_drawdown": -0.2,
            "calmar_ratio": 1.0,
            "annualized_volatility": 0.3,
            "annualized_one_way_turnover": 40.0,
            "cumulative_return": 1.0,
            "benchmark_annualized_return": 0.1,
        },
        "years": {
            year: {
                "annualized_return": year_ann,
                "sharpe_ratio": 1.0,
                "alpha": 0.1,
                "beta": 1.0,
                "max_drawdown": -0.1,
                "annualized_one_way_turnover": 40.0,
                "benchmark_annualized_return": 0.1,
            }
        },
        "figures": {},
        "universe_filter": {},
    }


def test_ensemble_preds_zscore_then_mean(tmp_path):
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2020-08-03"]), ["A", "B"]],
        names=["datetime", "instrument"],
    )
    p1 = tmp_path / "s1.pkl"
    p2 = tmp_path / "s2.pkl"
    pd.Series([1.0, 3.0], index=idx, name="score").to_frame().to_pickle(p1)
    pd.Series([10.0, 30.0], index=idx, name="score").to_frame().to_pickle(p2)
    out = ensemble_preds([p1, p2])
    assert out.loc[pd.Timestamp("2020-08-03"), "B"] > out.loc[pd.Timestamp("2020-08-03"), "A"]


def test_report_includes_ensemble_signal_row():
    doc = {
        "backtest_window": ["2020-08-03", "2026-07-31"],
        "account": 1_000_000,
        "universe_filter": {
            "st_names": "backtest/configs/regime-adapt/st_names.csv",
            "min_amount": 10_000_000,
            "min_listing_days": 60,
        },
        "seeds": {
            "42": _seed_rec(0.30, 1.0, 0.20),
            "1000": _seed_rec(0.20, 0.8, 0.10),
        },
        "ensemble": {
            "method": "daily_zscore_mean",
            "session_dir": "backtest/result/dummy_ens",
            "full_period": {
                "annualized_return": 0.40,
                "sharpe_ratio": 1.25,
                "alpha": 0.22,
                "beta": 0.95,
                "max_drawdown": -0.18,
                "calmar_ratio": 2.2,
                "annualized_volatility": 0.28,
                "annualized_one_way_turnover": 46.0,
                "cumulative_return": 2.0,
                "benchmark_annualized_return": 0.1,
            },
            "years": {
                "2021": {
                    "annualized_return": 0.33,
                    "sharpe_ratio": 1.4,
                    "alpha": 0.15,
                    "beta": 0.9,
                    "max_drawdown": -0.08,
                    "annualized_one_way_turnover": 45.0,
                }
            },
            "figures": {},
        },
    }
    html = report.render_html(doc)
    assert "五种子均值信号" in html
    assert "+40.0%" in html
    assert "1.25" in html
    assert "截面 z-score" in html
    assert "2021" in html


def test_report_describes_daily_st_filter():
    doc = {
        "backtest_window": ["2020-08-03", "2026-07-31"],
        "account": 1_000_000,
        "universe_filter": {
            "st_daily": "scripts/data_collector/tushare/st_daily.csv",
            "min_amount": 10_000_000,
            "min_listing_days": 60,
            "min_recent_trading_days": 60,
        },
        "seeds": {"42": _seed_rec(0.22, 0.77, 0.38, year="2026")},
    }
    html = report.render_html(doc)
    assert "st_daily.csv" in html
    assert "近60" in html.replace(" ", "")
    assert "st_names.csv" not in html


def test_report_hides_stale_snapshot_ensemble():
    doc = {
        "backtest_window": ["2020-08-03", "2026-07-31"],
        "account": 1_000_000,
        "universe_filter": {"st_daily": "scripts/data_collector/tushare/st_daily.csv"},
        "seeds": {"42": _seed_rec(0.22, 0.77, 0.38, year="2026")},
        "ensemble": {
            "session_dir": "backtest/result/dummy_ens",
            "full_period": {
                "annualized_return": 0.99,
                "sharpe_ratio": 9.9,
                "alpha": 0.5,
                "beta": 1.0,
                "max_drawdown": -0.1,
                "calmar_ratio": 1.0,
                "annualized_volatility": 0.2,
                "annualized_one_way_turnover": 40.0,
                "cumulative_return": 1.0,
                "benchmark_annualized_return": 0.1,
            },
            "universe_filter": {"st_filter": "enabled", "n_st_symbols": 206},
        },
    }
    html = report.render_html(doc)
    assert "+99.0%" not in html
    assert "尚未用日频 ST 重跑" in html


def test_report_compares_before_and_after_fix():
    current = {
        "backtest_window": ["2020-08-03", "2026-07-31"],
        "account": 1_000_000,
        "universe_filter": {
            "st_daily": "scripts/data_collector/tushare/st_daily.csv",
            "min_amount": 10_000_000,
            "min_listing_days": 60,
        },
        "seeds": {
            "42": _seed_rec(0.2215, 0.77, 0.487, year="2026"),
            "1000": _seed_rec(0.2568, 0.86, 0.553, year="2026"),
        },
    }
    before = {
        "seeds": {
            "42": _seed_rec(0.327, 1.08, 0.425, year="2026"),
            "1000": _seed_rec(0.256, 0.73, -0.248, year="2026"),
        }
    }
    html = report.render_html(
        current,
        compare_runs=[("修复前（组合冻结 + 静态 ST）", before)],
    )
    assert "与修复前对照" in html
    assert "组合冻结 + 静态 ST" in html
    assert "日频 ST（当前）" in html
    assert "-24.8%" in html
    assert "+55.3%" in html
