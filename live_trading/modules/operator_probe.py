"""Audited, immutable operator batches for the isolated prType=49 probe."""

import dataclasses
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from filelock import FileLock

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
AUTHORIZATION_PROFILE_NAMES = (
    "CLOSE_AUCTION",
    "AFTER_HOURS_FIXED_PRICE",
)
SNAPSHOT_REQUEST_SCHEMA_VERSION = "1.0"
SNAPSHOT_EVIDENCE_PURPOSE = "SHARED_REAL_ACCOUNT_OPERATOR_PREFLIGHT"
SNAPSHOT_PUBLISH_CUTOFF = "14:45:00"
SNAPSHOT_ADVANCE_GATE_NAME = "SNAPSHOT_ORDER_ADVANCE.lock"
SNAPSHOT_MAC_LIFECYCLE_LOCK_NAME = "SNAPSHOT_MAC_LIFECYCLE.lock"
MAIN_STRATEGY_ID = "csi1000_b6m_b2s_postclose_real"
SNAPSHOT_REQUEST_STRATEGIES = {MAIN_STRATEGY_ID, PROBE_STRATEGY_ID}
QMT_PROFILE_BRIDGE_ROOTS = {
    "CLOSE_AUCTION": r"D:\qmt_bridge",
    "AFTER_HOURS_FIXED_PRICE": r"D:\qmt_bridge\pr49_probe",
}


