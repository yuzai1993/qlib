"""Audited, immutable operator batches for the isolated prType=49 probe."""

import dataclasses
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from live_trading.modules.code_map import qmt_to_qlib
from live_trading.modules.execution_profile import get_execution_profile
from live_trading.modules.signal_publisher import PublishError
from live_trading.modules.signal_schema import (
    BatchHeader,
    SchemaError,
    SignalOrder,
    compute_checksum,
    make_client_order_id,
    validate_batch,
)


OPERATOR_BATCH_SEQUENCE = 900
OPERATOR_ORDER_SEQUENCE = 1
ONE_LOT = 100
BUY_TARGET_VALUE = 1_000_000.0
PROBE_STRATEGY_ID = "csi1000_pr49_one_lot_probe"


@dataclass(frozen=True)
class OperatorProbeRequest:
    """One intentional one-lot request, independent of strategy predictions."""

    config_id: str
    trade_date: str
    stock_code: str
    side: str
    quantity: int
    reason: str


def _require_trade_date(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SchemaError(f"trade_date must be YYYY-MM-DD: {value!r}")
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"trade_date must be YYYY-MM-DD: {value!r}") from exc


def _probe_live_config(config: dict) -> dict:
    live = config.get("live", {})
    if live.get("kind") != "OPERATOR_PROBE":
        raise SchemaError("operator probe requires live.kind OPERATOR_PROBE")
    if live.get("strategy_id") != PROBE_STRATEGY_ID:
        raise SchemaError(
            f"operator probe requires strategy_id {PROBE_STRATEGY_ID}"
        )
    bridge_root = live.get("bridge_root")
    if (
        not isinstance(bridge_root, str)
        or not bridge_root.rstrip("/").endswith("/pr49_probe")
    ):
        raise SchemaError("operator probe requires an isolated /pr49_probe root")
    if live.get("broker_environment") != "REAL":
        raise SchemaError("operator probe requires REAL broker environment")
    if live.get("allow_real_money") is not True:
        raise SchemaError("operator probe requires allow_real_money=true")
    if live.get("default_mode") != "LIVE":
        raise SchemaError("operator probe requires default_mode=LIVE")
    try:
        profile = get_execution_profile(live["execution_session"])
    except (KeyError, ValueError) as exc:
        raise SchemaError("operator probe execution profile is invalid") from exc
    if profile.name != "AFTER_HOURS_FIXED_PRICE":
        raise SchemaError("operator probe requires AFTER_HOURS_FIXED_PRICE")
    return live


def _validate_request(request: OperatorProbeRequest, config: dict) -> dict:
    if not isinstance(request, OperatorProbeRequest):
        raise TypeError("request must be an OperatorProbeRequest")
    configured_id = config.get("_config_id")
    if configured_id and request.config_id != configured_id:
        raise SchemaError(
            f"request config_id {request.config_id!r} does not match {configured_id!r}"
        )
    _require_trade_date(request.trade_date)
    if request.side not in {"BUY", "SELL"}:
        raise SchemaError(f"invalid side: {request.side!r}")
    if (
        isinstance(request.quantity, bool)
        or not isinstance(request.quantity, int)
        or request.quantity != ONE_LOT
    ):
        raise SchemaError("operator probe quantity must be exactly 100 shares")
    if not isinstance(request.reason, str) or not request.reason.strip():
        raise SchemaError("operator probe reason must be a nonempty string")
    try:
        qmt_to_qlib(request.stock_code)
    except ValueError as exc:
        raise SchemaError(
            f"operator probe stock_code is unknown or invalid: {request.stock_code!r}"
        ) from exc
    return _probe_live_config(config)


def _batch_id(request: OperatorProbeRequest, live: dict) -> str:
    return (
        f"{request.trade_date.replace('-', '')}_{live['strategy_id']}_"
        f"{OPERATOR_BATCH_SEQUENCE:03d}"
    )


def _header(request: OperatorProbeRequest, live: dict, account_id: str) -> BatchHeader:
    """Build deterministic bytes so retry verification remains exact."""
    return BatchHeader(
        batch_id=_batch_id(request, live),
        strategy_id=live["strategy_id"],
        trade_date=request.trade_date,
        signal_date=request.trade_date,
        account_id=account_id,
        account_type=live.get("account_type", "STOCK"),
        account_environment="REAL",
        mode="LIVE",
        created_at=f"{request.trade_date}T00:00:00+08:00",
        order_count=1,
        checksum="",
    )


