"""Fail-closed validation of Live Trading against its designated Backtest."""

from __future__ import annotations

import math
from pathlib import Path

import yaml


class ParityError(ValueError):
    """A decision-critical Live/Backtest setting has drifted."""


def _get(mapping: dict, path: str):
    current = mapping
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return f"<missing:{path}>"
        current = current[key]
    return current


def _equal(left, right) -> bool:
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)
    return left == right


def validate_backtest_parity(live: dict, backtest: dict) -> None:
    """Raise with all mismatches between Live and its parity Backtest."""
    live_fees = live.get("fees", {})
    buy_cost = (
        float(live_fees.get("commission_rate", 0.0))
        + float(live_fees.get("transfer_fee_rate", 0.0))
    )
    sell_cost = buy_cost + float(live_fees.get("stamp_duty_rate", 0.0))

    comparisons = [
        ("model.experiment_name", _get(live, "model.experiment_name"),
         _get(backtest, "parity.model_experiment_name")),
        ("model.experiment_id", _get(live, "model.experiment_id"),
         _get(backtest, "parity.model_experiment_id")),
        ("model.recorder_id", _get(live, "model.recorder_id"),
         _get(backtest, "parity.model_recorder_id")),
        ("data.provider_uri", _get(live, "data.qlib_dir"),
         _get(backtest, "data.provider_uri")),
        ("data.region", _get(live, "data.region"), _get(backtest, "data.region")),
        ("data.instruments", _get(live, "data.instruments"),
         _get(backtest, "data.instruments")),
        ("data.benchmark", _get(live, "data.benchmark"),
         _get(backtest, "data.benchmark")),
        ("handler.class", _get(live, "handler.class"),
         _get(backtest, "data.handler.class")),
        ("handler.module", _get(live, "handler.module"),
         _get(backtest, "data.handler.module_path")),
        ("handler.start_time", _get(live, "handler.start_time"),
         _get(backtest, "data.handler.start_time")),
        ("handler.fit_start_time", _get(live, "handler.fit_start_time"),
         _get(backtest, "data.handler.fit_start_time")),
        ("handler.fit_end_time", _get(live, "handler.fit_end_time"),
         _get(backtest, "data.handler.fit_end_time")),
        ("handler.infer_processors", _get(live, "handler.infer_processors"),
         _get(backtest, "data.handler.infer_processors")),
        ("strategy.class", _get(live, "strategy.class"),
         _get(backtest, "strategy.class")),
        ("strategy.topk", _get(live, "strategy.topk"),
         _get(backtest, "strategy.topk")),
        ("strategy.n_drop", _get(live, "strategy.n_drop"),
         _get(backtest, "strategy.n_drop")),
        ("strategy.risk_degree", _get(live, "strategy.risk_degree"),
         _get(backtest, "strategy.kwargs.risk_degree")),
        ("strategy.hold_thresh", _get(live, "strategy.hold_thresh"),
         _get(backtest, "strategy.kwargs.hold_thresh")),
        ("strategy.only_tradable", _get(live, "strategy.only_tradable"),
         _get(backtest, "strategy.kwargs.only_tradable")),
        ("strategy.forbid_all_trade_at_limit",
         _get(live, "strategy.forbid_all_trade_at_limit"),
         _get(backtest, "strategy.kwargs.forbid_all_trade_at_limit")),
        ("backtest.account", _get(live, "monitor.performance_baseline.opening_total_value"),
         _get(backtest, "backtest.account")),
        ("exchange.freq", _get(live, "exchange.freq"),
         _get(backtest, "backtest.exchange_kwargs.freq")),
        ("exchange.deal_price", _get(live, "exchange.deal_price"),
         _get(backtest, "backtest.exchange_kwargs.deal_price")),
        ("exchange.limit_threshold", _get(live, "exchange.limit_threshold"),
         _get(backtest, "backtest.exchange_kwargs.limit_threshold")),
        ("exchange.trade_unit", _get(live, "exchange.trade_unit"),
         _get(backtest, "backtest.exchange_kwargs.trade_unit")),
        ("exchange.open_cost", _get(live, "exchange.open_cost"),
         _get(backtest, "backtest.exchange_kwargs.open_cost")),
        ("exchange.close_cost", _get(live, "exchange.close_cost"),
         _get(backtest, "backtest.exchange_kwargs.close_cost")),
        ("exchange.min_cost", _get(live, "exchange.min_cost"),
         _get(backtest, "backtest.exchange_kwargs.min_cost")),
        ("backtest.open_cost", buy_cost,
         _get(backtest, "backtest.exchange_kwargs.open_cost")),
        ("backtest.close_cost", sell_cost,
         _get(backtest, "backtest.exchange_kwargs.close_cost")),
        ("backtest.min_cost", _get(live, "fees.min_commission"),
         _get(backtest, "backtest.exchange_kwargs.min_cost")),
    ]

    mismatches = [
        f"{path}: live={left!r}, backtest={right!r}"
        for path, left, right in comparisons
        if not _equal(left, right)
    ]
    if mismatches:
        raise ParityError(
            "Live/Backtest parity mismatch:\n- " + "\n- ".join(mismatches)
        )


def validate_configured_backtest(live: dict, project_root: Path) -> Path:
    """Load and validate the Backtest selected by ``live.parity``."""
    relative_path = _get(live, "parity.backtest_config")
    if not isinstance(relative_path, str) or relative_path.startswith("<missing:"):
        raise ParityError("parity.backtest_config is required")
    path = Path(project_root) / relative_path
    if not path.is_file():
        raise ParityError(f"parity backtest config not found: {path}")
    with open(path, encoding="utf-8") as handle:
        backtest = yaml.safe_load(handle)
    if not isinstance(backtest, dict):
        raise ParityError(f"parity backtest config must be a mapping: {path}")
    validate_backtest_parity(live, backtest)
    return path
