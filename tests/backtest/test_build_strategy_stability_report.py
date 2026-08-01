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
                    "calmar_ratio": 0.2 * index,
                    "annualized_volatility": 0.15,
                    "max_drawdown": -0.1,
                    "annualized_one_way_turnover": 3.0,
                    "benchmark_cumulative_return": 0.42,
                },
                "years": years,
                "positive_complete_years": 4,
                "complete_year_sharpe_median": 0.8,
                "worst_complete_year_max_drawdown": -0.2,
            }
        )
    if model_ref == "b6-m":
        baseline = next(
            item
            for item in rows
            if item["candidate_id"] == protocol.CURRENT_STRATEGY_BASELINE_ID
        )
        baseline["full_period"].update(
            annualized_return=0.1234,
            sharpe_ratio=1.234,
            calmar_ratio=0.987,
            annualized_volatility=0.222,
            max_drawdown=-0.2345,
            annualized_one_way_turnover=4.567,
        )
    return {
        "exp_id": f"strategy-stability-full-period/{model_ref}",
        "phase": "S",
        "model_ref": model_ref,
        "conclusion": "diagnostic_no_selection",
        "diagnostic_results": rows,
    }


def _baseline_row():
    return {
        "exp_id": "baseline/b2-s-on-b6-m",
        "phase": "S",
        "baseline_ref": "B2-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "selection_pool": "csi1000",
        "selection_segment": ["2020-01-13", "2021-07-15"],
        "test_segment": ["2021-07-16", "2026-07-31"],
        "strategy": {
            "candidate_id": "topk-t30-d2-h20",
            "strategy_class": "TopkDropoutStrategy",
            "topk": 30,
            "n_drop": 2,
            "hold_thresh": 20,
        },
    }


def test_report_keeps_only_b6_full_period_and_neighborhood_snapshot():
    html = report.build_html([_baseline_row(), _row("b1-m"), _row("b6-m")])
    soup = BeautifulSoup(html, "html.parser")

    first_table = soup.select_one("table")
    assert first_table is not None
    assert "baseline" in (first_table.get("class") or [])
    baseline_text = first_table.get_text(" ", strip=True)
    assert "B2-S v1.0" in baseline_text
    assert "B6 v1.0" in baseline_text
    assert "CSI1000" in baseline_text
    assert "2020-01-13 至 2026-07-31" in baseline_text
    assert "Top30 / d2 / h20" in baseline_text
    assert all(value in baseline_text for value in ("12.34%", "1.234", "0.987", "22.20%", "-23.45%", "4.567"))
    assert [section.get("id") for section in soup.select("section.model")] == ["b6-m"]
    assert soup.select_one("#b1-m") is None
    full_rows = soup.select("#b6-m table.full-period tbody tr")
    assert len(full_rows) == 22
    assert full_rows[0].select_one("td").get_text(strip=True) == "topk-t30-d2-h20"
    assert len(soup.select("#b6-m table.yearly tbody tr")) == 22 * 7
    headers = [header.get_text(strip=True) for header in soup.select("th")]
    assert "IR" not in headers
    assert all(label in headers for label in ("扣费年化", "夏普", "卡玛", "年化波动", "最大回撤", "年化单边换手"))
    assert all(label in headers for label in ("完整年正收益数", "完整年夏普中位", "最差完整年回撤"))
    assert "CSI1000 区间累计收益：42.00%" in soup.get_text()
    assert "部分年度" in soup.get_text()
    assert len(soup.select("#b6-neighborhood table.full-period tbody tr")) == 6
    assert soup.select_one("#b6-neighborhood table.yearly") is None
    assert "邻域自然年拆分" not in soup.get_text()
    assert "selected_candidate_id" not in html


def test_invalid_row_shows_concise_reason_and_attempt_count():
    row = _row("b6-m")
    invalid = row["diagnostic_results"][-1]
    invalid.update(
        status="invalid",
        error="continuous portfolio is incomplete from 2020-06-01\nnoisy traceback",
        previous_attempts=[{"status": "failed"}],
    )

    soup = BeautifulSoup(report.build_html([_baseline_row(), row]), "html.parser")
    last = soup.select_one("#b6-m table.full-period tbody tr:last-child").get_text(" ", strip=True)

    assert "continuous portfolio is incomplete from 2020-06-01" in last
    assert "2 次" in last
    assert "noisy traceback" not in last


def test_report_rejects_missing_b6_diagnostic_or_incomplete_candidate_set():
    with pytest.raises(ValueError, match="exactly one B6-M"):
        report.build_html([_baseline_row(), _row("b1-m")])

    row = _row("b6-m")
    row["diagnostic_results"].pop()
    with pytest.raises(ValueError, match="candidate set"):
        report.build_html([_baseline_row(), row])


def test_report_requires_exactly_one_current_strategy_baseline():
    diagnostic = _row("b6-m")

    with pytest.raises(ValueError, match="exactly one B2-S"):
        report.build_html([diagnostic])
    with pytest.raises(ValueError, match="exactly one B2-S"):
        report.build_html([_baseline_row(), _baseline_row(), diagnostic])
