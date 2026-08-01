"""Immutable contracts for the B2-S local TopkDropout neighborhood experiment."""

from __future__ import annotations

import copy
import math
from typing import Any, Sequence

import numpy as np

TOPK_VALUES = (26, 28, 30, 32, 34)
N_DROP_VALUES = (1, 2, 3, 4)
HOLD_VALUES = (12, 14, 16, 18, 20, 22, 24, 26, 28)
RISK_VALUES = (0.90, 0.95, 1.00)
BASELINE_CANDIDATE_ID = "topk-t30-d2-h20-r095"
IR_KEY = "excess_with_cost_information_ratio"
ANN_KEY = "excess_with_cost_annualized_return"
MDD_KEY = "excess_with_cost_max_drawdown"
TURNOVER_KEY = "annualized_one_way_turnover"


def _candidate_id(topk: int, n_drop: int, hold_thresh: int, risk_degree: float) -> str:
    return (
        f"topk-t{topk}-d{n_drop}-h{hold_thresh}-"
        f"r{int(round(risk_degree * 100)):03d}"
    )


def strategy_neighborhood_grid() -> list[dict[str, Any]]:
    rows = [
        {
            "candidate_id": _candidate_id(topk, n_drop, hold, risk),
            "strategy_class": "TopkDropoutStrategy",
            "topk": topk,
            "n_drop": n_drop,
            "hold_thresh": hold,
            "risk_degree": risk,
        }
        for topk in TOPK_VALUES
        for n_drop in N_DROP_VALUES
        for hold in HOLD_VALUES
        for risk in RISK_VALUES
    ]
    if len(rows) != 540 or len({row["candidate_id"] for row in rows}) != len(rows):
        raise AssertionError("strategy neighborhood grid must contain 540 unique candidates")
    return rows


def _coordinates(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        TOPK_VALUES.index(int(candidate["topk"])),
        N_DROP_VALUES.index(int(candidate["n_drop"])),
        HOLD_VALUES.index(int(candidate["hold_thresh"])),
        RISK_VALUES.index(float(candidate["risk_degree"])),
    )


def neighborhood_ids(
    candidate: dict[str, Any], grid: Sequence[dict[str, Any]]
) -> set[str]:
    center = _coordinates(candidate)
    ids = set()
    for other in grid:
        point = _coordinates(other)
        distance = sum(abs(left - right) for left, right in zip(center, point))
        if distance <= 1:
            ids.add(str(other["candidate_id"]))
    return ids


def _finite(row: dict[str, Any], key: str) -> float | None:
    try:
        value = float(row.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def score_valid_candidates(
    rows: Sequence[dict[str, Any]],
    grid: Sequence[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = list(grid or strategy_neighborhood_grid())
    expected_ids = {str(candidate["candidate_id"]) for candidate in candidates}
    actual_ids = {str(row.get("candidate_id") or "") for row in rows}
    if len(rows) != len(expected_ids) or actual_ids != expected_ids:
        raise ValueError("valid result candidate set differs from preregistered grid")
    by_id = {str(row["candidate_id"]): row for row in rows}
    scored: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        result = copy.deepcopy(by_id[candidate_id])
        neighbor_ids = neighborhood_ids(candidate, candidates)
        neighbor_irs = [
            _finite(by_id[neighbor_id], IR_KEY)
            if by_id[neighbor_id].get("status") == "success"
            else None
            for neighbor_id in neighbor_ids
        ]
        own_metrics = [
            _finite(result, key) for key in (IR_KEY, ANN_KEY, MDD_KEY, TURNOVER_KEY)
        ]
        complete = all(value is not None for value in neighbor_irs) and all(
            value is not None for value in own_metrics
        )
        result["neighbor_count"] = len(neighbor_ids)
        result["neighborhood_complete"] = complete
        result["neighbor_ir_p25"] = (
            float(np.quantile([float(value) for value in neighbor_irs], 0.25))
            if complete
            else None
        )
        scored.append(result)
        if complete:
            eligible.append(result)
    if not eligible:
        raise ValueError("no candidate has a complete finite axial neighborhood")
    winner = min(
        eligible,
        key=lambda row: (
            -float(row["neighbor_ir_p25"]),
            -float(row[IR_KEY]),
            -float(row[ANN_KEY]),
            -float(row[MDD_KEY]),
            float(row[TURNOVER_KEY]),
            str(row["candidate_id"]),
        ),
    )
    return scored, copy.deepcopy(winner)
