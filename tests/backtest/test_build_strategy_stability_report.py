from __future__ import annotations

import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import build_strategy_stability_report as report  # noqa: E402
import phase_s_protocol as protocol  # noqa: E402


def _row(model_ref: str):
    rows = []
    for index, candidate in enumerate(protocol.strategy_grid(model_ref)):
        years = {
            str(year): {
                "partial_year": year in (2020, 2026),
                "annualized_return": 0.01 * index,
                "sharpe_ratio": 0.1 * index,
                "alpha": 0.005 * index,
                "beta": 0.8,
                "benchmark_cumulative_return": 0.42,
                "calmar_ratio": 0.2 * index,
                "annualized_volatility": 0.15,
                "max_drawdown": -0.1,
                "annualized_one_way_turnover": 3.0,
            }
            for year in range(2020, 2027)
        }
        rows.append(
            {
                **candidate,
                "status": "success",
                "full_period": {
                    "annualized_return": 0.01 * index,
                    "sharpe_ratio": 0.1 * index,
                    "alpha": 0.005 * index,
                    "beta": 0.8,
                    "benchmark_cumulative_return": 0.42,
                    "calmar_ratio": 0.2 * index,
                    "annualized_volatility": 0.15,
                    "max_drawdown": -0.1,
                    "annualized_one_way_turnover": 3.0,
                },
                "years": years,
                "positive_complete_years": 4,
                "complete_year_sharpe_median": 0.8,
                "worst_complete_year_max_drawdown": -0.2,
            }
        )
    return {
        "exp_id": f"strategy-stability-full-period/{model_ref}-a10m",
        "phase": "S",
        "model_ref": model_ref,
        "account": 10_000_000,
        "conclusion": "diagnostic_no_selection",
        "diagnostic_results": rows,
    }


def _baseline_row():
    years = {
        str(year): {
            "partial_year": year in (2020, 2026),
            "annualized_return": 0.20,
            "sharpe_ratio": 1.1,
            "alpha": 0.18,
            "beta": 0.61,
            "benchmark_cumulative_return": 0.21,
            "calmar_ratio": 0.9,
            "annualized_volatility": 0.20,
            "max_drawdown": -0.24,
            "annualized_one_way_turnover": 21.0,
        }
        for year in range(2020, 2027)
    }
    return {
        "exp_id": "baseline/b4-s-on-b6-m",
        "phase": "S",
        "state": "baseline",
        "baseline_ref": "B4-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "selection_pool": "csi1000",
        "selection_segment": ["2020-01-13", "2026-07-31"],
        "test_segment": ["2020-01-13", "2026-07-31"],
        "neighbor_ir_p25": 0.837,
        "strategy": {
            "candidate_id": "topk-t22-d2-h2",
            "strategy_class": "TopkDropoutStrategy",
            "topk": 22,
            "n_drop": 2,
            "hold_thresh": 2,
            "risk_degree": 0.90,
        },
        "full_period": {
            "annualized_return": 0.2386,
            "sharpe_ratio": 1.165,
            "alpha": 0.201,
            "beta": 0.614,
            "benchmark_cumulative_return": 0.209,
            "calmar_ratio": 0.971,
            "annualized_volatility": 0.205,
            "max_drawdown": -0.246,
            "annualized_one_way_turnover": 21.19,
        },
        "years": years,
    }


def test_report_keeps_only_baseline_grid_and_baseline_yearly():
    html = report.build_html([_baseline_row(), _row("b1-m"), _row("b6-m")])
    soup = BeautifulSoup(html, "html.parser")

    first_table = soup.select_one("table")
    assert first_table is not None
    assert "baseline" in (first_table.get("class") or [])
    baseline_text = first_table.get_text(" ", strip=True)
    assert "B4-S v1.0" in baseline_text
    assert "Top22 / d2 / h2" in baseline_text
    assert "邻域行" in baseline_text
    assert "邻域 IR P25" not in baseline_text
    assert "Alpha" in html and "Beta" in html and "基准涨幅" in html
    # 邻域行与普通行同列，且不再单独挂邻域 IR P25 列
    baseline_rows = first_table.select("tbody tr")
    assert len(baseline_rows) == 2
    assert len(baseline_rows[0].select("td.num")) == len(baseline_rows[1].select("td.num"))
    assert len(METRICS := __import__("build_strategy_stability_report", fromlist=["METRICS"]).METRICS) == len(
        baseline_rows[0].select("td.num")
    )
    assert [section.get("id") for section in soup.select("section")] == [
        "current-baseline",
        "b6-m",
        "baseline-yearly",
    ]
    assert soup.select_one("#full-neighborhood") is None
    full_rows = soup.select("#b6-m table.full-period tbody tr")
    assert len(full_rows) == 22
    yearly_rows = soup.select("#baseline-yearly table.yearly tbody tr")
    assert len(yearly_rows) == 7
    assert soup.select_one("#b6-neighborhood") is None
    assert soup.select_one("#full-neighborhood") is None
    assert soup.select_one("#beta-overlay") is None
    assert soup.select_one("#phase-s-audit-index") is None


def test_report_rejects_missing_baseline_or_incomplete_grid():
    with pytest.raises(ValueError, match="exactly one B6-M"):
        report.build_html([_baseline_row()])
    with pytest.raises(ValueError, match="exactly one current strategy baseline"):
        report.build_html([_row("b6-m")])
    row = _row("b6-m")
    row["diagnostic_results"].pop()
    with pytest.raises(ValueError, match="candidate set"):
        report.build_html([_baseline_row(), row])