def account_identity_fingerprint(
    account_id: str, account_type: str, account_environment: str,
) -> str:
    """Return a domain-separated fingerprint without exposing the account."""
    material = "\0".join((
        "qlib-account-snapshot-v1",
        str(account_environment), str(account_type), str(account_id),
    )).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def snapshot_artifact_checksum(payload: dict) -> str:
    body = dict(payload)
    body.pop("checksum", None)
    encoded = json.dumps(
        body, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AccountSnapshotRequest:
    request_id: str
    trade_date: str
    collector_execution_profile: str
    collector_bridge_root: str
    requested_for_strategy_id: str
    account_type: str
    account_environment: str
    account_id_masked: str
    account_fingerprint: str
    created_at: str
    publish_cutoff: str = SNAPSHOT_PUBLISH_CUTOFF
    schema_version: str = SNAPSHOT_REQUEST_SCHEMA_VERSION
    evidence_purpose: str = SNAPSHOT_EVIDENCE_PURPOSE

    def to_dict(self) -> dict:
        payload = {"type": "account_snapshot_request", **dataclasses.asdict(self)}
        payload["checksum"] = snapshot_artifact_checksum(payload)
        return payload


def build_account_snapshot_request(
    config: dict,
    *,
    trade_date: str,
    collector_execution_profile: str,
    requested_for_strategy_id: str,
    account_id: str,
    request_id: str | None = None,
    created_at: str | None = None,
) -> AccountSnapshotRequest:
    """Build a read-only observation request, never a trading batch."""
    live = _operator_live_config(config)
    _require_trade_date(trade_date)
    if collector_execution_profile not in QMT_PROFILE_BRIDGE_ROOTS:
        raise SchemaError("unknown snapshot collector execution profile")
    configured_profile = live.get("execution_session")
    if configured_profile != collector_execution_profile:
        raise SchemaError("collector profile does not match collector config")
    if requested_for_strategy_id not in SNAPSHOT_REQUEST_STRATEGIES:
        raise SchemaError("snapshot request strategy is not approved")
    if not isinstance(account_id, str) or not account_id.strip():
        raise SchemaError("snapshot request requires resolved REAL account")
    if os.environ.get("QMT_REAL_ACCOUNT_ID") != account_id:
        raise SchemaError("snapshot request account does not match runtime REAL account")
    request_id = request_id or (
        "snapshot_%s_%s" % (
            trade_date.replace("-", ""), uuid.uuid4().hex,
        )
    )
    if not re.fullmatch(r"snapshot_[0-9]{8}_[a-f0-9]{32}", request_id):
        raise SchemaError("invalid durable snapshot request_id")
    if request_id.split("_")[1] != trade_date.replace("-", ""):
        raise SchemaError("snapshot request_id trade date mismatch")
    timestamp = created_at or _snapshot_publish_now().isoformat()
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SchemaError("snapshot request created_at is invalid") from exc
    if parsed.tzinfo is None:
        raise SchemaError("snapshot request created_at must include timezone")
    if parsed.date().isoformat() != trade_date:
        raise SchemaError("snapshot request created_at date mismatch")
    if parsed.timetz().replace(tzinfo=None) >= datetime_time.fromisoformat(
        SNAPSHOT_PUBLISH_CUTOFF
    ):
        raise SchemaError("snapshot request created_at must precede cutoff")
    account_type = live.get("account_type", "")
    return AccountSnapshotRequest(
        request_id=request_id,
        trade_date=trade_date,
        collector_execution_profile=collector_execution_profile,
        collector_bridge_root=QMT_PROFILE_BRIDGE_ROOTS[
            collector_execution_profile
        ],
        requested_for_strategy_id=requested_for_strategy_id,
        account_type=account_type,
        account_environment="REAL",
        account_id_masked=(
            account_id[:2] + "*" * max(0, len(account_id) - 4) + account_id[-2:]
            if len(account_id) > 4 else "*" * len(account_id)
        ),
        account_fingerprint=account_identity_fingerprint(
            account_id, account_type, "REAL",
        ),
        created_at=timestamp,
    )


def prepare_account_snapshot_request(
    request: AccountSnapshotRequest, recorder, account_id: str,
) -> dict:
    """Persist canonical immutable bytes without exposing them to QMT."""
    payload = request.to_dict()
    if account_identity_fingerprint(
        account_id, request.account_type, request.account_environment,
    ) != request.account_fingerprint:
        raise SchemaError("runtime account changed before snapshot preparation")
    recorder.record_account_snapshot_request(payload, account_id)
    return payload


def publish_account_snapshot_request(
    request_id: str,
    config: dict,
    recorder,
    bridge_root: Path,
    account_id: str,
) -> Path:
    """Expose only the exact canonical bytes of a durable prepared request."""
    live = _operator_live_config(config)
    root = Path(bridge_root).expanduser()
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise SchemaError("snapshot request bridge root must be canonical")
    configured_root = Path(live.get("bridge_root", "")).expanduser()
    if configured_root != root:
        raise SchemaError("snapshot request bridge root does not match config")
    request_root = root / "snapshot_requests"
    authorization_root = _snapshot_authorization_root(
        root, live["execution_session"],
    )
    lifecycle_lock_path = (
        authorization_root / "state" / SNAPSHOT_MAC_LIFECYCLE_LOCK_NAME
    )
    lifecycle_lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lifecycle_lock_path))
    with lock:
        # The importer takes this same Mac lock through DB commit, archive
        # verification, and gate release.  The first durable read must happen
        # here so a terminal importer can never be followed by gate recreation.
        durable = recorder.get_account_snapshot_request(request_id)
        if durable is None:
            raise SchemaError("unknown prepared account snapshot request")
        if durable.get("status") not in {"PREPARED", "REQUESTED"}:
            raise SchemaError("account snapshot request is already terminal")
        try:
            payload = json.loads(durable["request_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise SchemaError(
                "prepared account snapshot artifact is corrupt"
            ) from exc
        if not isinstance(payload, dict) or payload.get(
            "request_id"
        ) != request_id:
            raise SchemaError("prepared account snapshot request_id mismatch")
        if snapshot_artifact_checksum(payload) != durable["request_checksum"]:
            raise SchemaError("prepared account snapshot checksum mismatch")
        if payload.get("checksum") != durable["request_checksum"]:
            raise SchemaError(
                "prepared account snapshot artifact checksum mismatch"
            )
        if payload.get("collector_execution_profile") != live.get(
            "execution_session"
        ):
            raise SchemaError("prepared snapshot collector profile mismatch")
        if payload.get("collector_bridge_root") != QMT_PROFILE_BRIDGE_ROOTS[
            live["execution_session"]
        ]:
            raise SchemaError("prepared snapshot canonical bridge root mismatch")
        if payload.get("trade_date") != date.today().isoformat():
            raise SchemaError(
                "prepared snapshot request trade_date must equal today"
            )
        if payload.get("publish_cutoff") != SNAPSHOT_PUBLISH_CUTOFF:
            raise SchemaError("prepared snapshot publish cutoff mismatch")
        _require_snapshot_publish_window(payload["trade_date"])
        if durable["account_id"] != account_id:
            raise SchemaError("prepared snapshot durable account mismatch")
        if account_identity_fingerprint(
            account_id, payload.get("account_type", ""),
            payload.get("account_environment", ""),
        ) != payload.get("account_fingerprint"):
            raise SchemaError("prepared snapshot runtime account mismatch")
        if payload.get("account_id_masked") != (
            account_id[:2] + "*" * max(0, len(account_id) - 4)
            + account_id[-2:] if len(account_id) > 4
            else "*" * len(account_id)
        ):
            raise SchemaError("prepared snapshot masked account mismatch")
        encoded = durable["request_json"] + "\n"
        with _snapshot_order_advance_gate(
            authorization_root, request_id, live["execution_session"],
            allow_create=durable["status"] == "PREPARED",
        ):
            # REQUESTED recovery reaches this point only while the original
            # matching gate still exists.  Do not recreate protocol paths
            # before that proof.
            inbox = request_root / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            target = inbox / ("request_%s.json" % request_id)
            done = inbox / ("request_%s.done" % request_id)
            recorder.mark_account_snapshot_request_published(
                request_id, durable["request_checksum"],
            )
            if target.exists() or done.exists():
                if (
                    target.is_file()
                    and target.read_text(encoding="utf-8") == encoded
                ):
                    if done.is_file():
                        if done.read_text(
                            encoding="utf-8"
                        ).strip() != payload["checksum"]:
                            raise SchemaError(
                                "snapshot request done checksum conflicts"
                            )
                        return target
                    tmp_done = inbox / (done.name + ".tmp")
                    tmp_done.write_text(
                        payload["checksum"] + "\n", encoding="utf-8",
                    )
                    os.replace(tmp_done, done)
                    return target
                raise SchemaError(
                    "snapshot request artifact conflicts with durable request"
                )
            tmp_json = inbox / (target.name + ".tmp")
            tmp_done = inbox / (done.name + ".tmp")
            if (
                tmp_json.exists()
                and tmp_json.read_text(encoding="utf-8") != encoded
            ):
                raise SchemaError(
                    "snapshot request temporary artifact conflicts"
                )
            tmp_json.write_text(encoded, encoding="utf-8")
            tmp_done.write_text(
                payload["checksum"] + "\n", encoding="utf-8",
            )
            os.replace(tmp_json, target)
            os.replace(tmp_done, done)
            return target


def _snapshot_publish_now() -> datetime:
    return datetime.now().astimezone()


def _snapshot_authorization_root(
    bridge_root: Path, execution_profile: str,
) -> Path:
    return (
        bridge_root.parent
        if execution_profile == "AFTER_HOURS_FIXED_PRICE"
        else bridge_root
    )


def _require_snapshot_publish_window(trade_date: str) -> None:
    now = _snapshot_publish_now()
    if now.date().isoformat() != trade_date:
        raise SchemaError("snapshot publish clock date does not match trade_date")
    if now.timetz().replace(tzinfo=None) >= datetime_time.fromisoformat(
        SNAPSHOT_PUBLISH_CUTOFF
    ):
        raise SchemaError(
            "snapshot publish cutoff %s has passed" % SNAPSHOT_PUBLISH_CUTOFF
        )


@contextmanager
def _snapshot_order_advance_gate(
    authorization_root: Path, request_id: str, execution_profile: str,
    *, allow_create: bool,
):
    gate = authorization_root / "state" / SNAPSHOT_ADVANCE_GATE_NAME
    gate.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "owner": "MAC_SNAPSHOT_PUBLISHER",
        "request_id": request_id,
        "execution_profile": execution_profile,
        "created_at": _snapshot_publish_now().isoformat(),
    }
    def require_matching_existing(missing_message, cause=None):
        try:
            existing = json.loads(gate.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            raise SchemaError(missing_message) from cause
        if not isinstance(existing, dict) or any(
            existing.get(field) != expected for field, expected in (
                ("owner", "MAC_SNAPSHOT_PUBLISHER"),
                ("request_id", request_id),
                ("execution_profile", execution_profile),
            )
        ):
            raise SchemaError(missing_message) from cause

    if not allow_create:
        require_matching_existing(
            "snapshot original lifecycle gate is required for REQUESTED retry"
        )
    else:
        try:
            descriptor = os.open(
                str(gate), os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except OSError as exc:
            require_matching_existing(
                "snapshot order advance gate busy/timeout", exc,
            )
        else:
            try:
                os.write(
                    descriptor,
                    (json.dumps(
                        metadata, ensure_ascii=True, sort_keys=True,
                    ) + "\n").encode("ascii"),
                )
            finally:
                os.close(descriptor)
    yield


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
        main_strategy_id = live.get("main_strategy_id")
        if (
            not isinstance(main_strategy_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", main_strategy_id)
            or main_strategy_id == PROBE_STRATEGY_ID
        ):
            raise SchemaError(
                "operator probe requires a distinct safe main_strategy_id"
            )
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
    details = recorder.get_broker_position_details(
        request.trade_date,
        require_lifecycle_evidence=True,
        evidence_strategy_id=PROBE_STRATEGY_ID,
    )
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
    approved = recorder.get_stock_names()
    if request.stock_code not in approved:
        raise SchemaError(
            f"stock_code is not in the approved trade-date universe: "
            f"{request.stock_code!r}"
        )
    if live.get("kind") == "OPERATOR_PROBE":
        validate_probe_transition(request, recorder)
    else:
        details = recorder.get_broker_position_details(
            broker_trade_date,
            require_lifecycle_evidence=True,
            evidence_strategy_id=live["strategy_id"],
        )
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


def _require_exclusive_main_sell(
    request: OperatorProbeRequest,
    live: dict,
    recorder,
    bridge_root: Path,
) -> bool:
    """Keep a date/profile-wide LIVE marker exclusive to one operator SELL."""
    if request.side != "SELL":
        raise SchemaError("main operator publication only supports SELL")
    strategy_id = live["strategy_id"]
    state = recorder.get_execution_state(strategy_id)
    if state["state"] != "PAUSED":
        raise SchemaError(
            "main operator SELL requires durable PAUSED execution state"
        )

    operator_batch_id = _batch_id(request, live)
    conflicting_batches = [
        batch for batch in recorder.get_active_batches_by_date(
            request.trade_date, strategy_id=strategy_id,
        )
        if batch["mode"] == "LIVE" and batch["batch_id"] != operator_batch_id
    ]
    if conflicting_batches:
        ids = ",".join(batch["batch_id"] for batch in conflicting_batches)
        raise SchemaError(
            f"same-day LIVE batch blocks exclusive main SELL: {ids}"
        )

    prefix = f"{request.trade_date.replace('-', '')}_{strategy_id}_"
    expected_inbox = {
        f"signal_{operator_batch_id}.jsonl",
        f"signal_{operator_batch_id}.done",
    }
    observed_expected_inbox = set()
    artifacts = []
    for directory in ("inbox", "processing", "archive"):
        root = bridge_root / directory
        if not root.is_dir():
            continue
        for path in root.glob(f"signal_{prefix}*"):
            if directory == "inbox" and path.name in expected_inbox:
                observed_expected_inbox.add(path.name)
                continue
            if path.is_file():
                artifacts.append(path.relative_to(bridge_root).as_posix())
    state_root = bridge_root / "state"
    if state_root.is_dir():
        for path in state_root.glob(f"*{prefix}*"):
            if path.is_file():
                artifacts.append(path.relative_to(bridge_root).as_posix())
    if observed_expected_inbox and observed_expected_inbox != expected_inbox:
        artifacts.append("inbox/incomplete exact operator batch pair")
    if artifacts:
        raise SchemaError(
            "same-day QMT artifact blocks exclusive main SELL: "
            + ",".join(sorted(artifacts))
        )
    return observed_expected_inbox == expected_inbox


def _require_no_operator_authorization_marker(
    request: OperatorProbeRequest, authorization_root: Path,
) -> None:
    """Require a fresh post-publication marker in the shared auth domain."""
    marker_names = {
        f"{get_execution_profile(name).authorization_prefix}{request.trade_date}"
        for name in AUTHORIZATION_PROFILE_NAMES
    }
    state_roots = (
        authorization_root / "state",
        authorization_root / "pr49_probe" / "state",
    )
    artifacts = []
    intents = []
    for state_root in state_roots:
        for marker_name in marker_names:
            marker = state_root / marker_name
            if marker.is_file():
                artifacts.append(
                    marker.relative_to(authorization_root).as_posix()
                )
            for intent in state_root.glob(
                f"{marker_name}.intent.*.tmp"
            ):
                if intent.is_file():
                    intents.append(
                        intent.relative_to(authorization_root).as_posix()
                    )
    if intents:
        raise SchemaError(
            "same-day authorization intent blocks operator publication: "
            + ",".join(sorted(intents))
        )
    if artifacts:
        raise SchemaError(
            "same-day authorization marker blocks operator publication: "
            + ",".join(sorted(artifacts))
        )


@contextmanager
def _shared_authorization_gate(publisher):
    try:
        with publisher.authorization_gate():
            yield
    except PublishError as exc:
        raise SchemaError(str(exc)) from exc


def _require_main_strategy_paused(recorder, main_strategy_id: str) -> None:
    state = recorder.get_execution_state(main_strategy_id)
    if state["state"] != "PAUSED":
        raise SchemaError(
            "operator probe requires main strategy durable PAUSED state, "
            f"found {state['state']}"
        )


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
    bridge_root = _validate_publish_root(live, publisher)
    is_probe = live.get("kind") == "OPERATOR_PROBE"
    main_strategy_id = live.get("main_strategy_id") if is_probe else None
    gate = (
        recorder.probe_snapshot_gate()
        if is_probe
        else recorder.operator_publish_gate()
    )
    record_gate = {
        "required_execution_state": "PAUSED",
        "required_execution_state_strategy_id": (
            main_strategy_id if is_probe else live["strategy_id"]
        ),
    }
    if not is_probe:
        record_gate["exclusive_same_day_live"] = True
    authorization_root = publisher.authorization_domain_root
    with (
        recorder.execution_publication_gate(),
        gate,
        _shared_authorization_gate(publisher),
    ):
        if is_probe:
            _require_main_strategy_paused(recorder, main_strategy_id)
        _require_no_operator_authorization_marker(
            request, authorization_root,
        )
        if not is_probe:
            _require_exclusive_main_sell(
                request, live, recorder, bridge_root,
            )
        header = _header(request, live, account_id)

        durable_retry = recorder.get_batch(header.batch_id) is not None
        if durable_retry:
            # A durable plan remains authoritative for immutable bytes and
            # mutable holdings/availability. ACCOUNT evidence is rechecked
            # because a query-failure snapshot cannot authorize real money.
            recorder.get_broker_position_details(
                request.trade_date,
                require_lifecycle_evidence=True,
                evidence_strategy_id=live["strategy_id"],
            )
            order = _make_operator_order(request, live)
            header = _normalized_header(header, order)
            recorder.record_publish_plan(
                header, [order], probe_transition={
                    "side": order.side, "stock_code": order.stock_code,
                } if is_probe else None, **record_gate,
            )
        else:
            header, order = preview_operator_probe(
                request, config, recorder, account_id,
            )
        try:
            already_visible = publisher.ensure_publishable(header, [order])
        except PublishError as exc:
            raise SchemaError(str(exc)) from exc
        except OSError as exc:
            if not is_probe and durable_retry:
                raise SchemaError(
                    "main SELL inbox changed during durable retry; "
                    "refusing to republish"
                ) from exc
            raise
        _require_no_operator_authorization_marker(
            request, authorization_root,
        )
        if not is_probe and durable_retry:
            if already_visible:
                return bridge_root / "inbox" / f"signal_{header.batch_id}.jsonl"
            # A DB-only crash is recoverable only while no trace of a QMT
            # claim exists. Recheck after the byte comparison so a concurrent
            # inbox -> processing/archive transition cannot trigger rewrite.
            exact_pair_visible = _require_exclusive_main_sell(
                request, live, recorder, bridge_root,
            )
            if exact_pair_visible:
                raise SchemaError(
                    "main SELL inbox changed during durable retry; "
                    "refusing to republish"
                )
        # The durable record is the atomic serialization point even when another
        # invocation interleaves after the read-only SMB preflight.
        probe_transition = {
            "side": order.side, "stock_code": order.stock_code,
        } if is_probe else None
        recorder.record_publish_plan(
            header, [order], probe_transition=probe_transition, **record_gate,
        )
        if probe_transition is not None:
            return recorder.publish_recorded_operator_probe(
                header,
                [order],
                probe_transition,
                lambda: publisher.publish(header, [order]),
                required_paused_strategy_id=main_strategy_id,
                revalidate_eligibility=(durable_retry and not already_visible),
            )
        _require_no_operator_authorization_marker(
            request, authorization_root,
        )
        return publisher.publish(
            header,
            [order],
            before_exposure=lambda: _require_no_operator_authorization_marker(
                request, authorization_root,
            ),
        )
