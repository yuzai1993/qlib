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
PROBE_ACTIVE_STATES = {"BUY_PLANNED", "BUY_FILLED", "SELL_PLANNED"}


@dataclass(frozen=True)
class OperatorProbeRequest:
    """One intentional one-lot request, independent of strategy predictions."""

    config_id: str
    trade_date: str
    stock_code: str
    side: str
    quantity: int
    reason: str
    eligibility_confirmed: bool = False


def _require_trade_date(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SchemaError(f"trade_date must be YYYY-MM-DD: {value!r}")
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"trade_date must be YYYY-MM-DD: {value!r}") from exc


def _operator_live_config(config: dict) -> dict:
    live = config.get("live", {})
    if live.get("broker_environment") != "REAL":
        raise SchemaError("operator tool requires REAL broker environment")
    if live.get("allow_real_money") is not True:
        raise SchemaError("operator tool requires allow_real_money=true")
    if live.get("default_mode") != "LIVE":
        raise SchemaError("operator tool requires default_mode=LIVE")
    try:
        profile = get_execution_profile(live["execution_session"])
    except (KeyError, ValueError) as exc:
        raise SchemaError("operator execution profile is invalid") from exc
    kind = live.get("kind", "STRATEGY")
    if kind == "OPERATOR_PROBE":
        if live.get("strategy_id") != PROBE_STRATEGY_ID:
            raise SchemaError(
                f"operator probe requires strategy_id {PROBE_STRATEGY_ID}"
            )
        if profile.name != "AFTER_HOURS_FIXED_PRICE":
            raise SchemaError("operator probe requires AFTER_HOURS_FIXED_PRICE")
        bridge_root = live.get("bridge_root")
        if (
            not isinstance(bridge_root, str)
            or not bridge_root.rstrip("/").endswith("/pr49_probe")
        ):
            raise SchemaError("operator probe requires an isolated /pr49_probe root")
    elif kind == "STRATEGY":
        if profile.name != "CLOSE_AUCTION":
            raise SchemaError("main operator tool requires CLOSE_AUCTION")
    else:
        raise SchemaError("operator tool requires STRATEGY or OPERATOR_PROBE")
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
    return _operator_live_config(config)


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
        max_quantity=ONE_LOT if request.side == "BUY" else 0,
    )


def _normalized_header(header: BatchHeader, order: SignalOrder) -> BatchHeader:
    normalized = dataclasses.replace(
        header,
        checksum=compute_checksum([order.to_json_line()]),
    )
    validate_batch(normalized, [order])
    return normalized


def _qlib_trade_dates(start_date: str, end_date: str) -> list[str]:
    """Load the authoritative local Qlib A-share calendar fail-closed."""
    try:
        import qlib
        from qlib.data import D

        provider_uri = Path(os.environ.get(
            "QLIB_CN_DATA_DIR", "~/.qlib/qlib_data/cn_data",
        )).expanduser()
        if not provider_uri.is_dir():
            raise SchemaError(
                f"Qlib calendar data directory is missing: {provider_uri}"
            )
        qlib.init(
            provider_uri=str(provider_uri), region="cn", kernels=1,
        )
        values = D.calendar(start_time=start_date, end_time=end_date)
    except SchemaError:
        raise
    except Exception as exc:
        raise SchemaError(f"Qlib trade calendar unavailable: {exc}") from exc
    return [str(value)[:10] for value in values]


def _require_later_qlib_trade_date(
    buy_trade_date: str, sell_trade_date: str,
) -> None:
    if sell_trade_date <= buy_trade_date:
        raise SchemaError("SELL requires a later Qlib trade date than BUY")
    dates = _qlib_trade_dates(buy_trade_date, sell_trade_date)
    if buy_trade_date not in dates or sell_trade_date not in dates:
        raise SchemaError("BUY and SELL must both be Qlib trade dates")
    if dates.index(sell_trade_date) <= dates.index(buy_trade_date):
        raise SchemaError("SELL requires a later Qlib trade date than BUY")


