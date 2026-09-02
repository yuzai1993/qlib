"""Fail-closed validation of Live Trading against its designated Backtest."""

from __future__ import annotations

import math
from pathlib import Path

import yaml

from live_trading.modules.execution_profile import get_execution_profile


class ParityError(ValueError):
    """A decision-critical Live/Backtest setting has drifted."""


_LADDER_CLASS = "CohortLadderStrategy"
_TOPK_DROPOUT_CLASS = "TopkDropoutStrategy"
_LIVE_ONLY_DEVIATIONS = (
    "netting",
    "absorb_broker_excess",
    "no_buyable_substitution",
)
_UNIVERSE_KEYS = (
    "st_daily",
    "min_amount",
    "min_listing_days",
    "min_recent_trading_days",
    "pool",
)
# 通道与成交价是绑定关系：盘后固定价和收盘集合竞价都以收盘价撮合。
_SESSION_DEAL_PRICE = {
    "AFTER_HOURS_FIXED_PRICE": "close",
    "CLOSE_AUCTION": "close",
}


def _get(mapping: dict, path: str):
    current = mapping
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return f"<missing:{path}>"
        current = current[key]
    return current


def _optional(mapping: dict, path: str):
    value = _get(mapping, path)
    return None if isinstance(value, str) and value.startswith("<missing:") else value


def _equal(left, right) -> bool:
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)
    return left == right


def _model_members(config: dict, path: str):
    """把成员列表规整成排序后的 (model_path, sha256) 序列；缺该段返回 None。"""
    members = _optional(config, path)
    if members is None:
        return None
    if not isinstance(members, list) or not members:
        raise ParityError(f"{path} must be a non-empty list")
    normalized = []
    for member in members:
        if not isinstance(member, dict):
            raise ParityError(f"{path} entries must be mappings")
        for key in ("model_path", "sha256"):
            if not member.get(key):
                raise ParityError(f"{path} entries require a non-empty {key}")
        normalized.append((str(member["model_path"]), str(member["sha256"])))
    # 顺序无关、集合必须相等：种子的书写顺序不承载语义。
    return sorted(normalized)


def _model_comparisons(live: dict, backtest: dict) -> list:
    comparisons = [
        ("model.experiment_name", _get(live, "model.experiment_name"),
         _get(backtest, "parity.model_experiment_name")),
    ]
    live_members = _model_members(live, "model.members")
    backtest_members = _model_members(backtest, "parity.model_members")
    if live_members is None and backtest_members is None:
        return comparisons + [
            ("model.experiment_id", _get(live, "model.experiment_id"),
             _get(backtest, "parity.model_experiment_id")),
            ("model.recorder_id", _get(live, "model.recorder_id"),
             _get(backtest, "parity.model_recorder_id")),
            ("model.model_path", _get(live, "model.model_path"),
             _get(backtest, "parity.model_path")),
            ("model.sha256", _get(live, "model.sha256"),
             _get(backtest, "parity.model_sha256")),
        ]
    return comparisons + [
        ("model.member_count",
         -1 if live_members is None else len(live_members),
         -1 if backtest_members is None else len(backtest_members)),
        ("model.members", live_members, backtest_members),
    ]


def _strategy_comparisons(live: dict, backtest: dict) -> list:
    live_class = _get(live, "strategy.class")
    backtest_class = _get(backtest, "strategy.class")
    shared = [
        ("strategy.class", live_class, backtest_class),
        ("strategy.topk", _get(live, "strategy.topk"),
         _get(backtest, "strategy.topk")),
        ("strategy.risk_degree", _get(live, "strategy.risk_degree"),
         _get(backtest, "strategy.kwargs.risk_degree")),
        ("strategy.only_tradable", _get(live, "strategy.only_tradable"),
         _get(backtest, "strategy.kwargs.only_tradable")),
        ("strategy.forbid_all_trade_at_limit",
         _get(live, "strategy.forbid_all_trade_at_limit"),
         _get(backtest, "strategy.kwargs.forbid_all_trade_at_limit")),
    ]
    if live_class != backtest_class:
        # 类都不一样，再比只有一侧存在的字段只会刷出噪音掩盖真正的错。
        return shared
    if live_class == _LADDER_CLASS:
        return shared + [
            ("strategy.horizon", _get(live, "strategy.horizon"),
             _get(backtest, "strategy.horizon")),
        ]
    if live_class == _TOPK_DROPOUT_CLASS:
        return shared + [
            ("strategy.n_drop", _get(live, "strategy.n_drop"),
             _get(backtest, "strategy.n_drop")),
            ("strategy.hold_thresh", _get(live, "strategy.hold_thresh"),
             _get(backtest, "strategy.kwargs.hold_thresh")),
            ("strategy.initial_buy_count",
             _optional(live, "strategy.initial_buy_count"),
             _optional(backtest, "strategy.kwargs.initial_buy_count")),
        ]
    raise ParityError(f"unknown strategy class for parity: {live_class!r}")


