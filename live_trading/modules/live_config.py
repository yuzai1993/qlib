"""Live Trading standalone configuration loading and validation."""

from datetime import date
import math
from pathlib import Path
import re

import yaml


_BASELINE_KEYS = {
    "first_snapshot_date", "opening_total_value", "benchmark_close",
}


def _validate_performance_baseline(config: dict) -> None:
    baseline = config.get("monitor", {}).get("performance_baseline")
    if baseline is None:
        return
    if not isinstance(baseline, dict) or set(baseline) != _BASELINE_KEYS:
        raise ValueError(
            "monitor.performance_baseline must contain exactly "
            "first_snapshot_date, opening_total_value, benchmark_close"
        )
    raw_date = baseline["first_snapshot_date"]
    if not isinstance(raw_date, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", raw_date,
    ):
        raise ValueError(
            "monitor.performance_baseline.first_snapshot_date must be YYYY-MM-DD"
        )
    try:
        date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError(
            "monitor.performance_baseline.first_snapshot_date is invalid"
        ) from exc
    for key in ("opening_total_value", "benchmark_close"):
        value = baseline[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(
                f"monitor.performance_baseline.{key} must be a positive number"
            )


def _validate_simulation_config(config: dict) -> None:
    live = config.get("live", {})
    if "broker_environment" not in live:
        return
    if live.get("broker_environment") != "SIMULATION":
        raise ValueError("live.broker_environment must be SIMULATION")
    if live.get("allow_real_money") is not False:
        raise ValueError("live.allow_real_money must be false")
    if live.get("after_hours_price_type") != 49:
        raise ValueError("live.after_hours_price_type must be 49")

    opening_cash = config.get("account", {}).get("opening_cash")
    if (
        isinstance(opening_cash, bool)
        or not isinstance(opening_cash, (int, float))
        or not math.isfinite(opening_cash)
        or opening_cash <= 0
    ):
        raise ValueError("account.opening_cash must be a positive number")

    strategy = config.get("strategy", {})
    topk = strategy.get("topk")
    initial_buy_count = strategy.get("initial_buy_count")
    if (
        isinstance(initial_buy_count, bool)
        or not isinstance(initial_buy_count, int)
        or not isinstance(topk, int)
        or initial_buy_count <= 0
        or initial_buy_count > topk
    ):
        raise ValueError(
            "strategy.initial_buy_count must be a positive integer no greater than topk"
        )


def load_live_config(config_path, project_root=None) -> dict:
    """Load one self-contained Live Trading YAML file.

    Args:
        config_path: live yaml 路径
        project_root: retained for caller compatibility; no path inheritance occurs
    """
    config_path = Path(config_path)

    with open(config_path, encoding="utf-8") as f:
        merged = yaml.safe_load(f)
    if not isinstance(merged, dict):
        raise ValueError(f"live config must be a mapping: {config_path}")
    if "base_config" in merged:
        raise ValueError("live config must be standalone; base_config is forbidden")

    _validate_performance_baseline(merged)
    _validate_simulation_config(merged)
    merged["_config_path"] = str(config_path)
    merged["_config_id"] = config_path.stem
    return merged
