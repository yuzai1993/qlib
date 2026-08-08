"""Immutable contracts for the B3-S local TopkDropout neighborhood experiment."""

from __future__ import annotations

import copy
import math
from typing import Any, Sequence

import numpy as np

# Centered on B3-S: topk=20, n_drop=2, hold_thresh=10, risk_degree=0.95
TOPK_VALUES = (16, 18, 20, 22, 24)
N_DROP_VALUES = (1, 2, 3, 4)
HOLD_VALUES = (2, 4, 6, 8, 10, 12, 14, 16, 18)
RISK_VALUES = (0.90, 0.95, 1.00)
BASELINE_CANDIDATE_ID = "topk-t20-d2-h10-r095"
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


# Absolute-return fields used by the Phase S baseline table's neighborhood row.
# Each value is the P25 across the axial neighborhood (same rule as neighbor_ir_p25).
NEIGHBOR_ABSOLUTE_METRIC_KEYS = (
    ("annualized_return", ("absolute_portfolio.annualized_return", "portfolio_annualized_return")),
    ("sharpe_ratio", ("absolute_portfolio.sharpe_ratio", "portfolio_information_ratio")),
    ("alpha", ("absolute_portfolio.alpha", "alpha")),
    ("beta", ("absolute_portfolio.beta", "beta")),
    (
        "benchmark_cumulative_return",
        (
            "absolute_portfolio.benchmark_cumulative_return",
            "benchmark_cumulative_return",
            "benchmark_cum_return",
        ),
    ),
    ("calmar_ratio", ("absolute_portfolio.calmar_ratio",)),
    ("annualized_volatility", ("absolute_portfolio.annualized_volatility",)),
    (
        "max_drawdown",
        ("absolute_portfolio.max_drawdown", "portfolio_max_drawdown"),
    ),
    (
        "annualized_one_way_turnover",
        (
            "absolute_portfolio.annualized_one_way_turnover",
            "annualized_one_way_turnover",
        ),
    ),
)


def _nested_finite(row: dict[str, Any], dotted_key: str) -> float | None:
    current: Any = row
    for part in dotted_key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    try:
        value = float(current)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _metric_from_row(row: dict[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = _nested_finite(row, key) if "." in key else _finite(row, key)
        if value is not None:
            return value
    return None


def neighbor_absolute_metric_p25(
    candidate: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    grid: Sequence[dict[str, Any]],
) -> dict[str, float | None]:
    """P25 of each absolute-return metric over the candidate's axial neighborhood."""
    neighbor_ids = neighborhood_ids(candidate, grid)
    out: dict[str, float | None] = {}
    for metric_key, source_keys in NEIGHBOR_ABSOLUTE_METRIC_KEYS:
        values = []
        for neighbor_id in neighbor_ids:
            row = by_id.get(neighbor_id) or {}
            if row.get("status") != "success":
                continue
            value = _metric_from_row(row, source_keys)
            if value is not None:
                values.append(value)
        out[metric_key] = (
            float(np.quantile(values, 0.25)) if values else None
        )
    return out


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
        result["neighbor_metrics_p25"] = neighbor_absolute_metric_p25(
            candidate, by_id, candidates
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