def _universe_comparisons(live: dict, backtest: dict) -> list:
    live_section = _optional(live, "universe_filter")
    backtest_section = _optional(backtest, "universe_filter")
    if live_section is None and backtest_section is None:
        return []  # 存量 CSI1000 / CSI300 两侧都没有这一段
    if not isinstance(live_section, dict) or not isinstance(
        backtest_section, dict
    ):
        raise ParityError(
            "universe_filter must be present on both sides once either "
            f"declares it: live={type(live_section).__name__}, "
            f"backtest={type(backtest_section).__name__}"
        )
    return [
        (f"universe_filter.{key}",
         _get(live, f"universe_filter.{key}"),
         _get(backtest, f"universe_filter.{key}"))
        for key in _UNIVERSE_KEYS
    ]


def _channel_comparisons(live: dict, backtest: dict) -> list:
    session = _optional(live, "live.execution_session")
    if session is None:
        return [("live.execution_session", None,
                 _optional(backtest, "parity.execution_session"))]
    comparisons = [
        ("live.execution_session", session,
         _get(backtest, "parity.execution_session")),
        # signal_price_type 不在 live 配置里，必须由 profile 推出来，
        # 否则改了 execution_session 忘改 parity 配置就查不出来。
        ("live.signal_price_type",
         get_execution_profile(session).signal_price_type,
         _get(backtest, "parity.signal_price_type")),
    ]
    bound = _SESSION_DEAL_PRICE.get(session)
    if bound is not None:
        comparisons.append((
            f"exchange.deal_price bound to {session}", bound,
            _get(backtest, "backtest.exchange_kwargs.deal_price"),
        ))
    return comparisons


def _deviation_comparisons(live: dict, backtest: dict) -> list:
    if _get(live, "strategy.class") != _LADDER_CLASS:
        return []  # 这三项是真阶梯独有的偏离，存量 TopkDropout 两侧都不该有
    # 两侧同时缺也算漂移：`<missing:strategy.netting>` 与
    # `<missing:parity.netting>` 是不同字符串，比较自然不等，于是 fail-closed。
    return [
        (f"live_only deviation {name}",
         _get(live, f"strategy.{name}"), _get(backtest, f"parity.{name}"))
        for name in _LIVE_ONLY_DEVIATIONS
    ]


def _common_comparisons(
    live: dict,
    backtest: dict,
    opening_account,
    buy_cost: float,
    sell_cost: float,
) -> list:
    return [
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
        # start_time 是研究回测的加载区间。实盘每晚只装信号日前
        # inference_lookback_days，两边不必相等。
        ("handler.fit_start_time", _get(live, "handler.fit_start_time"),
         _get(backtest, "data.handler.fit_start_time")),
        ("handler.fit_end_time", _get(live, "handler.fit_end_time"),
         _get(backtest, "data.handler.fit_end_time")),
        ("handler.infer_processors", _get(live, "handler.infer_processors"),
         _get(backtest, "data.handler.infer_processors")),
        ("handler.feature_groups", _optional(live, "handler.feature_groups"),
         _optional(backtest, "data.handler.feature_groups")),
        ("handler.filter_pipe", _optional(live, "handler.filter_pipe"),
         _optional(backtest, "data.handler.instruments.filter_pipe")),
        ("backtest.account", opening_account,
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
        ("live.close_auction_price_type",
         _optional(live, "live.close_auction_price_type"),
         _optional(backtest, "parity.close_auction_price_type")),
    ]


def validate_backtest_parity(live: dict, backtest: dict) -> None:
    """Raise with all mismatches between Live and its parity Backtest."""
    live_fees = live.get("fees", {})
    buy_cost = (
        float(live_fees.get("commission_rate", 0.0))
        + float(live_fees.get("transfer_fee_rate", 0.0))
    )
    sell_cost = buy_cost + float(live_fees.get("stamp_duty_rate", 0.0))
    opening_cash = _optional(live, "account.opening_cash")
    if opening_cash is None:
        opening_account = _get(
            live, "monitor.performance_baseline.opening_total_value"
        )
    else:
        opening_account = float(opening_cash) + float(
            _optional(live, "account.opening_value_adjustment") or 0.0
        )

    comparisons = _common_comparisons(
        live, backtest, opening_account, buy_cost, sell_cost
    )
    for group in (
        _model_comparisons,
        _strategy_comparisons,
        _universe_comparisons,
        _channel_comparisons,
        _deviation_comparisons,
    ):
        comparisons.extend(group(live, backtest))

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
