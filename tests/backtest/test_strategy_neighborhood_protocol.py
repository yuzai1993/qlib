from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import strategy_neighborhood_protocol as protocol  # noqa: E402


def _success(candidate: dict, ir: float = 1.0) -> dict:
    return {
        **candidate,
        "status": "success",
        "excess_with_cost_information_ratio": ir,
        "excess_with_cost_annualized_return": 0.12,
        "excess_with_cost_max_drawdown": -0.20,
        "annualized_one_way_turnover": 5.0,
    }


def test_grid_is_exact_unique_540_candidate_neighborhood():
    grid = protocol.strategy_neighborhood_grid()

    assert len(grid) == 540
    assert len({row["candidate_id"] for row in grid}) == 540
    assert {row["topk"] for row in grid} == {26, 28, 30, 32, 34}
    assert {row["n_drop"] for row in grid} == {1, 2, 3, 4}
    assert {row["hold_thresh"] for row in grid} == set(range(12, 30, 2))
    assert {row["risk_degree"] for row in grid} == {0.90, 0.95, 1.00}
    assert protocol.BASELINE_CANDIDATE_ID in {
        row["candidate_id"] for row in grid
    }


def test_baseline_has_self_and_eight_axial_neighbors():
    grid = protocol.strategy_neighborhood_grid()
    baseline = next(
        row for row in grid if row["candidate_id"] == protocol.BASELINE_CANDIDATE_ID
    )

    ids = protocol.neighborhood_ids(baseline, grid)

    assert len(ids) == 9
    assert protocol.BASELINE_CANDIDATE_ID in ids
    assert "topk-t28-d2-h20-r095" in ids
    assert "topk-t32-d2-h20-r095" in ids
    assert "topk-t30-d1-h20-r095" in ids
    assert "topk-t30-d3-h20-r095" in ids
    assert "topk-t30-d2-h18-r095" in ids
    assert "topk-t30-d2-h22-r095" in ids
    assert "topk-t30-d2-h20-r090" in ids
    assert "topk-t30-d2-h20-r100" in ids


def test_scoring_rejects_incomplete_preregistered_candidate_set():
    grid = protocol.strategy_neighborhood_grid()
    rows = [_success(candidate) for candidate in grid[:-1]]

    with pytest.raises(ValueError, match="candidate set"):
        protocol.score_valid_candidates(rows, grid)


def test_robust_selection_prefers_supported_plateau_over_single_spike():
    grid = protocol.strategy_neighborhood_grid()
    baseline = next(
        row for row in grid if row["candidate_id"] == protocol.BASELINE_CANDIDATE_ID
    )
    supported = protocol.neighborhood_ids(baseline, grid)
    rows = [_success(candidate) for candidate in grid]
    by_id = {row["candidate_id"]: row for row in rows}
    for candidate_id in supported:
        by_id[candidate_id]["excess_with_cost_information_ratio"] = 2.0
    spike_id = "topk-t34-d4-h28-r100"
    by_id[spike_id]["excess_with_cost_information_ratio"] = 3.0

    scored, winner = protocol.score_valid_candidates(rows, grid)

    assert winner["candidate_id"] == protocol.BASELINE_CANDIDATE_ID
    assert winner["neighbor_ir_p25"] == pytest.approx(2.0)
    assert next(row for row in scored if row["candidate_id"] == spike_id)[
        "neighbor_ir_p25"
    ] == pytest.approx(1.0)


def test_non_finite_neighbor_makes_candidate_ineligible():
    grid = protocol.strategy_neighborhood_grid()
    rows = [_success(candidate) for candidate in grid]
    by_id = {row["candidate_id"]: row for row in rows}
    by_id["topk-t30-d2-h20-r090"]["excess_with_cost_information_ratio"] = float("nan")

    scored, _ = protocol.score_valid_candidates(rows, grid)
    baseline = next(
        row for row in scored if row["candidate_id"] == protocol.BASELINE_CANDIDATE_ID
    )

    assert baseline["neighborhood_complete"] is False
    assert baseline["neighbor_ir_p25"] is None
