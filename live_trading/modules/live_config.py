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


def _validate_trading_config(config: dict) -> None:
    live = config.get("live", {})
    if "broker_environment" not in live:
        return
    environment = live.get("broker_environment")
    allow_real_money = live.get("allow_real_money")
    if environment not in {"SIMULATION", "REAL"}:
        raise ValueError(
            "live.broker_environment must be SIMULATION or REAL"
        )
    if environment == "SIMULATION" and allow_real_money is not False:
        raise ValueError(
            "SIMULATION broker_environment requires allow_real_money=false"
        )
    if environment == "REAL" and allow_real_money is not True:
        raise ValueError(
            "REAL broker_environment requires allow_real_money=true"
        )
    if environment == "REAL" and live.get("default_mode") != "LIVE":
        raise ValueError("REAL broker_environment requires default_mode=LIVE")
    if live.get("execution_session") != "CLOSE_AUCTION":
        raise ValueError("live.execution_session must be CLOSE_AUCTION")
    if live.get("close_auction_price_type") != 11:
        raise ValueError("live.close_auction_price_type must be 11")
    if live.get("submit_after") != "14:57:05":
        raise ValueError("live.submit_after must be 14:57:05")

    opening_cash = config.get("account", {}).get("opening_cash")
    if (
        isinstance(opening_cash, bool)
        or not isinstance(opening_cash, (int, float))
        or not math.isfinite(opening_cash)
        or opening_cash <= 0
    ):
        raise ValueError("account.opening_cash must be a positive number")

    opening_value_adjustment = config.get("account", {}).get(
        "opening_value_adjustment", 0.0,
    )
    if (
        isinstance(opening_value_adjustment, bool)
        or not isinstance(opening_value_adjustment, (int, float))
        or not math.isfinite(opening_value_adjustment)
    ):
        raise ValueError(
            "account.opening_value_adjustment must be a finite number"
        )
    if opening_cash + opening_value_adjustment <= 0:
        raise ValueError(
            "account economic opening value must be positive"
        )

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
    _validate_trading_config(merged)
    merged["_config_path"] = str(config_path)
    merged["_config_id"] = config_path.stem
    return merged