def _make_operator_order(
    request: OperatorProbeRequest, live: dict,
) -> SignalOrder:
    """Render immutable payload fields that never depend on live eligibility."""
    if request.side == "SELL":
        quantity = ONE_LOT
        target_value = 0.0
        priority = 10
    else:
        # The v2 bridge deliberately sizes BUYs from target_value.  Its fixed
        # one-lot ceiling turns this deliberately high target into one lot.
        quantity = 0
        target_value = BUY_TARGET_VALUE
        priority = 20
    profile = get_execution_profile(live["execution_session"])
    return SignalOrder(
        batch_id=_batch_id(request, live),
        client_order_id=make_client_order_id(
            request.trade_date,
            OPERATOR_BATCH_SEQUENCE,
            OPERATOR_ORDER_SEQUENCE,
            request.side,
        ),
        stock_code=request.stock_code,
        side=request.side,
        quantity=quantity,
        target_value=target_value,
        price_type=profile.signal_price_type,
        limit_price=0.0,
        priority=priority,
        instrument_qlib=qmt_to_qlib(request.stock_code),
        reason=request.reason,
    )


def _normalized_header(header: BatchHeader, order: SignalOrder) -> BatchHeader:
    normalized = dataclasses.replace(
        header,
        checksum=compute_checksum([order.to_json_line()]),
    )
    validate_batch(normalized, [order])
    return normalized


def build_operator_order(
    request: OperatorProbeRequest,
    config: dict,
    recorder,
    broker_trade_date: str,
) -> SignalOrder:
    """Validate one probe request against the durable ledger and QMT snapshot."""
    live = _validate_request(request, config)
    if broker_trade_date != request.trade_date:
        raise SchemaError(
            "broker position snapshot must be from the operator trade_date"
        )
    details = recorder.get_broker_position_details(broker_trade_date)
    positions = recorder.get_positions()
    held = positions.get(request.stock_code, {}).get("shares", 0)

    if request.side == "SELL":
        if held < ONE_LOT:
            raise SchemaError("SELL requires at least one lot in the live ledger")
        broker = details.get(request.stock_code)
        available = None if broker is None else broker.get("can_use_volume")
        if available is None or available < ONE_LOT:
            raise SchemaError(
                "SELL requires at least one lot available in latest broker snapshot"
            )
    else:
        if held > 0:
            raise SchemaError("BUY rejected: stock is already held in live ledger")
    return _make_operator_order(request, live)


def preview_operator_probe(
    request: OperatorProbeRequest, config: dict, recorder, account_id: str,
) -> tuple[BatchHeader, SignalOrder]:
    """Produce validated immutable payload data without publishing it."""
    live = _validate_request(request, config)
    _validate_real_account(live, account_id)
    order = build_operator_order(request, config, recorder, request.trade_date)
    header = _header(request, live, account_id)
    return _normalized_header(header, order), order


def _validate_real_account(live: dict, account_id: str) -> None:
    if not isinstance(account_id, str) or not account_id.strip():
        raise SchemaError("resolved REAL account_id is required")
    configured_account = live.get("account_id")
    if configured_account and account_id != configured_account:
        raise SchemaError("resolved account_id does not match probe config")
    real_account = os.environ.get("QMT_REAL_ACCOUNT_ID")
    if real_account != account_id:
        raise SchemaError("resolved account_id does not match QMT_REAL_ACCOUNT_ID")


def publish_operator_probe(
    request: OperatorProbeRequest, config: dict, recorder, publisher, account_id: str,
) -> Path:
    """Durably record one exact plan before making its signal files visible."""
    if os.environ.get("LIVE_TRADING_CONFIRM") != "YES":
        raise SchemaError("refusing operator publish without LIVE_TRADING_CONFIRM=YES")
    live = _validate_request(request, config)
    _validate_real_account(live, account_id)
    header = _header(request, live, account_id)

    if recorder.get_batch(header.batch_id) is not None:
        # A durable plan is authoritative after a crash between its DB commit
        # and SMB publication.  Rebuild only immutable request/config fields;
        # do not re-check mutable holdings or broker availability on recovery.
        order = _make_operator_order(request, live)
        header = _normalized_header(header, order)
        recorder.record_publish_plan(header, [order])
    else:
        header, order = preview_operator_probe(
            request, config, recorder, account_id,
        )
    try:
        publisher.ensure_publishable(header, [order])
    except PublishError as exc:
        raise SchemaError(str(exc)) from exc
    if recorder.get_batch(header.batch_id) is None:
        recorder.record_publish_plan(header, [order])
    return publisher.publish(header, [order])
