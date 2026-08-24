"""Live Trading standalone configuration loading and validation."""

from datetime import date
import math
from pathlib import Path
import re

import yaml

from live_trading.modules.execution_profile import get_execution_profile


_BASELINE_KEYS = {
    "first_snapshot_date", "opening_total_value", "benchmark_close",
}
# Historical operator-probe pairing. Leave pointing at CSI1000 so a
# leftover probe yaml cannot pause the ladder.
_OPERATOR_PROBE_MAIN_STRATEGY_ID = "csi1000_b6m_b2s_postclose_real"


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
    kind = live.get("kind", "STRATEGY")
    if kind not in {"STRATEGY", "OPERATOR_PROBE"}:
        raise ValueError("live.kind must be STRATEGY or OPERATOR_PROBE")

    profile = get_execution_profile(live.get("execution_session"))
    # BT v4 真阶梯把主策略搬到盘后固定价，所以 STRATEGY 两个通道都合法；价类型与
    # 四个时点仍逐项对齐 profile，半切换的配置照旧 fail-closed。
    if kind == "STRATEGY" and profile.name not in {
        "CLOSE_AUCTION", "AFTER_HOURS_FIXED_PRICE",
    }:
        raise ValueError(
            "live.kind STRATEGY requires CLOSE_AUCTION or AFTER_HOURS_FIXED_PRICE"
        )
    if kind == "OPERATOR_PROBE" and profile.name != "AFTER_HOURS_FIXED_PRICE":
        raise ValueError(
            "live.kind OPERATOR_PROBE requires AFTER_HOURS_FIXED_PRICE"
        )
    if live.get("close_auction_price_type") != profile.qmt_price_type:
        raise ValueError(
            "live.close_auction_price_type must match execution profile "
            f"price_type {profile.qmt_price_type}"
        )
    for field in ("submit_after", "cancel_at", "finalize_at", "snapshot_after"):
        if field not in live:
            raise ValueError(f"live.{field} is required")
        configured = live[field]
        expected = getattr(profile, field)
        if configured != expected:
            raise ValueError(
                f"live.{field} must match execution profile: {expected}"
            )

    if kind == "OPERATOR_PROBE":
        if (
            environment != "REAL"
            or allow_real_money is not True
            or live.get("default_mode") != "LIVE"
        ):
            raise ValueError("OPERATOR_PROBE requires REAL/LIVE")
        if live.get("strategy_id") != "csi1000_pr49_one_lot_probe":
            raise ValueError(
                "OPERATOR_PROBE requires strategy_id "
                "csi1000_pr49_one_lot_probe"
            )
        main_strategy_id = live.get("main_strategy_id")
        if main_strategy_id != _OPERATOR_PROBE_MAIN_STRATEGY_ID:
            raise ValueError(
                "OPERATOR_PROBE requires live.main_strategy_id "
                f"{_OPERATOR_PROBE_MAIN_STRATEGY_ID}"
            )
        bridge_root = live.get("bridge_root")
        if (
            not isinstance(bridge_root, str)
            or not bridge_root.rstrip("/").endswith("/pr49_probe")
        ):
            raise ValueError(
                "OPERATOR_PROBE bridge_root must end with /pr49_probe"
            )
        if live.get("max_orders_per_day") != 100:
            raise ValueError("OPERATOR_PROBE max_orders_per_day must be 100")

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

    if kind == "STRATEGY":
        strategy = config.get("strategy", {})
        topk = strategy.get("topk")
        if strategy.get("class") == "CohortLadderStrategy":
            # 阶梯每天恒定加一层、h 天后满仓，建仓爬坡是结构性的，
            # 没有 TopkDropout 那种首日限量的 initial_buy_count。
            horizon = strategy.get("horizon")
            if (
                isinstance(horizon, bool)
                or not isinstance(horizon, int)
                or not isinstance(topk, int)
                or horizon <= 0
                or topk <= 0
            ):
                raise ValueError(
                    "CohortLadderStrategy requires positive integer "
                    "strategy.topk and strategy.horizon"
                )
            return
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
