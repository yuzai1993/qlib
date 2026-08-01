from __future__ import annotations

import sys
from pathlib import Path

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
                },
                "years": years,
                "positive_complete_years": 4,
                "complete_year_sharpe_median": 0.8,
                "worst_complete_year_max_drawdown": -0.2,
            }
        )
    return {
        "exp_id": f"strategy-stability-full-period/{model_ref}",
        "phase": "S",
        "model_ref": model_ref,
        "conclusion": "diagnostic_no_selection",
        "diagnostic_results": rows,
    }


def test_report_has_requested_metrics_baseline_first_years_and_b6_neighborhood():
    html = report.build_html([_row("b1-m"), _row("b6-m")])
    soup = BeautifulSoup(html, "html.parser")

    assert [section.get("id") for section in soup.select("section.model")] == ["b1-m", "b6-m"]
    for model_ref, expected_rows in (("b1-m", 18), ("b6-m", 22)):
        section = soup.select_one(f"#{model_ref}")
        full_rows = section.select("table.full-period tbody tr")
        assert len(full_rows) == expected_rows
        assert full_rows[0].select_one("td").get_text(strip=True) == protocol.BASELINE_CANDIDATE_ID
    headers = [header.get_text(strip=True) for header in soup.select("th")]
    assert "IR" not in headers
    assert all(label in headers for label in ("扣费年化", "夏普", "卡玛", "年化波动", "最大回撤", "年化单边换手"))
    assert "部分年度" in soup.get_text()
    assert len(soup.select("#b6-neighborhood table.full-period tbody tr")) == 6
    assert "selected_candidate_id" not in html