def validate_probe_transition(
    request: OperatorProbeRequest, recorder,
) -> None:
    """Validate one probe transition from imported fills and broker evidence."""
    if not isinstance(request, OperatorProbeRequest):
        raise TypeError("request must be an OperatorProbeRequest")
    _require_trade_date(request.trade_date)
    details = recorder.get_broker_position_details(request.trade_date)
    positions = recorder.get_positions()
    held = positions.get(request.stock_code, {}).get("shares", 0)
    broker = details.get(request.stock_code)

    lifecycle = recorder.get_operator_probe_lifecycle(PROBE_STRATEGY_ID)
    if request.side == "BUY":
        if request.eligibility_confirmed is not True:
            raise SchemaError("BUY requires explicit --eligibility-confirmed")
        unresolved = recorder.get_unreconciled_active_live_batches_before(
            request.trade_date, strategy_id=PROBE_STRATEGY_ID,
        )
        if unresolved:
            raise SchemaError(
                "unresolved prior probe batch blocks a new transition: "
                f"{unresolved[0]['batch_id']}"
            )
        if lifecycle is not None and lifecycle["state"] in PROBE_ACTIVE_STATES:
            raise SchemaError(
                f"BUY rejected: probe lifecycle is {lifecycle['state']}"
            )
        if held > 0:
            raise SchemaError("BUY rejected: stock is already held in live ledger")
        if broker is not None and broker.get("shares", 0) > 0:
            raise SchemaError("BUY rejected: stock is held in latest broker snapshot")
        return

    if request.side != "SELL":
        raise SchemaError(f"invalid side: {request.side!r}")
    if lifecycle is None:
        if held < ONE_LOT:
            raise SchemaError("SELL requires at least one lot in the live ledger")
        available = None if broker is None else broker.get("can_use_volume")
        if available is None or available < ONE_LOT:
            raise SchemaError(
                "SELL requires at least one lot available in latest broker snapshot"
            )
        raise SchemaError("SELL requires an actual applied BUY probe holding")
    if lifecycle["stock_code"] != request.stock_code:
        raise SchemaError("SELL symbol must match the BUY lifecycle symbol")
    applied_buy = recorder.get_operator_probe_applied_quantity(
        lifecycle["buy_batch_id"], "BUY", lifecycle["stock_code"],
    )
    if applied_buy != ONE_LOT:
        raise SchemaError(
            "SELL requires actual applied BUY quantity exactly 100 shares"
        )
    if lifecycle["state"] != "BUY_FILLED":
        raise SchemaError(
            f"SELL requires BUY_FILLED lifecycle, got {lifecycle['state']}"
        )
    _require_later_qlib_trade_date(
        lifecycle["buy_trade_date"], request.trade_date,
    )
    unresolved = recorder.get_unreconciled_active_live_batches_before(
        request.trade_date, strategy_id=PROBE_STRATEGY_ID,
    )
    if unresolved:
        raise SchemaError(
            "unresolved prior probe batch blocks a new transition: "
            f"{unresolved[0]['batch_id']}"
        )
    if held < ONE_LOT:
        raise SchemaError("SELL requires at least one lot in the live ledger")
    available = None if broker is None else broker.get("can_use_volume")
    if available is None or available < ONE_LOT:
        raise SchemaError(
            "SELL requires at least one lot available in latest broker snapshot"
        )


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
    approved = recorder.get_stock_names()
    if request.stock_code not in approved:
        raise SchemaError(
            f"stock_code is not in the approved trade-date universe: "
            f"{request.stock_code!r}"
        )
    if live.get("kind") == "OPERATOR_PROBE":
        validate_probe_transition(request, recorder)
    else:
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
            broker = details.get(request.stock_code)
            if broker is not None and broker.get("shares", 0) > 0:
                raise SchemaError(
                    "BUY rejected: stock is held in latest broker snapshot"
                )
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


def _validate_publish_root(live: dict, publisher) -> Path:
    """Bind REAL publication to one canonical, non-symlinked bridge root."""
    configured = live.get("bridge_root")
    if not isinstance(configured, str):
        raise SchemaError("configured bridge root is required")
    configured_path = Path(configured).expanduser()
    if not configured_path.is_absolute():
        raise SchemaError("configured bridge root must be absolute")
    try:
        canonical = configured_path.resolve(strict=True)
    except OSError as exc:
        raise SchemaError("configured bridge root is missing") from exc
    if configured_path != canonical:
        raise SchemaError("configured bridge root must not use a symlink")
    if not canonical.is_dir() or not os.access(str(canonical), os.W_OK | os.X_OK):
        raise SchemaError("configured bridge root is not writable")
    try:
        publisher_path = Path(publisher.bridge_root).expanduser()
        publisher_canonical = publisher_path.resolve(strict=True)
    except (AttributeError, OSError) as exc:
        raise SchemaError("publisher root is missing") from exc
    if publisher_path != publisher_canonical:
        raise SchemaError("publisher root must not use a symlink")
    if publisher_canonical != canonical:
        raise SchemaError("publisher root does not match configured bridge root")
    return canonical


def publish_operator_probe(
    request: OperatorProbeRequest, config: dict, recorder, publisher, account_id: str,
) -> Path:
    """Durably record one exact plan before making its signal files visible."""
    if os.environ.get("LIVE_TRADING_CONFIRM") != "YES":
        raise SchemaError("refusing operator publish without LIVE_TRADING_CONFIRM=YES")
    live = _validate_request(request, config)
    if (
        live.get("kind") == "OPERATOR_PROBE"
        and request.side == "BUY"
        and request.eligibility_confirmed is not True
    ):
        raise SchemaError("BUY requires explicit --eligibility-confirmed")
    _validate_real_account(live, account_id)
    _validate_publish_root(live, publisher)
    header = _header(request, live, account_id)

    if recorder.get_batch(header.batch_id) is not None:
        # A durable plan is authoritative after a crash between its DB commit
        # and SMB publication.  Rebuild only immutable request/config fields;
        # do not re-check mutable holdings or broker availability on recovery.
        order = _make_operator_order(request, live)
        header = _normalized_header(header, order)
        recorder.record_publish_plan(
            header, [order], probe_transition={
                "side": order.side, "stock_code": order.stock_code,
            } if live.get("kind") == "OPERATOR_PROBE" else None,
        )
    else:
        header, order = preview_operator_probe(
            request, config, recorder, account_id,
        )
    try:
        publisher.ensure_publishable(header, [order])
    except PublishError as exc:
        raise SchemaError(str(exc)) from exc
    # The durable record is the atomic serialization point even when another
    # invocation interleaves after the read-only SMB preflight.
    recorder.record_publish_plan(
        header, [order], probe_transition={
            "side": order.side, "stock_code": order.stock_code,
        } if live.get("kind") == "OPERATOR_PROBE" else None,
    )
    return publisher.publish(header, [order])
