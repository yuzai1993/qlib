from __future__ import annotations

import math
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest/scripts"
sys.path.insert(0, str(SCRIPTS))

import phase_s_protocol as protocol  # noqa: E402
import run_strategy_stability as stability  # noqa: E402
from strategy_stability_metrics import IncompletePortfolioError  # noqa: E402

BASE_CONFIG = ROOT / "backtest/configs/train-data/csi1000-full-v2/td_csi1000_full_v2_lgbm_s2000.yaml"


def test_full_config_uses_exact_period_account_costs_and_candidate():
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    candidate = next(
        row for row in protocol.strategy_grid("b1-m")
        if row["candidate_id"] == "topk-t30-d3-h1"
    )

    config = stability.build_stability_config(base, candidate)

    assert config["segments"]["test"] == ["2020-01-13", "2026-07-31"]
    assert config["data"]["instruments"] == "csi1000"
    assert config["data"]["benchmark"] == "SH000852"
    assert config["backtest"]["account"] == 500000
    assert config["backtest"]["exchange_kwargs"] == protocol.EXCHANGE_KWARGS
    assert config["strategy"]["topk"] == 30
    assert config["strategy"]["n_drop"] == 3
    assert config["strategy"]["hold_thresh"] == 1


def test_payload_has_complete_grid_baseline_first_and_no_selection_or_ir():
    rows = [
        {
            **candidate,
            "status": "success",
            "full_period": {"sharpe_ratio": index / 10},
            "years": {},
        }
        for index, candidate in enumerate(protocol.strategy_grid("b6-m"))
    ]

    payload = stability.build_diagnostic_payload("b6-m", rows)

    assert len(payload["all_rows"]) == 22
    assert payload["all_rows"][0]["candidate_id"] == protocol.BASELINE_CANDIDATE_ID
    assert "winner" not in payload
    assert "selected_candidate_id" not in payload
    assert "information_ratio" not in str(payload).lower()


def test_non_finite_requested_metric_is_invalid_not_success():
    row = {
        "status": "success",
        "full_period": {
            "annualized_return": 0.1,
            "sharpe_ratio": math.nan,
            "calmar_ratio": 0.4,
            "annualized_volatility": 0.2,
            "max_drawdown": -0.1,
            "annualized_one_way_turnover": 4.0,
        },
    }

    stability.classify_diagnostic_outcome(row)

    assert row["status"] == "invalid"
    assert "non-finite" in row["error"]


def test_interrupted_continuous_portfolio_is_invalid_not_retryable_failure():
    row = {"status": "failed"}

    stability.classify_diagnostic_exception(
        row, IncompletePortfolioError("portfolio is incomplete from 2020-06-01")
    )

    assert row["status"] == "invalid"
    assert row["error"] == "portfolio is incomplete from 2020-06-01"


def test_payload_rejects_missing_candidate():
    rows = [
        {**candidate, "status": "failed"}
        for candidate in protocol.strategy_grid("b1-m")[:-1]
    ]

    try:
        stability.build_diagnostic_payload("b1-m", rows)
    except ValueError as exc:
        assert "candidate set" in str(exc)
    else:
        raise AssertionError("missing candidate must be rejected")


def test_resume_retries_invalid_only_with_explicit_opt_in():
    grid = protocol.strategy_grid("b1-m")[:3]
    existing = [
        {**grid[0], "status": "success"},
        {**grid[1], "status": "invalid"},
        {**grid[2], "status": "failed"},
    ]

    selected = stability.select_resume_candidates(grid, existing)
    repair_selected = stability.select_resume_candidates(
        grid, existing, retry_invalid=True
    )

    assert [item["candidate_id"] for item in selected] == [grid[2]["candidate_id"]]
    assert [item["candidate_id"] for item in repair_selected] == [
        grid[1]["candidate_id"],
        grid[2]["candidate_id"],
    ]
