"""Core strategy metrics must retain their own replay window and diagnostic status."""

import json
from pathlib import Path

import pytest

from quant_platform.research import ResearchCatalog
from tests.quant_platform.test_research import catalog, row, strategy_doc


ROOT = Path(__file__).resolve().parents[2]
HISTORICAL = {"start": "2020-08-03", "end": "2026-07-31"}
FORWARD = ["2026-08-03", "2026-10-28"]


@pytest.mark.parametrize(
    ("experiment_id", "cagr", "sharpe"),
    [
        ("diag/bt-v5-300k-current-cost-20261029", 0.35726948459578844, 1.203549370722813),
        ("diag/bt-v5-300k-risk1-current-cost-20261029", 0.372295975153784, 1.2086470017635766),
    ],
)
def test_archived_capital_cost_diagnostic_core_uses_historical_window(experiment_id, cagr, sharpe):
    c = ResearchCatalog(ROOT)

    record = next(r for r in c.snapshot()["records"] if r["id"] == experiment_id)

    assert record["window"] == HISTORICAL
    assert record["metrics"]["annualized_return"] == cagr
    assert record["metrics"]["sharpe_ratio"] == sharpe
    assert record["scope"] == "capital_cost_diagnostic"
    assert record["comparable"] is False
    source = next(r for r in record["resources"] if r["kind"] == "metrics" and r["available"])
    raw = json.loads(c.resource(source["id"]).read_text())
    assert raw["diagnostic_periods"][0]["start"] == "2026-08-03"
    assert raw["diagnostic_periods"][0]["end"] == "2026-10-28"


@pytest.mark.parametrize("diagnostic_marker", ["periods", "provenance", "report_kind"])
def test_main_nav_window_wins_over_forward_protocol_dates_without_promoting_diagnostic(
    tmp_path, diagnostic_marker
):
    doc = strategy_doc()
    del doc["backtest_window"]
    doc["ensemble"]["nav_curve"] = [["2020-08-03", 1.0, 1.0], ["2026-07-31", 4.0, 1.5]]
    diagnostic_period = {
        "start": "2026-08-03", "end": "2026-10-28",
        "metrics": {"annualized_return": 0.88, "cumulative_return": 0.156},
    }
    changes = {
        "report_kind": "phase_m_v1_bt",
        "eval_window": FORWARD,
        "eval_protocol": "主表2020-08-03~2026-07-31，新增2026-08-03~2026-10-28单列",
    }
    if diagnostic_marker == "periods":
        doc["diagnostic_periods"] = [diagnostic_period]
    elif diagnostic_marker == "provenance":
        doc["provenance"] = {"evaluation_mode": "frozen_parameter_diagnostic_not_selection"}
    else:
        changes["report_kind"] = "phase_m_v1_bt_capital_cost_diagnostic"
    c = catalog(tmp_path, [row("bt/replay", "strategy", **changes)], [doc])
    source_before = (tmp_path / "backtest/experiments/bt_replay.json").read_bytes()

    record = c.snapshot()["records"][0]

    assert record["window"] == HISTORICAL
    assert record["metrics"]["annualized_return"] == 0.26
    assert record["metrics"]["sharpe_ratio"] == 1.04
    assert record["scope"] == "capital_cost_diagnostic"
    assert record["comparable"] is False
    assert (tmp_path / "backtest/experiments/bt_replay.json").read_bytes() == source_before


def test_explicit_backtest_window_retains_authority_over_nav_and_protocol(tmp_path):
    doc = strategy_doc()
    doc["backtest_window"] = ["2021-01-04", "2026-07-31"]
    doc["ensemble"]["nav_curve"] = [["2020-08-03", 1.0], ["2026-07-31", 4.0]]
    original = row("bt/explicit", "strategy", eval_window=FORWARD)
    c = catalog(tmp_path, [original], [doc])

    record = c.snapshot()["records"][0]

    assert record["window"] == {"start": "2021-01-04", "end": "2026-07-31"}


def test_standard_strategy_with_nav_evidence_remains_comparable(tmp_path):
    doc = strategy_doc()
    del doc["backtest_window"]
    doc["ensemble"]["nav_curve"] = [["2020-08-03", 1.0], ["2026-07-31", 4.0]]
    c = catalog(tmp_path, [row("bt/standard", "strategy")], [doc])

    record = c.snapshot()["records"][0]

    assert record["window"] == HISTORICAL
    assert record["scope"] == "standard"
    assert record["comparable"] is True
