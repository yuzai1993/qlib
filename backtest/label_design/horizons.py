"""Pure helpers for cumulative and holding-survival return labels."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import pandas as pd


def cumulative_label(horizon: int) -> str:
    """Return the H-day close return entered at t+1."""
    horizon = int(horizon)
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    return f"Ref($close, -{horizon + 1})/Ref($close, -1)-1"


def survival_weighted_label(
    survival: Mapping[int, float],
    *,
    max_horizon: int,
) -> tuple[str, dict[int, float]]:
    """Build a normalized sum of forward one-day returns."""
    return survival_power_weighted_label(
        survival,
        max_horizon=max_horizon,
        power=1.0,
    )


def survival_power_weighted_label(
    survival: Mapping[int, float],
    *,
    max_horizon: int,
    power: float,
) -> tuple[str, dict[int, float]]:
    """Build forward-return weights proportional to survival probability^power."""
    max_horizon = int(max_horizon)
    if max_horizon <= 0:
        raise ValueError("max_horizon must be positive")
    power = float(power)
    if not math.isfinite(power) or power <= 0:
        raise ValueError("power must be positive and finite")
    raw: dict[int, float] = {}
    for age in range(1, max_horizon + 1):
        if age not in survival:
            raise ValueError(f"survival curve is missing age {age}")
        value = float(survival[age])
        if value < 0:
            raise ValueError(f"survival at age {age} must be nonnegative")
        raw[age] = value**power
    total = sum(raw.values())
    if total <= 0:
        raise ValueError("survival weights must have a positive sum")
    weights = {age: value / total for age, value in raw.items()}
    terms = [
        (
            f"{weights[age]:.12f}*"
            f"(Ref($close, -{age + 1})/Ref($close, -{age})-1)"
        )
        for age in range(1, max_horizon + 1)
    ]
    return "+".join(terms), weights


def select_horizon_anchors(
    quantiles: Mapping[str, float],
    anchors: Sequence[int],
) -> list[int]:
    """Map P50/P75/P90 to distinct nearest anchors."""
    keys = ("p50", "p75", "p90")
    missing = [key for key in keys if key not in quantiles]
    if missing:
        raise ValueError(f"missing holding quantile: {missing[0]}")
    available = sorted({int(anchor) for anchor in anchors})
    if len(available) < len(keys):
        raise ValueError("at least three distinct anchors are required")

    selected: list[int] = []
    unused = set(available)
    for key in keys:
        raw = float(quantiles[key])
        choice = min(unused, key=lambda anchor: (abs(anchor - raw), anchor))
        selected.append(choice)
        unused.remove(choice)
    return selected


def common_self_eval_end(
    calendar: Sequence,
    *,
    official_end: str,
    max_horizon: int,
) -> str:
    """Move the official end back by H+1 trading dates."""
    dates = pd.DatetimeIndex(calendar).sort_values().unique()
    end = pd.Timestamp(official_end)
    eligible = dates[dates <= end]
    offset = int(max_horizon) + 1
    if offset <= 1:
        raise ValueError("max_horizon must be positive")
    if len(eligible) <= offset:
        raise ValueError("calendar is too short for the requested horizon")
    return str(pd.Timestamp(eligible[-1 - offset]).date())
