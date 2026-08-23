#coding:gbk
# qmt_signal_bridge -- QMT built-in strategy that consumes qlib signal batches.
#
# Protocol: docs/superpowers/specs/2026-07-11-qmt-live-signal-bridge-design.md
# Runtime:  QMT built-in Python 3.6. ASCII only (file declares gbk; ascii is a
#           strict subset, so it is valid in both encodings).
#
# Flow per batch (selected by EXECUTION_PROFILE):
#   inbox/signal_{batch}.jsonl + .done
#     -> claim to processing/ (skip if expired / duplicate / bad checksum)
#     -> CLOSE_AUCTION: 14:57 / prType=11 / explicit daily side limits
#     -> AFTER_HOURS_FIXED_PRICE: 15:05 / prType=49 / price=0
#     -> poll order status by remark (client_order_id)
#     -> profile-specific cancel, finalize, and account snapshot times
#
# LIVE double switch: header.mode == "LIVE" AND the selected profile's marker
# exists. If both profiles' markers exist, both instances fail closed.

import json
import math
import os
import re
import time
import datetime
import traceback

# ======================= user settings =======================

EXECUTION_PROFILE = "CLOSE_AUCTION"
# A separately compiled AFTER_HOURS_FIXED_PRICE instance must swap these two
# roots. Keeping both explicit lets each instance inspect the other's marker.
BRIDGE_ROOT = r"D:\qmt_bridge"
OTHER_BRIDGE_ROOT = r"D:\qmt_bridge\pr49_probe"
ACCOUNT_ID = ""            # QMT-local account id; must match header if set
ACCOUNT_TYPE = "STOCK"
STRATEGY_NAME = "qlib_bridge"
SCHEMA_VERSION = "2.0"
SOURCE_VERSION = "2026-08-08-task9a-snapshot-gate-replay-fix3"
ACCOUNT_ENVIRONMENT = "SIMULATION"
# REAL deployments must set all four values deliberately in the QMT-local
# copy. Keeping the repository default False prevents an accidental cutover.
ALLOW_REAL_MONEY = False
REAL_EXPECTED_INITIAL_CASH = 1000000.0
REAL_INITIAL_CASH_TOLERANCE = 100.0
REAL_REQUIRE_EMPTY_POSITIONS = True
LIMIT_PRICE_TYPE = 11
# Safety rollout gate. 100 means one-lot execution. Keep it at 100 until the
# explicitly selected account environment has passed one-lot acceptance.
MAX_ORDER_QUANTITY = 100
MAX_ORDERS_PER_BATCH = 40
MAX_BATCH_BYTES = 256 * 1024

POLL_SECONDS = 3           # min interval between polls (handlebar is tick-driven)
SNAPSHOT_OBSERVER_START = "09:35:00"
SNAPSHOT_PUBLISH_CUTOFF = "14:45:00"
SNAPSHOT_ADVANCE_GATE_NAME = "SNAPSHOT_ORDER_ADVANCE.lock"
SELL_WAIT_TIMEOUT_SEC = 0
TRADE_START = "14:57:05"
CANCEL_AT = "15:00:05"
FINALIZE_AT = "15:00:30"
SNAPSHOT_REFRESH_AT = "15:01:00"

_EXECUTION_PROFILES = {
    "CLOSE_AUCTION": {
        "signal_price_type": "CLOSE_AUCTION_LIMIT",
        "qmt_price_type": 11,
        "submit_after": "14:57:05",
        "cancel_at": "15:00:05",
        "finalize_at": "15:00:30",
        "snapshot_after": "15:01:00",
        "authorization_prefix": "LIVE_OK_",
        "other_authorization_prefix": "PR49_LIVE_OK_",
        "sell_wait_seconds": 0,
        "timer_start": "14:56:55",
    },
    "AFTER_HOURS_FIXED_PRICE": {
        "signal_price_type": "AFTER_HOURS_CLOSE",
        "qmt_price_type": 49,
        "submit_after": "15:05:00",
        "cancel_at": "15:28:00",
        "finalize_at": "15:30:00",
        "snapshot_after": "15:31:00",
        "authorization_prefix": "PR49_LIVE_OK_",
        "other_authorization_prefix": "LIVE_OK_",
        "sell_wait_seconds": 4 * 60,
        "timer_start": "15:04:55",
    },
}

# BUY-side fee estimate used only for local cash reservation.
COMMISSION_RATE = 0.00020
MIN_COMMISSION = 5.0
TRANSFER_FEE_RATE = 0.00001

# order status values (see design doc / QMT docs)
STATUS_PART_CANCEL = 53
STATUS_CANCELED = 54
STATUS_PART_SUCC = 55
STATUS_SUCCEEDED = 56
STATUS_JUNK = 57
TERMINAL_ORDER_STATUS = (STATUS_PART_CANCEL, STATUS_CANCELED,
                         STATUS_SUCCEEDED, STATUS_JUNK)

# ======================= state =======================


class Batch(object):
    def __init__(self, header, orders):
        self.header = header
        self.orders = orders          # list of dicts, sells first (priority asc)
        self.phase = "SELL"           # SELL -> BUY -> DONE
        self.phase_started = time.time()
        self.trading_started = False  # phase timer resets on first trade pass
        self.execution_authorized = False  # frozen first-pass LIVE eligibility
        self.execution_live = False   # true only after both LIVE safety gates
        self.dual_authorization_blocked = False
        self.submitted = {}           # client_order_id -> True
        self.fills = {}               # client_order_id -> fill dict (latest)
        self.remaining_cash = None    # one broker cash snapshot for BUY phase
        self.order_evidence = {}      # persisted query/API/callback audit state
        self.processing_jsonl = None
        self.processing_done = None
        self.finalized = False
        self.broker_authorized = (
            header.get("schema_version") == SCHEMA_VERSION
            and header.get("account_environment") == ACCOUNT_ENVIRONMENT
        )

    def batch_id(self):
        return self.header["batch_id"]


class State(object):
    def __init__(self):
        self.last_poll = 0.0
        self.batch = None             # current Batch or None
        self.processed = set()        # batch ids finished (persisted)
        self.loaded = False
        self.trading_enabled = False
        self.timer_registered = False
        self.snapshot_timer_registered = False
        self.snapshot_observer_loaded = False
        self.snapshot_observer_enabled = False
        self.snapshot_residue_signature = None
        self.log_write_failure = None
        self.snapshot_accounts = {}


g = State()

# ======================= small utils =======================


def _log(msg, account_ids=None):
    message = _redact_text(msg, account_ids=account_ids)
    print("[qlib_bridge] " + message)
    try:
        _append_log_line(
            "qmt_bridge_%s.log" % _today(),
            "%s [qlib_bridge] %s" % (
                datetime.datetime.now().isoformat(), message),
        )
    except Exception:
        pass


def _append_log_line(name, line):
    log_dir = _path("logs")
    if not os.path.isdir(log_dir):
        os.makedirs(log_dir)
    with open(os.path.join(log_dir, name), "a", encoding="utf-8") as f:
        f.write(str(line) + "\n")
        f.flush()


_SECRET_KEY_PATTERN = (
    r"(?:[a-z0-9_-]*(?:credential|password|passwd|secret|token|sendkey|"
    r"api[_-]?key|apikey|cookie|authorization|"
    r"session[_-]?(?:id|key|identifier))[a-z0-9_-]*|session|auth|"
    r"auth[_-]?(?:value|header|data|info))"
)
_ACCOUNT_KEY_PATTERN = r"(?:account[_-]?id|accountid|account)"
_REDACTED = "***REDACTED***"


def _redact_keyed_text(text, key_pattern, replacement):
    quoted = re.compile(
        r"(?i)([\"']?" + key_pattern + r"[\"']?\s*[:=]\s*)"
        r"([\"'])(.*?)\2"
    )
    bare = re.compile(
        r"(?i)([\"']?" + key_pattern + r"[\"']?\s*[:=]\s*)"
        r"([^,\s}\]]+)"
    )

    def replace_quoted(match):
        raw = match.group(3)
        value = replacement(raw) if callable(replacement) else replacement
        return match.group(1) + match.group(2) + value + match.group(2)

    def replace_bare(match):
        raw = match.group(2).strip("\"'")
        value = replacement(raw) if callable(replacement) else replacement
        return match.group(1) + value

    return bare.sub(replace_bare, quoted.sub(replace_quoted, text))


def _known_account_ids(extra=None):
    values = []
    configured = str(globals().get("ACCOUNT_ID", "") or "")
    if configured:
        values.append(configured)
    state = globals().get("g")
    batch = getattr(state, "batch", None)
    if batch is not None:
        account_id = str(batch.header.get("account_id", "") or "")
        if account_id:
            values.append(account_id)
    for account_id in getattr(state, "snapshot_accounts", {}).values():
        account_id = str(account_id or "")
        if account_id:
            values.append(account_id)
    if extra is not None:
        if isinstance(extra, (list, tuple, set)):
            supplied = extra
        else:
            supplied = (extra,)
        for account_id in supplied:
            account_id = str(account_id or "")
            if account_id:
                values.append(account_id)
    return sorted(set(values), key=len, reverse=True)


def _redact_text(value, limit=None, account_ids=None):
    text = str(value).replace("\r", " ").replace("\n", " ")
    for account_id in _known_account_ids(account_ids):
        # Short test/config placeholders such as "1" are not broker account
        # identifiers and would corrupt prices, timestamps, and order ids.
        if len(account_id) < 5:
            continue
        pattern = re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(account_id)
            + r"(?![A-Za-z0-9])"
        )
        text = pattern.sub(_mask_account(account_id), text)
    authorization = re.compile(
        r"(?i)(\bauthorization\s*[:=]\s*)([^,;}\]]+)"
    )

    def redact_authorization(match):
        raw = match.group(2).strip()
        prefix = "Bearer " if raw.lower().startswith("bearer ") else ""
        return match.group(1) + prefix + _REDACTED

    text = authorization.sub(redact_authorization, text)
    bearer = re.compile(r"(?i)(\bbearer\s+)([^\s,;}\]]+)")
    text = bearer.sub(lambda match: match.group(1) + _REDACTED, text)
    session_credential = re.compile(
        r"(?i)(\bsession[ _-]+credential\s*(?:[:=]\s*)?)"
        r"([^\s,;}\]]+)"
    )
    text = session_credential.sub(
        lambda match: match.group(1) + _REDACTED, text,
    )
    unkeyed_credential = re.compile(
        r"(?i)(\bcredential\s+)([^\s,;}\]]+)"
    )
    text = unkeyed_credential.sub(
        lambda match: match.group(1) + _REDACTED, text,
    )
    unkeyed_session = re.compile(
        r"(?i)(\bsession\s+)(?!credential\b)([^\s,;}\]]+)"
    )
    text = unkeyed_session.sub(
        lambda match: match.group(1) + _REDACTED, text,
    )
    text = _redact_keyed_text(text, _SECRET_KEY_PATTERN, _REDACTED)
    text = _redact_keyed_text(text, _ACCOUNT_KEY_PATTERN, _mask_account)
    if limit is not None:
        return text[:int(limit)]
    return text


def _bounded_text(value, limit=240, account_ids=None):
    return _redact_text(value, limit, account_ids)


def _normalized_field_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _is_secret_field_name(name):
    normalized = _normalized_field_name(name)
    if normalized in (
            "authorizationpath", "otherauthorizationpath",
            "authorizationpresent", "otherauthorizationpresent"):
        return False
    if any(fragment in normalized for fragment in (
            "credential", "password", "passwd", "secret", "token",
            "sendkey", "apikey", "cookie", "authorization")):
        return True
    if normalized == "session" or any(
            fragment in normalized for fragment in (
                "sessionid", "sessionkey", "sessionidentifier",
            )):
        return True
    return normalized in (
        "auth", "authvalue", "authheader", "authdata", "authinfo",
    )


def _sanitize_value(
        value, field_name="", account_ids=None, depth=0,
        preserve_items=False):
    normalized = _normalized_field_name(field_name)
    if _is_secret_field_name(field_name):
        return _REDACTED
    if "accountid" in normalized or normalized == "account":
        return _mask_account(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value, 2048, account_ids)
    if depth >= 6:
        return "[MAX_DEPTH]"
    if isinstance(value, (list, tuple)):
        items = value if preserve_items else value[:50]
        return [
            _sanitize_value(item, account_ids=account_ids, depth=depth + 1)
            for item in items
        ]
    if isinstance(value, dict):
        items = list(value.items())
        if depth > 0:
            items = items[:50]
        return dict(
            (str(key), _sanitize_value(
                item, key, account_ids, depth + 1,
                depth == 0 and _normalized_field_name(key) in (
                    "positions", "candidates",
                ),
            ))
            for key, item in items
        )
    return _redact_text(repr(value), 512, account_ids)


def _remember_log_write_failure(event_type, exc):
    marker = g.log_write_failure
    if marker is None:
        marker = {
            "failed_event": _bounded_text(event_type, 80),
            "error_type": type(exc).__name__,
            "error_message": _bounded_text(exc),
            "failure_count": 0,
            "first_failure_ts": datetime.datetime.now().isoformat(),
        }
    marker["failure_count"] = min(
        int(marker.get("failure_count", 0)) + 1, 999999,
    )
    g.log_write_failure = marker
    print(
        "[qlib_bridge] LOG_WRITE_PENDING event=%s error=%s"
        % (marker["failed_event"], marker["error_type"])
    )


def _append_event_json(event):
    _append_log_line(
        "qmt_events_%s.jsonl" % _today(),
        json.dumps(event, ensure_ascii=True, sort_keys=True),
    )


def _log_event(event_type, _account_context=None, **fields):
    event = {
        "ts": datetime.datetime.now().isoformat(),
        "event": str(event_type),
    }
    event.update(fields)
    event = _sanitize_value(event, account_ids=_account_context)
    try:
        _append_event_json(event)
    except Exception as exc:
        _remember_log_write_failure(event_type, exc)
        return

    recovery = None
    if g.log_write_failure is not None and event_type != "LOG_WRITE_RECOVERED":
        recovery = {
            "ts": datetime.datetime.now().isoformat(),
            "event": "LOG_WRITE_RECOVERED",
        }
        recovery.update(g.log_write_failure)
        recovery["recovered_after_event"] = str(event_type)
        try:
            _append_event_json(recovery)
        except Exception as exc:
            _remember_log_write_failure("LOG_WRITE_RECOVERED", exc)
            recovery = None
        else:
            g.log_write_failure = None

    try:
        message = event.get("message", "")
        _append_log_line(
            "qmt_bridge_%s.log" % _today(),
            "%s [%s] %s" % (event["ts"], event["event"], message),
        )
        if recovery is not None:
            _append_log_line(
                "qmt_bridge_%s.log" % _today(),
                "%s [LOG_WRITE_RECOVERED] %s" % (
                    recovery["ts"], recovery["failed_event"],
                ),
            )
    except Exception:
        print("[qlib_bridge] text log persistence failed")


def _json_safe_value(value):
    return _sanitize_value(value)


def _safe_detail(obj, field_names):
    """Return an allow-listed, JSON-safe detail without credentials."""
    result = {}
    for field in field_names:
        if isinstance(field, (tuple, list)):
            output_name = field[0]
            candidates = field[1:]
        else:
            output_name = field
            candidates = (field,)
        value = None
        found = False
        for name in candidates:
            try:
                if isinstance(obj, dict) and name in obj:
                    value = obj.get(name)
                    found = True
                    break
                if not isinstance(obj, dict):
                    value = getattr(obj, name)
                    found = True
                    break
            except Exception:
                continue
        if found:
            result[str(output_name)] = _json_safe_value(value)
    return result


def _mask_account(account_id):
    value = str(account_id or "")
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + ("*" * (len(value) - 4)) + value[-2:]


def _evidence_for(batch, coid):
    evidence = batch.order_evidence.get(coid)
    if evidence is None:
        evidence = {
            "query_count": 0,
            "attempt_started": None,
            "api_returned": False,
            "api_return": {},
            "order_observed": False,
            "qmt_order_ids": [],
            "callback_counts": {"order": 0, "deal": 0, "error": 0},
            "last_broker_statuses": [],
        }
        batch.order_evidence[coid] = evidence
    evidence.setdefault("query_count", 0)
    evidence.setdefault("attempt_started", None)
    evidence.setdefault("api_returned", False)
    evidence.setdefault("api_return", {})
    evidence.setdefault("order_observed", False)
    evidence.setdefault("qmt_order_ids", [])
    callback_counts = evidence.setdefault("callback_counts", {})
    callback_counts.setdefault("order", 0)
    callback_counts.setdefault("deal", 0)
    callback_counts.setdefault("error", 0)
    evidence.setdefault("last_broker_statuses", [])
    return evidence


def _now_hms():
    return datetime.datetime.now().strftime("%H:%M:%S")


def _today():
    return datetime.date.today().strftime("%Y-%m-%d")


def _profile_settings():
    try:
        return dict(_EXECUTION_PROFILES[EXECUTION_PROFILE])
    except KeyError:
        raise ValueError("unknown execution profile: %r" % EXECUTION_PROFILE)


def _expected_signal_price_type():
    return _profile_settings()["signal_price_type"]


def _activate_profile_settings():
    settings = _profile_settings()
    global LIMIT_PRICE_TYPE
    global SELL_WAIT_TIMEOUT_SEC
    global TRADE_START
    global CANCEL_AT
    global FINALIZE_AT
    global SNAPSHOT_REFRESH_AT
    LIMIT_PRICE_TYPE = settings["qmt_price_type"]
    SELL_WAIT_TIMEOUT_SEC = settings["sell_wait_seconds"]
    TRADE_START = settings["submit_after"]
    CANCEL_AT = settings["cancel_at"]
    FINALIZE_AT = settings["finalize_at"]
    SNAPSHOT_REFRESH_AT = settings["snapshot_after"]


def _canonical_bridge_root(path):
    raw = str(path)
    if not raw or not os.path.isabs(raw):
        raise ValueError("bridge roots must be absolute canonical paths")
    canonical = os.path.normcase(os.path.realpath(os.path.abspath(raw)))
    if os.path.normcase(raw) != canonical:
        raise ValueError("bridge roots must use canonical path spelling")
    return canonical


def _validate_profile_roots():
    current_root = _canonical_bridge_root(BRIDGE_ROOT)
    other_root = _canonical_bridge_root(OTHER_BRIDGE_ROOT)
    if EXECUTION_PROFILE == "CLOSE_AUCTION":
        expected_other = os.path.normcase(os.path.join(
            current_root, "pr49_probe",
        ))
        valid = other_root == expected_other
    else:
        expected_current = os.path.normcase(os.path.join(
            other_root, "pr49_probe",
        ))
        valid = current_root == expected_current
    if not valid:
        raise ValueError(
            "profile roots must be an exact main/pr49_probe direct pair"
        )


def _path(*parts):
    return os.path.join(BRIDGE_ROOT, *parts)


def _ensure_dirs():
    for d in ("inbox", "processing", "outbound", "archive", "state", "logs"):
        p = _path(d)
        if not os.path.isdir(p):
            os.makedirs(p)
    _ensure_snapshot_dirs()


def _ensure_snapshot_dirs():
    log_dir = _path("logs")
    if not os.path.isdir(log_dir):
        os.makedirs(log_dir)
    request_root = _path("snapshot_requests")
    for d in ("inbox", "processing", "archive", "responses"):
        p = os.path.join(request_root, d)
        if not os.path.isdir(p):
            os.makedirs(p)


def _state_file():
    return _path("state", "processed_batches.txt")


def _load_processed():
    if os.path.isfile(_state_file()):
        with open(_state_file(), "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    g.processed.add(line)


def _mark_processed(batch_id):
    if batch_id in g.processed:
        return
    g.processed.add(batch_id)
    with open(_state_file(), "a") as f:
        f.write(batch_id + "\n")


def _sha256_of_lines(lines):
    import hashlib
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
    return "sha256:" + h.hexdigest()


def _authorization_path(trade_date):
    prefix = _profile_settings()["authorization_prefix"]
    return os.path.join(BRIDGE_ROOT, "state", prefix + trade_date)


def _other_authorization_path(trade_date):
    prefix = _profile_settings()["other_authorization_prefix"]
    return os.path.join(OTHER_BRIDGE_ROOT, "state", prefix + trade_date)


def _live_ok(trade_date):
    return os.path.isfile(_authorization_path(trade_date))


def _other_profile_authorized(trade_date):
    return os.path.isfile(_other_authorization_path(trade_date))


def _active_state_path(batch_id):
    return _path("state", "active_" + batch_id + ".json")


def _save_active_state(batch):
    payload = {
        "batch_id": batch.batch_id(),
        "phase": batch.phase,
        "phase_started": batch.phase_started,
        "trading_started": batch.trading_started,
        "execution_authorized": batch.execution_authorized,
        "execution_live": batch.execution_live,
        "dual_authorization_blocked": batch.dual_authorization_blocked,
        "submitted": sorted(batch.submitted.keys()),
        "fills": batch.fills,
        "remaining_cash": batch.remaining_cash,
        "order_evidence": batch.order_evidence,
        "orders": batch.orders,
    }
    path = _active_state_path(batch.batch_id())
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, sort_keys=True)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def _load_active_state(batch):
    path = _active_state_path(batch.batch_id())
    if not os.path.isfile(path):
        return
    with open(path, "r") as f:
        payload = json.load(f)
    if payload.get("batch_id") != batch.batch_id():
        raise ValueError("active state batch_id mismatch")
    batch.phase = payload.get("phase", "SELL")
    batch.trading_started = bool(payload.get("trading_started", False))
    batch.execution_authorized = bool(payload.get(
        "execution_authorized", payload.get("execution_live", False),
    ))
    batch.execution_live = bool(payload.get("execution_live", False))
    batch.dual_authorization_blocked = bool(payload.get(
        "dual_authorization_blocked", False,
    ))
    batch.submitted = dict((coid, True) for coid in payload.get("submitted", []))
    batch.fills = payload.get("fills", {})
    batch.remaining_cash = payload.get("remaining_cash")
    loaded_evidence = payload.get("order_evidence", {})
    batch.order_evidence = (
        loaded_evidence if isinstance(loaded_evidence, dict) else {}
    )
    if payload.get("orders"):
        batch.orders = payload["orders"]
    try:
        phase_started = float(payload.get("phase_started"))
    except (TypeError, ValueError):
        phase_started = time.time()
    if not math.isfinite(phase_started) or phase_started <= 0.0:
        phase_started = time.time()
    batch.phase_started = phase_started


def _remove_active_state(batch_id):
    path = _active_state_path(batch_id)
    if os.path.isfile(path):
        os.remove(path)


def _fail_safe_corrupt_active_state(batch):
    """Preserve corrupt state and assume every order may be in flight."""
    path = _active_state_path(batch.batch_id())
    if os.path.isfile(path):
        suffix = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        corrupt = path + ".corrupt_" + suffix
        sequence = 1
        while os.path.isfile(corrupt):
            corrupt = path + ".corrupt_" + suffix + "_" + str(sequence)
            sequence += 1
        os.rename(path, corrupt)
    batch.phase = "SELL"
    batch.phase_started = time.time()
    batch.trading_started = True
    batch.execution_authorized = False
    batch.execution_live = (
        batch.broker_authorized and batch.header.get("mode") == "LIVE"
    )
    batch.submitted = dict(
        (order["client_order_id"], True) for order in batch.orders
    )
    batch.fills = {}
    batch.remaining_cash = None
    batch.order_evidence = {}
    _save_active_state(batch)

# ======================= fills output =======================


def _fills_path(batch_id):
    return _path("outbound", "fills_" + batch_id + ".jsonl")


def _log_order_finalized(batch, order, fill):
    coid = order["client_order_id"]
    evidence = _evidence_for(batch, coid)
    _log_event(
        "ORDER_FINALIZED",
        batch_id=batch.batch_id(),
        client_order_id=coid,
        stock_code=order.get("stock_code", ""),
        side=order.get("side", ""),
        requested_quantity=int(order.get("quantity", 0) or 0),
        api_returned=bool(evidence.get("api_returned", False)),
        api_return=evidence.get("api_return", {}),
        order_observed=bool(evidence.get("order_observed", False)),
        qmt_order_ids=evidence.get("qmt_order_ids", []),
        callback_counts=evidence.get("callback_counts", {}),
        query_count=int(evidence.get("query_count", 0)),
        fill_status=fill.get("status", ""),
        filled_quantity=int(fill.get("filled_qty", 0) or 0),
        average_price=float(fill.get("avg_price", 0.0) or 0.0),
        reason=str(fill.get("message", "") or ""),
        message=str(fill.get("message", "") or fill.get("status", "")),
    )


def _write_fill(batch, order, status, filled_qty, avg_price, qmt_order_id, message):
    account_id = _account_id(batch)
    message = _redact_text(message, 512, (account_id,))
    mode = batch.header.get("mode", "SIMULATE")
    requested_qty = int(order.get("quantity", 0) or 0)
    if requested_qty <= 0 and order.get("side") == "BUY":
        requested_qty = 100
    event = {
        "type": "fill_event",
        "batch_id": batch.batch_id(),
        "client_order_id": order["client_order_id"],
        "mode": mode,
        "stock_code": order["stock_code"],
        "side": order["side"],
        "status": status,
        "requested_qty": requested_qty,
        "filled_qty": int(filled_qty),
        "avg_price": float(avg_price),
        "qmt_order_id": str(qmt_order_id),
        "message": message,
        "ts": datetime.datetime.now().isoformat(),
        # Shares satisfied by same-day internal transfer instead of a market
        # order. Mac reconstructs the ladder move from applied_qty + netted_qty.
        "netted_qty": int(order.get("netted_qty", 0) or 0),
    }
    prev = batch.fills.get(order["client_order_id"])
    if prev is not None and prev["status"] == status \
            and prev["filled_qty"] == event["filled_qty"] \
            and prev.get("netted_qty", 0) == event["netted_qty"]:
        return  # no change, do not spam the file
    batch.fills[order["client_order_id"]] = event
    with open(_fills_path(batch.batch_id()), "a") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")
    _save_active_state(batch)
    previous_status = prev.get("status", "") if prev is not None else ""
    if previous_status != status:
        _log_event(
            "ORDER_STATUS_CHANGED",
            _account_context=account_id,
            batch_id=batch.batch_id(),
            client_order_id=order["client_order_id"],
            stock_code=order["stock_code"],
            previous_status=previous_status,
            status=status,
            filled_quantity=int(filled_qty),
            average_price=float(avg_price),
            qmt_order_id=str(qmt_order_id),
            reason=str(message or ""),
            message="%s -> %s" % (previous_status or "NONE", status),
        )
    if status in (
            "FILLED", "PARTIAL", "REJECTED", "SKIPPED", "EXPIRED", "ERROR"):
        _log_order_finalized(batch, order, event)


def _account_snapshot_path(batch_id):
    return _path("outbound", "account_" + batch_id + ".jsonl")


def _qmt_stock_code(symbol, exchange):
    """Rebuild the 600000.SH form from a POSITION row."""
    symbol = str(symbol).strip()
    market = str(exchange or "").strip().upper()
    if market.startswith("SH"):
        return symbol + ".SH"
    if market.startswith("SZ"):
        return symbol + ".SZ"
    if market.startswith("BJ"):
        return symbol + ".BJ"
    if symbol[:1] in ("6", "9"):
        return symbol + ".SH"
    if symbol[:1] in ("0", "2", "3"):
        return symbol + ".SZ"
    if symbol[:1] in ("4", "8"):
        return symbol + ".BJ"
    return symbol


def _opt_float(obj, *names):
    for name in names:
        raw = getattr(obj, name, None)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def _dump_broker_snapshot(batch_id, trade_date, account_id, label):
    """Dump broker ACCOUNT + POSITION for second-line reconciliation.

    The fill receipts only describe what the strategy believes it traded.
    This snapshot is the broker's own view, so the Mac side can detect
    ledger drift (missed split children, fee/opening-cash gaps) the same day.

    Never raises: a failed snapshot must not block batch finalization.
    """
    try:
        rows = []
        accounts = get_trade_detail_data(account_id, ACCOUNT_TYPE, "ACCOUNT")
        if accounts:
            a = accounts[0]
            rows.append({
                "type": "account_snapshot",
                "batch_id": batch_id,
                "trade_date": trade_date,
                "account_id_masked": _mask_account(
                    getattr(a, "m_strAccountID", "") or account_id,
                ),
                "available_cash": _opt_float(a, "m_dAvailable"),
                "total_asset": _opt_float(a, "m_dBalance", "m_dAssureAsset"),
                "market_value": _opt_float(
                    a, "m_dInstrumentValue", "m_dStockValue"),
                "frozen_cash": _opt_float(a, "m_dFrozenCash"),
                "ts": datetime.datetime.now().isoformat(),
            })
        else:
            _log("ACCOUNT query returned no rows; snapshot has positions only")

        positions = get_trade_detail_data(account_id, ACCOUNT_TYPE, "POSITION")
        for p in positions or []:
            shares = int(getattr(p, "m_nVolume", 0) or 0)
            if shares <= 0:
                continue
            rows.append({
                "type": "broker_position",
                "batch_id": batch_id,
                "trade_date": trade_date,
                "stock_code": _qmt_stock_code(
                    getattr(p, "m_strInstrumentID", ""),
                    getattr(p, "m_strExchangeID", ""),
                ),
                "shares": shares,
                "can_use_volume": int(getattr(p, "m_nCanUseVolume", 0) or 0),
                "frozen_shares": int(
                    getattr(p, "m_nFrozenVolume", 0) or 0,
                ),
                "avg_cost": _opt_float(p, "m_dOpenPrice", "m_dPositionCost"),
                "market_value": _opt_float(p, "m_dMarketValue"),
                "ts": datetime.datetime.now().isoformat(),
            })

        path = _account_snapshot_path(batch_id)
        with open(path, "w") as f:
            for row in rows:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        with open(path.replace(".jsonl", ".done"), "w") as f:
            f.write("done\n")
        account_row = next(
            (row for row in rows if row.get("type") == "account_snapshot"),
            {},
        )
        position_rows = [
            row for row in rows if row.get("type") == "broker_position"
        ]
        _log_event(
            "ACCOUNT_SNAPSHOT",
            _account_context=account_id,
            batch_id=batch_id,
            trade_date=trade_date,
            label=label,
            account_id_masked=_mask_account(account_id),
            account_type=ACCOUNT_TYPE,
            available_cash=account_row.get("available_cash"),
            frozen_cash=account_row.get("frozen_cash"),
            total_asset=account_row.get("total_asset"),
            market_value=account_row.get("market_value"),
            positions=position_rows,
            position_count=len(position_rows),
            message="broker account snapshot persisted",
        )
        _log(
            "account snapshot written (%s): %d rows" % (label, len(rows)),
            (account_id,),
        )
        return True
    except Exception:
        _log(
            "account snapshot failed:\n" + traceback.format_exc(),
            (account_id,),
        )
        return False


def _write_account_snapshot(batch):
    if (not batch.execution_live or batch.header.get("mode") != "LIVE"
            or not batch.broker_authorized):
        return
    _dump_broker_snapshot(
        batch.batch_id(), batch.header.get("trade_date", ""),
        _account_id(batch), "finalize",
    )


def _snapshot_marker_path(batch_id):
    return _path("state", "snapshot_refresh_" + batch_id + ".json")


def _write_snapshot_marker(batch):
    """Ask the post-close pass to rewrite this batch's broker snapshot.

    The finalize-time snapshot (15:00~15:01) carries final cash and shares
    but intraday prices; on the sim account even the account values can be
    stale. After SNAPSHOT_REFRESH_AT the marker triggers a rewrite with
    close values, before the 15:32 Mac-side import.
    """
    if (not batch.execution_live or batch.header.get("mode") != "LIVE"
            or not batch.broker_authorized):
        return
    payload = {
        "batch_id": batch.batch_id(),
        "trade_date": batch.header.get("trade_date", ""),
        "account_id_masked": _mask_account(_account_id(batch)),
        "account_environment": batch.header.get(
            "account_environment", ""),
        "account_type": batch.header.get("account_type", ACCOUNT_TYPE),
        "schema_version": batch.header.get("schema_version", ""),
        "signal_checksum": batch.header.get("checksum", ""),
        "strategy_id": batch.header.get("strategy_id", ""),
        "order_count": batch.header.get("order_count", len(batch.orders)),
    }
    g.snapshot_accounts[batch.batch_id()] = _account_id(batch)
    try:
        with open(_snapshot_marker_path(batch.batch_id()), "w") as f:
            f.write(json.dumps(payload, sort_keys=True))
    except Exception:
        _log("snapshot marker write failed:\n" + traceback.format_exc())


def _trusted_snapshot_account_ids(info):
    """Return exact accounts from fully validated durable batch sources."""
    batch_id = info.get("batch_id", "")
    if not isinstance(batch_id, str) or not batch_id:
        return set()
    signal_checksum = info.get("signal_checksum", "")
    if not isinstance(signal_checksum, str) or not signal_checksum:
        return set()
    marker_order_count = info.get("order_count")
    if not isinstance(marker_order_count, int) or marker_order_count < 0:
        return set()
    accounts = set()
    exact_name = "signal_" + batch_id + ".jsonl"
    repeat_prefix = "signal_" + batch_id + ".repeat_"
    for directory in ("processing", "archive"):
        root = _path(directory)
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            if name != exact_name and not (
                    name.startswith(repeat_prefix)
                    and name.endswith(".jsonl")):
                continue
            path = os.path.join(root, name)
            done_path = os.path.join(root, name[:-6] + ".done")
            if not os.path.isfile(done_path):
                continue
            try:
                if os.path.getsize(path) > MAX_BATCH_BYTES:
                    continue
                with open(path, "r") as f:
                    lines = [
                        line.strip() for line in f.read().splitlines()
                        if line.strip()
                    ]
                if not lines:
                    continue
                header = json.loads(lines[0])
                order_lines = lines[1:]
                orders = [json.loads(line) for line in order_lines]
                with open(done_path, "r") as f:
                    done_checksum = f.read().strip()
            except Exception:
                continue
            if not isinstance(header, dict):
                continue
            if any(not isinstance(order, dict) for order in orders):
                continue
            calculated_checksum = _sha256_of_lines(order_lines)
            if calculated_checksum != signal_checksum:
                continue
            if header.get("checksum") != calculated_checksum:
                continue
            if done_checksum != calculated_checksum:
                continue
            if header.get("batch_id") != batch_id:
                continue
            if header.get("trade_date") != info.get("trade_date"):
                continue
            if header.get("mode") != "LIVE":
                continue
            if header.get("schema_version") != info.get("schema_version"):
                continue
            if header.get("schema_version") != SCHEMA_VERSION:
                continue
            if header.get("strategy_id") != info.get("strategy_id"):
                continue
            if header.get("account_environment") != info.get(
                    "account_environment"):
                continue
            if header.get("account_environment") != ACCOUNT_ENVIRONMENT:
                continue
            if header.get("account_type") != info.get("account_type"):
                continue
            if header.get("account_type") != ACCOUNT_TYPE:
                continue
            if header.get("order_count") != marker_order_count:
                continue
            if header.get("order_count") != len(orders):
                continue
            if any(order.get("batch_id") != batch_id for order in orders):
                continue
            account_id = str(header.get("account_id", "") or "")
            if not account_id:
                continue
            if _mask_account(account_id) != info.get("account_id_masked"):
                continue
            accounts.add(account_id)
    return accounts


def _snapshot_account_binding(info):
    """Resolve and verify the exact account without storing it in marker."""
    if info.get("account_environment") != ACCOUNT_ENVIRONMENT:
        return ""
    if info.get("account_type") != ACCOUNT_TYPE:
        return ""
    batch_id = info.get("batch_id", "")
    durable_accounts = _trusted_snapshot_account_ids(info)
    if len(durable_accounts) > 1:
        return ""
    durable_account = next(iter(durable_accounts), "")
    memory_account = str(g.snapshot_accounts.get(batch_id, "") or "")
    configured_account = str(ACCOUNT_ID or "")
    account_id = durable_account or memory_account
    if not account_id:
        return ""
    if durable_account and memory_account \
            and durable_account != memory_account:
        return ""
    if durable_account and configured_account \
            and durable_account != configured_account:
        return ""
    if _mask_account(account_id) != info.get("account_id_masked"):
        return ""
    return account_id


def _refresh_account_snapshots_after_close():
    """Rewrite pending broker snapshots once the close is in (>= 15:01)."""
    if _now_hms() < SNAPSHOT_REFRESH_AT:
        return
    state_dir = _path("state")
    if not os.path.isdir(state_dir):
        return
    for name in sorted(os.listdir(state_dir)):
        if not name.startswith("snapshot_refresh_"):
            continue
        path = os.path.join(state_dir, name)
        try:
            with open(path, "r") as f:
                info = json.load(f)
        except Exception:
            _log("unreadable snapshot marker %s; dropping" % name)
            os.remove(path)
            continue
        if info.get("trade_date") != _today():
            # QMT was closed before the refresh window that day; the
            # finalize-time fallback snapshot already covers the batch.
            _log("stale snapshot marker %s; dropping" % name)
            os.remove(path)
            continue
        batch_id = info.get("batch_id", "")
        account_id = _snapshot_account_binding(info)
        if not account_id:
            _log("snapshot refresh account binding unavailable for %s" % batch_id)
            continue
        refreshed = _dump_broker_snapshot(
            batch_id, info["trade_date"], account_id, "post-close")
        if not refreshed:
            continue
        g.snapshot_accounts.pop(batch_id, None)
        os.remove(path)


def _finalize_batch(batch):
    if batch.finalized:
        return
    # Snapshot before the fills .done marker so the Mac importer never sees
    # receipts for a batch whose broker snapshot is still missing. The
    # post-close pass rewrites it with close values via the marker.
    _write_account_snapshot(batch)
    _write_snapshot_marker(batch)
    fills_path = _fills_path(batch.batch_id())
    # Empty batches still need a jsonl companion so the Mac importer can
    # archive the terminal receipt pair without warning.
    with open(fills_path, "a"):
        pass
    done = fills_path.replace(".jsonl", ".done")
    with open(done, "w") as f:
        f.write("done\n")
    _mark_processed(batch.batch_id())
    if batch.processing_jsonl and batch.processing_done:
        _archive_processing(batch.processing_jsonl, batch.processing_done)
    _remove_active_state(batch.batch_id())
    batch.finalized = True
    g.batch = None
    _log("batch %s finalized (%d fills)" % (batch.batch_id(), len(batch.fills)))

# ======================= batch claiming =======================


def _peek_trade_date(jsonl_path):
    """Read only the header line to get trade_date. None if unreadable."""
    try:
        if os.path.getsize(jsonl_path) > MAX_BATCH_BYTES:
            return None
        with open(jsonl_path, "r") as f:
            first = f.readline().strip()
        trade_date = json.loads(first).get("trade_date")
        return trade_date if isinstance(trade_date, str) else None
    except Exception:
        return None


def _claim_new_batch():
    if g.batch is not None:
        return
    inbox = _path("inbox")
    if not os.path.isdir(inbox):
        return
    done_files = sorted([f for f in os.listdir(inbox) if f.endswith(".done")])
    for done_name in done_files:
        jsonl_name = done_name[:-5] + ".jsonl"
        jsonl_src = os.path.join(inbox, jsonl_name)
        done_src = os.path.join(inbox, done_name)
        if not os.path.isfile(jsonl_src):
            continue
        # future batch (T-1 evening publish): leave in inbox until trade_date
        trade_date = _peek_trade_date(jsonl_src)
        if trade_date is not None and trade_date > _today():
            continue
        # claim: move both to processing/
        jsonl_dst = _path("processing", jsonl_name)
        done_dst = _path("processing", done_name)
        os.rename(jsonl_src, jsonl_dst)
        os.rename(done_src, done_dst)
        batch = _parse_and_check(jsonl_dst, done_dst)
        if batch is not None:
            g.batch = batch
            _save_active_state(batch)
            _log("claimed batch %s: %d orders, mode=%s"
                 % (batch.batch_id(), len(batch.orders), batch.header.get("mode")))
            return  # one batch at a time


def _archive_processing(jsonl_path, done_path):
    paths = (jsonl_path, done_path)
    collision = any(os.path.isfile(
        _path("archive", os.path.basename(path))) for path in paths)
    suffix = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    for p in paths:
        dst = _path("archive", os.path.basename(p))
        if collision:
            stem, ext = os.path.splitext(os.path.basename(p))
            dst = _path("archive", stem + ".repeat_" + suffix + ext)
            sequence = 1
            while os.path.isfile(dst):
                dst = _path(
                    "archive", stem + ".repeat_" + suffix
                    + "_" + str(sequence) + ext,
                )
                sequence += 1
        os.rename(p, dst)


def _parse_and_check(jsonl_path, done_path):
    """Return Batch or None (rejected batches get a fills file + done)."""
    header = {}
    orders = []
    order_lines = []
    try:
        if os.path.getsize(jsonl_path) > MAX_BATCH_BYTES:
            raise ValueError("batch file exceeds byte limit")
        with open(jsonl_path, "r") as f:
            lines = [l.strip() for l in f.read().splitlines() if l.strip()]
        if not lines:
            raise ValueError("batch file is empty")
        parsed_header = json.loads(lines[0])
        if not isinstance(parsed_header, dict):
            raise ValueError("batch header must be an object")
        header = parsed_header
        if not isinstance(header.get("batch_id"), str) \
                or not header.get("batch_id"):
            raise ValueError("batch_id must be a nonempty string")
        order_lines = lines[1:]
        orders = [json.loads(line) for line in order_lines]
        if any(not isinstance(order, dict) for order in orders):
            raise ValueError("each order must be an object")
    except Exception as exc:
        reason = _bounded_text(exc, 512)
        _log_event(
            "BATCH_VALIDATED",
            batch_id=_bounded_text(header.get("batch_id", ""), 128),
            strategy_id=_bounded_text(header.get("strategy_id", ""), 128),
            trade_date=_bounded_text(header.get("trade_date", ""), 32),
            account_id_masked=_mask_account(header.get("account_id", "")),
            order_count=len(order_lines),
            jsonl_file=os.path.basename(jsonl_path),
            done_file=os.path.basename(done_path),
            validation_passed=False,
            rejection_reason=reason,
            execution_profile=EXECUTION_PROFILE,
            message="batch validation rejected before structure load: " + reason,
        )
        _log("quarantine unreadable batch %s: %s"
             % (os.path.basename(jsonl_path), reason))
        _archive_processing(jsonl_path, done_path)
        return None
    batch = Batch(header, orders)
    batch.processing_jsonl = jsonl_path
    batch.processing_done = done_path
    batch_id = header.get("batch_id", "unknown")
    expected = ""
    actual = ""

    def reject(reason):
        batch.broker_authorized = False
        _log("reject batch %s: %s" % (batch_id, reason))
        _log_event(
            "BATCH_VALIDATED",
            batch_id=batch_id,
            strategy_id=header.get("strategy_id", ""),
            trade_date=header.get("trade_date", ""),
            mode=header.get("mode", ""),
            account_id_masked=_mask_account(header.get("account_id", "")),
            account_type=header.get("account_type", ""),
            account_environment=header.get("account_environment", ""),
            order_count=len(orders),
            jsonl_file=os.path.basename(jsonl_path),
            done_file=os.path.basename(done_path),
            expected_checksum=expected,
            header_checksum=header.get("checksum", ""),
            calculated_checksum=actual,
            checksum_match=bool(
                actual and expected in ("", actual)
                and header.get("checksum") == actual
            ),
            validation_passed=False,
            rejection_reason=str(reason),
            execution_profile=EXECUTION_PROFILE,
            message="batch validation rejected: " + str(reason),
        )
        for o in orders:
            if not all(o.get(key) for key in (
                    "client_order_id", "stock_code", "side")):
                _log("skip malformed order receipt in batch %s" % batch_id)
                continue
            _write_fill(batch, o, "SKIPPED", 0, 0.0, "", reason)
        _finalize_batch(batch)
        return None

    if batch_id in g.processed:
        return reject("duplicate batch")
    if header.get("schema_version") != SCHEMA_VERSION:
        return reject("schema_version must be %s" % SCHEMA_VERSION)
    if header.get("account_environment") != ACCOUNT_ENVIRONMENT:
        return reject("account_environment does not match QMT configuration")
    if ACCOUNT_ENVIRONMENT == "REAL":
        if not ALLOW_REAL_MONEY:
            return reject("REAL execution requires ALLOW_REAL_MONEY=True")
        if not ACCOUNT_ID:
            return reject("REAL execution requires configured ACCOUNT_ID")
        if header.get("mode") != "LIVE":
            return reject("REAL execution requires LIVE mode")
    if header.get("mode") not in ("SIMULATE", "LIVE"):
        return reject("mode must be SIMULATE or LIVE")
    if header.get("account_type") != ACCOUNT_TYPE:
        return reject("account_type mismatch")
    if not header.get("account_id"):
        return reject("account_id missing")
    if ACCOUNT_ID and str(header.get("account_id")) != str(ACCOUNT_ID):
        return reject("account_id does not match configured QMT account")
    if header.get("trade_date") != _today():
        return reject("expired: trade_date=%s today=%s"
                      % (header.get("trade_date"), _today()))
    with open(done_path, "r") as f:
        expected = f.read().strip()
    actual = _sha256_of_lines(order_lines)
    if expected and expected != actual:
        return reject("checksum mismatch")
    if header.get("checksum") != actual:
        return reject("header checksum mismatch")
    if header.get("order_count") != len(orders):
        return reject("order_count mismatch")
    if len(orders) > MAX_ORDERS_PER_BATCH:
        return reject("order_count exceeds maximum %d"
                      % MAX_ORDERS_PER_BATCH)

    seen = set()
    for order in orders:
        coid = order.get("client_order_id", "")
        side = order.get("side")
        quantity = order.get("quantity")
        max_quantity = order.get("max_quantity", 0)
        target_value = order.get("target_value")
        if order.get("batch_id") != batch_id:
            return reject("order batch_id mismatch")
        if not coid or coid in seen:
            return reject("duplicate or empty client_order_id")
        seen.add(coid)
        if side not in ("BUY", "SELL"):
            return reject("invalid order side")
        if order.get("price_type") != _expected_signal_price_type():
            return reject(
                "price_type must match execution profile: " +
                _expected_signal_price_type()
            )
        if order.get("limit_price") != 0 and order.get("limit_price") != 0.0:
            return reject("limit_price must be zero")
        if side == "BUY":
            if quantity != 0:
                return reject("BUY quantity must be zero")
            try:
                target_value = float(target_value)
            except (TypeError, ValueError):
                return reject("BUY target_value invalid")
            if not math.isfinite(target_value) or target_value <= 0.0:
                return reject("BUY target_value invalid")
        else:
            # Odd lots are legal on the sell side: absorb_broker_excess folds
            # bonus shares into the ladder, and a maturing layer is always sold
            # whole. Rejecting non-multiples of 100 would drop the entire batch.
            if (not isinstance(quantity, int) or isinstance(quantity, bool)
                    or quantity <= 0):
                return reject("SELL quantity must be a positive integer")
            if target_value != 0 and target_value != 0.0:
                return reject("SELL target_value must be zero")
        if max_quantity is None:
            max_quantity = 0
        if (not isinstance(max_quantity, int)
                or isinstance(max_quantity, bool) or max_quantity < 0
                or max_quantity % 100 != 0):
            return reject("max_quantity must be zero or a whole lot")
        if side == "SELL" and max_quantity != 0:
            return reject("SELL max_quantity must be zero")

    # sells first by priority, stable by client_order_id
    orders.sort(key=lambda o: (o.get("priority", 99), o.get("client_order_id", "")))
    batch.orders = orders
    batch.broker_authorized = True
    _log_event(
        "BATCH_VALIDATED",
        batch_id=batch_id,
        strategy_id=header.get("strategy_id", ""),
        trade_date=header.get("trade_date", ""),
        mode=header.get("mode", ""),
        account_id_masked=_mask_account(header.get("account_id", "")),
        account_type=header.get("account_type", ""),
        account_environment=header.get("account_environment", ""),
        order_count=len(orders),
        jsonl_file=os.path.basename(jsonl_path),
        done_file=os.path.basename(done_path),
        expected_checksum=expected,
        header_checksum=header.get("checksum", ""),
        calculated_checksum=actual,
        checksum_match=(expected in ("", actual)
                        and header.get("checksum") == actual),
        validation_passed=True,
        execution_profile=EXECUTION_PROFILE,
        signal_price_type=_expected_signal_price_type(),
        message="batch validation passed",
    )
    return batch


def _recover_processing_batch():
    if g.batch is not None:
        return
    processing = _path("processing")
    if not os.path.isdir(processing):
        return
    # Claiming uses two renames. Repair the only possible split states before
    # scanning processing so a crash between those renames cannot strand a
    # batch forever.
    inbox = _path("inbox")
    for name in list(os.listdir(processing)):
        counterpart = None
        if name.startswith("signal_") and name.endswith(".jsonl"):
            counterpart = name[:-6] + ".done"
        elif name.startswith("signal_") and name.endswith(".done"):
            counterpart = name[:-5] + ".jsonl"
        if counterpart is None:
            continue
        src = os.path.join(inbox, counterpart)
        dst = os.path.join(processing, counterpart)
        if os.path.isfile(src) and not os.path.isfile(dst):
            os.rename(src, dst)
    done_files = sorted([f for f in os.listdir(processing)
                         if f.startswith("signal_") and f.endswith(".done")])
    for done_name in done_files:
        jsonl_name = done_name[:-5] + ".jsonl"
        jsonl_path = os.path.join(processing, jsonl_name)
        done_path = os.path.join(processing, done_name)
        if not os.path.isfile(jsonl_path):
            continue
        try:
            with open(jsonl_path, "r") as f:
                header = json.loads(f.readline().strip())
            batch_id = header.get("batch_id", "")
        except Exception:
            batch_id = ""
        if batch_id in g.processed:
            _archive_processing(jsonl_path, done_path)
            _remove_active_state(batch_id)
            continue
        batch = _parse_and_check(jsonl_path, done_path)
        if batch is None:
            continue
        try:
            _load_active_state(batch)
        except Exception:
            _log("corrupt active state for %s; disabling all resubmission:\n%s"
                 % (batch.batch_id(), traceback.format_exc()))
            _fail_safe_corrupt_active_state(batch)
        g.batch = batch
        _log("recovered batch %s: phase=%s submitted=%d"
             % (batch.batch_id(), batch.phase, len(batch.submitted)))
        return


# ======================= snapshot-only observation requests =======================

_SNAPSHOT_REQUEST_SCHEMA = "1.0"
_SNAPSHOT_EVIDENCE_PURPOSE = "SHARED_REAL_ACCOUNT_OPERATOR_PREFLIGHT"
_SNAPSHOT_REQUEST_STRATEGIES = (
    "csi1000_b6m_b2s_postclose_real",
    "csi1000_pr49_one_lot_probe",
)


def _snapshot_request_dir(name):
    return _path("snapshot_requests", name)


def _snapshot_artifact_checksum(payload):
    import hashlib
    body = dict(payload)
    body.pop("checksum", None)
    encoded = json.dumps(
        body, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _snapshot_account_fingerprint(account_id, account_type, environment):
    import hashlib
    material = "\0".join((
        "qlib-account-snapshot-v1",
        str(environment), str(account_type), str(account_id),
    )).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _snapshot_request_id_valid(request_id):
    return bool(re.match(
        r"^snapshot_[0-9]{8}_[a-f0-9]{32}$", str(request_id or ""),
    ))


def _snapshot_root_matches(value):
    try:
        expected = os.path.normcase(os.path.realpath(os.path.abspath(BRIDGE_ROOT)))
        observed = os.path.normcase(os.path.realpath(os.path.abspath(value)))
    except Exception:
        return False
    return expected == observed


def _validate_snapshot_request(payload, done_checksum):
    if not isinstance(payload, dict):
        raise ValueError("snapshot request must be an object")
    if payload.get("type") != "account_snapshot_request":
        raise ValueError("invalid snapshot request type")
    if payload.get("schema_version") != _SNAPSHOT_REQUEST_SCHEMA:
        raise ValueError("invalid snapshot request schema")
    request_id = payload.get("request_id", "")
    if not _snapshot_request_id_valid(request_id):
        raise ValueError("invalid snapshot request_id")
    if payload.get("trade_date") != _today():
        raise ValueError("snapshot request must be for today")
    if payload.get("collector_execution_profile") != EXECUTION_PROFILE:
        raise ValueError("snapshot request execution profile mismatch")
    if not _snapshot_root_matches(payload.get("collector_bridge_root", "")):
        raise ValueError("snapshot request canonical bridge root mismatch")
    if payload.get("requested_for_strategy_id") not in \
            _SNAPSHOT_REQUEST_STRATEGIES:
        raise ValueError("snapshot request strategy is not approved")
    if payload.get("evidence_purpose") != _SNAPSHOT_EVIDENCE_PURPOSE:
        raise ValueError("snapshot request evidence purpose mismatch")
    if payload.get("publish_cutoff") != SNAPSHOT_PUBLISH_CUTOFF:
        raise ValueError("snapshot request publish cutoff mismatch")
    created_at = str(payload.get("created_at") or "")
    created_match = re.match(
        r"^([0-9]{4}-[0-9]{2}-[0-9]{2})T"
        r"([0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.[0-9]+)?"
        r"(?:Z|[+-][0-9]{2}:[0-9]{2})$",
        created_at,
    )
    if not created_match or created_match.group(1) != payload.get("trade_date"):
        raise ValueError("snapshot request created_at date mismatch")
    if created_match.group(2) >= SNAPSHOT_PUBLISH_CUTOFF:
        raise ValueError("snapshot request created_at missed publish cutoff")
    if payload.get("account_type") != ACCOUNT_TYPE:
        raise ValueError("snapshot request account type mismatch")
    if payload.get("account_environment") != "REAL":
        raise ValueError("snapshot request must bind REAL environment")
    if ACCOUNT_ENVIRONMENT != "REAL" or ALLOW_REAL_MONEY is not True:
        raise ValueError("QMT runtime is not explicitly bound to REAL observation")
    if not ACCOUNT_ID:
        raise ValueError("QMT runtime account binding is missing")
    fingerprint = _snapshot_account_fingerprint(
        ACCOUNT_ID, ACCOUNT_TYPE, ACCOUNT_ENVIRONMENT,
    )
    if payload.get("account_fingerprint") != fingerprint:
        raise ValueError("snapshot request account fingerprint mismatch")
    if payload.get("account_id_masked") != _mask_account(ACCOUNT_ID):
        raise ValueError("snapshot request masked account mismatch")
    checksum = _snapshot_artifact_checksum(payload)
    if payload.get("checksum") != checksum or done_checksum != checksum:
        raise ValueError("snapshot request checksum mismatch")
    return checksum


def _snapshot_row_account_id(row):
    for field in (
            "m_strAccountID", "m_strAccountId", "account_id", "accountId"):
        value = getattr(row, field, None)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _snapshot_account_id_matches_runtime(observed):
    # A mask is display-only and is not a unique broker identity.  Trusted
    # preflight evidence requires the broker row's full ID to match exactly.
    return bool(observed) and observed == str(ACCOUNT_ID)


def _validate_snapshot_account_rows(accounts):
    rows = list(accounts or [])
    if len(rows) != 1:
        raise ValueError("broker ACCOUNT query must return exactly one row")
    observed = _snapshot_row_account_id(rows[0])
    if not observed:
        raise ValueError("broker ACCOUNT row has no account identity")
    if not _snapshot_account_id_matches_runtime(observed):
        raise ValueError("broker ACCOUNT row does not match runtime account")
    return rows[0]


def _validate_snapshot_position_identity(row):
    observed = _snapshot_row_account_id(row)
    if observed and not _snapshot_account_id_matches_runtime(observed):
        raise ValueError("broker POSITION row does not match runtime account")


def _snapshot_query_response(payload):
    """Query only ACCOUNT/POSITION. No trading API is reachable here."""
    observed_at = datetime.datetime.now().isoformat()
    account = None
    positions = []
    error = ""
    observed_account_masked = None
    observed_account_fingerprint = None
    account_rows = []
    try:
        account_rows = list(
            get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "ACCOUNT") or []
        )
        row = _validate_snapshot_account_rows(account_rows)
        observed_account_masked = _mask_account(ACCOUNT_ID)
        observed_account_fingerprint = _snapshot_account_fingerprint(
            ACCOUNT_ID, ACCOUNT_TYPE, ACCOUNT_ENVIRONMENT,
        )
        account = {
            "request_id": payload["request_id"],
            "account_id_masked": observed_account_masked,
            "account_fingerprint": observed_account_fingerprint,
            "available_cash": _opt_float(row, "m_dAvailable"),
            "total_asset": _opt_float(
                row, "m_dBalance", "m_dAssureAsset"),
            "market_value": _opt_float(
                row, "m_dInstrumentValue", "m_dStockValue"),
            "frozen_cash": _opt_float(row, "m_dFrozenCash"),
            "ts": observed_at,
        }
        raw_positions = get_trade_detail_data(
            ACCOUNT_ID, ACCOUNT_TYPE, "POSITION",
        )
        for row in raw_positions or []:
            _validate_snapshot_position_identity(row)
            shares = int(getattr(row, "m_nVolume", 0) or 0)
            if shares <= 0:
                continue
            positions.append({
                "request_id": payload["request_id"],
                "trade_date": payload["trade_date"],
                "stock_code": _qmt_stock_code(
                    getattr(row, "m_strInstrumentID", ""),
                    getattr(row, "m_strExchangeID", ""),
                ),
                "shares": shares,
                "can_use_volume": int(
                    getattr(row, "m_nCanUseVolume", 0) or 0),
                "frozen_shares": int(
                    getattr(row, "m_nFrozenVolume", 0) or 0),
                "avg_cost": _opt_float(
                    row, "m_dOpenPrice", "m_dPositionCost"),
                "market_value": _opt_float(row, "m_dMarketValue"),
                "ts": observed_at,
            })
    except Exception as exc:
        error = _bounded_text(exc, 512, (ACCOUNT_ID,))
        account = None
        positions = []
        observed_account_masked = None
        observed_account_fingerprint = None
        _log_event(
            "SNAPSHOT_ACCOUNT_IDENTITY_ERROR",
            severity="ERROR",
            account_row_count=len(account_rows),
            account_id_masked=_mask_account(ACCOUNT_ID),
            reason=error,
            message="broker snapshot account identity validation failed",
        )
    status = "ERROR" if error else "COMPLETE"
    response = {
        "type": "account_snapshot_response",
        "schema_version": payload["schema_version"],
        "request_id": payload["request_id"],
        "trade_date": payload["trade_date"],
        "collector_execution_profile": payload[
            "collector_execution_profile"],
        "collector_bridge_root": payload["collector_bridge_root"],
        "requested_for_strategy_id": payload[
            "requested_for_strategy_id"],
        "evidence_purpose": payload["evidence_purpose"],
        "publish_cutoff": payload["publish_cutoff"],
        "account_type": payload["account_type"],
        "account_environment": payload["account_environment"],
        "account_id_masked": observed_account_masked,
        "account_fingerprint": observed_account_fingerprint,
        "request_checksum": payload["checksum"],
        "status": status,
        "account": account,
        "positions": positions,
        "observed_at": observed_at,
        "error": error,
    }
    response["checksum"] = _snapshot_artifact_checksum(response)
    return response


def _persist_snapshot_response(response):
    request_id = response["request_id"]
    response_dir = _snapshot_request_dir("responses")
    json_path = os.path.join(
        response_dir, "response_" + request_id + ".json")
    done_path = os.path.join(
        response_dir, "response_" + request_id + ".done")
    encoded = json.dumps(response, ensure_ascii=True, sort_keys=True) + "\n"
    if os.path.isfile(json_path) or os.path.isfile(done_path):
        if os.path.isfile(json_path) and not os.path.isfile(done_path):
            with open(json_path, "r") as handle:
                if handle.read() != encoded:
                    raise ValueError("partial terminal snapshot response changed")
            tmp_done = done_path + ".tmp"
            with open(tmp_done, "w") as handle:
                handle.write(response["checksum"] + "\n")
                handle.flush()
            os.replace(tmp_done, done_path)
            return True
        if not (os.path.isfile(json_path) and os.path.isfile(done_path)):
            raise ValueError("partial terminal snapshot response exists")
        with open(json_path, "r") as handle:
            prior_json = handle.read()
        with open(done_path, "r") as handle:
            prior_done = handle.read().strip()
        if prior_json == encoded and prior_done == response["checksum"]:
            return False
        raise ValueError("terminal snapshot response conflicts")
    tmp_json = json_path + ".tmp"
    tmp_done = done_path + ".tmp"
    with open(tmp_json, "w") as handle:
        handle.write(encoded)
        handle.flush()
    with open(tmp_done, "w") as handle:
        handle.write(response["checksum"] + "\n")
        handle.flush()
    os.replace(tmp_json, json_path)
    os.replace(tmp_done, done_path)
    return True


def _archive_snapshot_request(json_path, done_path):
    archive = _snapshot_request_dir("archive")
    for path in (json_path, done_path):
        target = os.path.join(archive, os.path.basename(path))
        if os.path.isfile(target):
            with open(path, "rb") as current:
                current_bytes = current.read()
            with open(target, "rb") as prior:
                prior_bytes = prior.read()
            if current_bytes != prior_bytes:
                raise ValueError("archived snapshot request conflicts")
            os.remove(path)
        else:
            os.replace(path, target)


def _snapshot_request_already_terminal(payload, json_path, done_path):
    request_id = payload["request_id"]
    archive_json = os.path.join(
        _snapshot_request_dir("archive"),
        "request_" + request_id + ".json",
    )
    archive_done = os.path.join(
        _snapshot_request_dir("archive"),
        "request_" + request_id + ".done",
    )
    response_name = "response_" + request_id
    roots = (
        _snapshot_request_dir("responses"),
        _snapshot_request_dir("archive"),
    )
    response_json = next((
        os.path.join(root, response_name + ".json") for root in roots
        if os.path.isfile(os.path.join(root, response_name + ".json"))
    ), "")
    response_done = next((
        os.path.join(root, response_name + ".done") for root in roots
        if os.path.isfile(os.path.join(root, response_name + ".done"))
    ), "")
    request_archive_present = (
        os.path.isfile(archive_json) or os.path.isfile(archive_done)
    )
    if not response_json and not response_done and not request_archive_present:
        return False
    if not response_json or not response_done:
        raise ValueError("partial terminal snapshot response evidence")
    for current, archived in (
        (json_path, archive_json), (done_path, archive_done),
    ):
        if not os.path.isfile(archived):
            continue
        with open(current, "rb") as handle:
            current_bytes = handle.read()
        with open(archived, "rb") as handle:
            archived_bytes = handle.read()
        if current_bytes != archived_bytes:
            raise ValueError("terminal snapshot request replay changed")
    with open(response_json, "r") as handle:
        response = json.load(handle)
    with open(response_done, "r") as handle:
        response_marker = handle.read().strip()
    checksum = _snapshot_artifact_checksum(response)
    if response.get("checksum") != checksum or response_marker != checksum:
        raise ValueError("terminal snapshot response evidence is corrupt")
    if response.get("request_id") != request_id \
            or response.get("request_checksum") != payload.get("checksum"):
        raise ValueError("terminal snapshot response binding mismatch")
    return True


def _recover_partial_snapshot_response(payload):
    request_id = payload["request_id"]
    response_json = os.path.join(
        _snapshot_request_dir("responses"),
        "response_" + request_id + ".json",
    )
    response_done = response_json[:-5] + ".done"
    if not os.path.isfile(response_json) and not os.path.isfile(response_done):
        return None
    if os.path.isfile(response_done) and not os.path.isfile(response_json):
        raise ValueError("snapshot response marker exists without JSON")
    if os.path.isfile(response_done):
        return None
    with open(response_json, "r") as handle:
        response = json.load(handle)
    checksum = _snapshot_artifact_checksum(response)
    if response.get("checksum") != checksum:
        raise ValueError("partial snapshot response checksum mismatch")
    bindings = (
        "request_id", "trade_date", "collector_execution_profile",
        "collector_bridge_root", "requested_for_strategy_id",
        "evidence_purpose", "publish_cutoff", "account_type",
        "account_environment",
    )
    for field in bindings:
        if response.get(field) != payload.get(field):
            raise ValueError("partial snapshot response binding mismatch: " + field)
    if response.get("request_checksum") != payload.get("checksum"):
        raise ValueError("partial snapshot response request checksum mismatch")
    if response.get("status") == "COMPLETE":
        if response.get("account_id_masked") != payload.get(
                "account_id_masked"):
            raise ValueError("partial snapshot response account mismatch")
        if response.get("account_fingerprint") != payload.get(
                "account_fingerprint"):
            raise ValueError("partial snapshot response fingerprint mismatch")
    elif response.get("account_id_masked") is not None \
            or response.get("account_fingerprint") is not None:
        raise ValueError("non-complete snapshot response claims account identity")
    _persist_snapshot_response(response)
    return response


def _snapshot_processor_lock_path():
    return _path("snapshot_requests", "processor.lock")


def _snapshot_advance_gate_path():
    domain_root = (
        BRIDGE_ROOT if EXECUTION_PROFILE == "CLOSE_AUCTION"
        else OTHER_BRIDGE_ROOT
    )
    return os.path.join(domain_root, "state", SNAPSHOT_ADVANCE_GATE_NAME)


def _acquire_snapshot_advance_gate():
    path = _snapshot_advance_gate_path()
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    try:
        metadata = {
            "owner": "QMT_ORDER_ADVANCE",
            "strategy_name": STRATEGY_NAME,
            "execution_profile": EXECUTION_PROFILE,
            "created_at": datetime.datetime.now().isoformat(),
        }
        os.write(descriptor, (
            json.dumps(metadata, ensure_ascii=True, sort_keys=True) + "\n"
        ).encode("ascii"))
    finally:
        os.close(descriptor)
    return True


def _release_snapshot_advance_gate():
    os.remove(_snapshot_advance_gate_path())


def _snapshot_protocol_artifacts():
    artifacts = []
    scan_errors = []
    for directory in ("inbox", "processing", "archive", "responses"):
        root = _snapshot_request_dir(directory)
        try:
            names = os.listdir(root)
        except OSError:
            scan_errors.append(directory + "/<scan-error>")
            continue
        for name in names:
            if not (name.startswith("request_snapshot_")
                    or name.startswith("response_snapshot_")):
                continue
            if not (
                name.endswith(".json") or name.endswith(".done")
                or name.endswith(".json.tmp") or name.endswith(".done.tmp")
                or ".intent" in name
            ):
                continue
            artifacts.append(directory + "/" + name)
    return sorted(artifacts), sorted(scan_errors)


def _snapshot_artifact_request_id(relative_path):
    match = re.search(
        r"(snapshot_[0-9]{8}_[a-f0-9]{32})", relative_path,
    )
    return match.group(1) if match else relative_path


def _snapshot_archive_group_resolved(request_id, artifacts):
    expected = set((
        "archive/request_" + request_id + ".json",
        "archive/request_" + request_id + ".done",
        "archive/response_" + request_id + ".json",
        "archive/response_" + request_id + ".done",
    ))
    if set(artifacts) != expected:
        return False
    request_json = os.path.join(
        _snapshot_request_dir("archive"),
        "request_" + request_id + ".json",
    )
    request_done = request_json[:-5] + ".done"
    response_json = os.path.join(
        _snapshot_request_dir("archive"),
        "response_" + request_id + ".json",
    )
    response_done = response_json[:-5] + ".done"
    try:
        with open(request_json, "r") as handle:
            request = json.load(handle)
        with open(request_done, "r") as handle:
            request_marker = handle.read().strip()
        with open(response_json, "r") as handle:
            response = json.load(handle)
        with open(response_done, "r") as handle:
            response_marker = handle.read().strip()
        if not isinstance(request, dict) or not isinstance(response, dict):
            return False
        request_checksum = _snapshot_artifact_checksum(request)
        response_checksum = _snapshot_artifact_checksum(response)
    except Exception:
        return False
    if request.get("type") != "account_snapshot_request" \
            or request.get("schema_version") != _SNAPSHOT_REQUEST_SCHEMA \
            or request.get("request_id") != request_id \
            or request.get("checksum") != request_checksum \
            or request_marker != request_checksum:
        return False
    if response.get("type") != "account_snapshot_response" \
            or response.get("schema_version") != _SNAPSHOT_REQUEST_SCHEMA \
            or response.get("request_id") != request_id \
            or response.get("request_checksum") != request_checksum \
            or response.get("checksum") != response_checksum \
            or response_marker != response_checksum \
            or response.get("status") != "COMPLETE":
        return False
    bindings = (
        "trade_date", "collector_execution_profile", "collector_bridge_root",
        "requested_for_strategy_id", "evidence_purpose", "publish_cutoff",
        "account_type", "account_environment", "account_id_masked",
        "account_fingerprint",
    )
    if any(response.get(field) != request.get(field) for field in bindings):
        return False
    account = response.get("account")
    if not isinstance(account, dict) \
            or account.get("request_id") != request_id \
            or account.get("account_id_masked") != request.get(
                "account_id_masked") \
            or account.get("account_fingerprint") != request.get(
                "account_fingerprint"):
        return False
    if request.get("collector_execution_profile") != EXECUTION_PROFILE \
            or not _snapshot_root_matches(
                request.get("collector_bridge_root", "")):
        return False
    if not ACCOUNT_ID:
        return False
    expected_fingerprint = _snapshot_account_fingerprint(
        ACCOUNT_ID, ACCOUNT_TYPE, ACCOUNT_ENVIRONMENT,
    )
    if request.get("account_id_masked") != _mask_account(ACCOUNT_ID) \
            or request.get("account_fingerprint") != expected_fingerprint:
        return False
    return True


def _snapshot_protocol_state():
    artifacts, scan_errors = _snapshot_protocol_artifacts()
    if scan_errors:
        return {
            "state": "ERROR",
            "severity": "ERROR",
            "blocking": True,
            "classification": "PROTOCOL_SCAN_ERROR",
            "artifacts": sorted(artifacts + scan_errors),
        }
    groups = {}
    for artifact in artifacts:
        request_id = _snapshot_artifact_request_id(artifact)
        groups.setdefault(request_id, []).append(artifact)
    unresolved = []
    for request_id, group in groups.items():
        if not _snapshot_request_id_valid(request_id) \
                or not _snapshot_archive_group_resolved(request_id, group):
            unresolved.extend(group)
    unresolved.sort()
    if not unresolved:
        return {
            "state": "CLEAR",
            "severity": "INFO",
            "blocking": False,
            "classification": "CLEAR",
            "artifacts": [],
        }
    if any(path.startswith("responses/") for path in unresolved):
        classification = "PENDING_IMPORT"
    elif any(path.startswith("inbox/") or path.startswith("processing/")
             for path in unresolved):
        classification = "PENDING_REQUEST"
    else:
        classification = "INVALID_RESIDUE"
    return {
        "state": "ERROR",
        "severity": "ERROR",
        "blocking": True,
        "classification": classification,
        "artifacts": unresolved,
    }


def _persist_snapshot_protocol_state(status):
    payload = dict(status)
    payload["observed_at"] = datetime.datetime.now().isoformat()
    path = _path("snapshot_requests", "status.json")
    signature = (
        payload["state"], payload["classification"],
        tuple(payload["artifacts"]),
    )
    if signature == g.snapshot_residue_signature and os.path.isfile(path):
        return
    previous = g.snapshot_residue_signature
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as handle:
        handle.write(json.dumps(
            payload, ensure_ascii=True, sort_keys=True,
        ) + "\n")
        handle.flush()
    os.replace(tmp_path, path)
    g.snapshot_residue_signature = signature
    if payload["blocking"]:
        _log_event(
            "SNAPSHOT_RESIDUE_BLOCKED",
            severity="ERROR",
            classification=payload["classification"],
            artifacts=payload["artifacts"],
            artifact_count=len(payload["artifacts"]),
            message="snapshot protocol residue blocks order processing",
        )
    elif previous is not None and previous[0] == "ERROR":
        _log_event(
            "SNAPSHOT_RESIDUE_CLEARED",
            severity="INFO",
            message="snapshot protocol is clear for order processing",
        )


def _snapshot_residue_blocks_orders():
    status = _snapshot_protocol_state()
    return _persist_snapshot_state_or_block(status)


def _persist_snapshot_state_or_block(status):
    try:
        _persist_snapshot_protocol_state(status)
    except Exception as exc:
        g.snapshot_residue_signature = (
            "ERROR", "STATUS_WRITE_ERROR",
            ("snapshot_requests/status.json",),
        )
        _log(
            "snapshot protocol status write failed; orders remain blocked: %s"
            % _bounded_text(exc),
        )
        return True
    return bool(status["blocking"])


def _acquire_snapshot_processor_lock():
    path = _snapshot_processor_lock_path()
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        try:
            stale = time.time() - os.path.getmtime(path) > 300
        except OSError:
            return False
        if not stale:
            return False
        try:
            os.remove(path)
            descriptor = os.open(path, flags)
        except OSError:
            return False
    try:
        os.write(descriptor, datetime.datetime.now().isoformat().encode("ascii"))
    finally:
        os.close(descriptor)
    return True


def _process_snapshot_requests():
    if not _acquire_snapshot_processor_lock():
        # Another QMT worker (or a restart-stale lock) may be inside the
        # observation critical section. Fail closed for order processing.
        _persist_snapshot_state_or_block({
            "state": "ERROR",
            "severity": "ERROR",
            "blocking": True,
            "classification": "PROCESSOR_LOCKED",
            "artifacts": ["processor.lock"],
        })
        return True
    try:
        return _process_snapshot_requests_locked()
    finally:
        try:
            os.remove(_snapshot_processor_lock_path())
        except OSError:
            pass


def _process_snapshot_requests_locked():
    """Claim and finish snapshot-only work independently of order batches."""
    inbox = _snapshot_request_dir("inbox")
    processing = _snapshot_request_dir("processing")
    # Repair a crash between the two claim renames.
    for name in list(os.listdir(processing)):
        if name.endswith(".json"):
            counterpart = name[:-5] + ".done"
        elif name.endswith(".done"):
            counterpart = name[:-5] + ".json"
        else:
            continue
        source = os.path.join(inbox, counterpart)
        target = os.path.join(processing, counterpart)
        if os.path.isfile(source) and not os.path.isfile(target):
            os.replace(source, target)
        if not os.path.isfile(target):
            archived = os.path.join(
                _snapshot_request_dir("archive"), counterpart,
            )
            if os.path.isfile(archived):
                with open(archived, "rb") as handle:
                    archived_bytes = handle.read()
                with open(target, "wb") as handle:
                    handle.write(archived_bytes)
                    handle.flush()
    done_names = sorted(
        name for name in os.listdir(inbox)
        if name.startswith("request_snapshot_") and name.endswith(".done")
    )
    for done_name in done_names:
        json_name = done_name[:-5] + ".json"
        source_json = os.path.join(inbox, json_name)
        source_done = os.path.join(inbox, done_name)
        if not os.path.isfile(source_json):
            continue
        os.replace(source_json, os.path.join(processing, json_name))
        os.replace(source_done, os.path.join(processing, done_name))
    done_names = sorted(
        name for name in os.listdir(processing)
        if name.startswith("request_snapshot_") and name.endswith(".done")
    )
    for done_name in done_names:
        json_name = done_name[:-5] + ".json"
        json_path = os.path.join(processing, json_name)
        done_path = os.path.join(processing, done_name)
        if not os.path.isfile(json_path):
            continue
        request_id = json_name[len("request_"):-len(".json")]
        try:
            if os.path.getsize(json_path) > MAX_BATCH_BYTES:
                raise ValueError("snapshot request exceeds byte limit")
            with open(json_path, "rb") as handle:
                request_bytes = handle.read()
            with open(done_path, "rb") as handle:
                done_bytes = handle.read()
            payload = json.loads(request_bytes.decode("utf-8"))
            done_checksum = done_bytes.decode("utf-8").strip()
            if payload.get("request_id") != request_id:
                raise ValueError("snapshot request filename mismatch")
            _validate_snapshot_request(payload, done_checksum)
            _log_event(
                "SNAPSHOT_REQUEST_RECEIVED",
                request_id=request_id,
                trade_date=payload.get("trade_date", ""),
                collector_execution_profile=EXECUTION_PROFILE,
                requested_for_strategy_id=payload.get(
                    "requested_for_strategy_id", ""),
                account_id_masked=_mask_account(ACCOUNT_ID),
                bridge_root=BRIDGE_ROOT,
                message="snapshot-only request validated",
            )
            recovered_response = _recover_partial_snapshot_response(payload)
            if recovered_response is not None:
                _archive_snapshot_request(json_path, done_path)
                _log_event(
                    "SNAPSHOT_REQUEST_TERMINAL",
                    request_id=request_id,
                    status=recovered_response["status"],
                    response_checksum=recovered_response["checksum"],
                    response_persisted=True,
                    restart_recovered=True,
                    account_id_masked=_mask_account(ACCOUNT_ID),
                    position_count=len(recovered_response["positions"]),
                    message="partial snapshot response recovered after restart",
                )
                continue
            if _snapshot_request_already_terminal(
                    payload, json_path, done_path):
                _archive_snapshot_request(json_path, done_path)
                _log_event(
                    "SNAPSHOT_REQUEST_REPLAY",
                    request_id=request_id,
                    response_persisted=False,
                    account_id_masked=_mask_account(ACCOUNT_ID),
                    message="exact terminal snapshot request replay ignored",
                )
                continue
            response = _snapshot_query_response(payload)
            with open(json_path, "rb") as handle:
                if handle.read() != request_bytes:
                    raise ValueError("snapshot request changed during broker query")
            with open(done_path, "rb") as handle:
                if handle.read() != done_bytes:
                    raise ValueError("snapshot request marker changed during query")
            persisted = _persist_snapshot_response(response)
            _archive_snapshot_request(json_path, done_path)
            _log_event(
                "SNAPSHOT_REQUEST_TERMINAL",
                request_id=request_id,
                status=response["status"],
                response_checksum=response["checksum"],
                response_persisted=bool(persisted),
                account_id_masked=_mask_account(ACCOUNT_ID),
                position_count=len(response["positions"]),
                message="snapshot-only request completed",
            )
        except Exception as exc:
            _log_event(
                "SNAPSHOT_REQUEST_REJECTED",
                request_id=request_id,
                account_id_masked=_mask_account(ACCOUNT_ID),
                error_type=type(exc).__name__,
                reason=_bounded_text(exc, 512, (ACCOUNT_ID,)),
                message="snapshot-only request rejected",
            )
            # Retain processing evidence for inspection and restart; an exact
            # terminal replay will be harmless once the conflict is resolved.
            continue
    return _snapshot_residue_blocks_orders()

# ======================= QMT API wrappers =======================
# All QMT built-in API usage is isolated below so the pure logic above
# stays testable / reviewable.


def _account_id(batch):
    return ACCOUNT_ID or batch.header.get("account_id", "")


class _OrderQueryResult(dict):
    def __init__(self):
        dict.__init__(self)
        self.result_count = 0
        self.query_error = ""


def _get_orders_by_remark(account_id):
    """remark -> list of order detail objects.

    STAR board (688*) limit orders max 100k shares; QMT may split one
    passorder into multiple contract IDs that share the same remark
    (client_order_id). Callers must aggregate via _summarize_remark_orders.
    """
    result = _OrderQueryResult()
    try:
        details = get_trade_detail_data(account_id, ACCOUNT_TYPE, "ORDER")
    except Exception as exc:
        result.query_error = _bounded_text(exc)
        _log("get_trade_detail_data ORDER failed:\n" + traceback.format_exc())
        return result
    result.result_count = len(details or [])
    for d in details:
        remark = getattr(d, "m_strRemark", "")
        if remark:
            result.setdefault(remark, []).append(d)
    return result


def _summarize_remark_orders(details, requested_qty):
    """Aggregate child orders that share one remark.

    Returns None if there is nothing actionable yet.
    Otherwise a dict: traded, avg_price, qmt_order_id, fill_status, message.

    FILLED only when traded >= requested_qty (never trust a single child
    SUCCEEDED while the parent quantity is still short).
    """
    if not details:
        return None
    traded = 0
    amount = 0.0
    sysids = []
    statuses = []
    for d in details:
        vol = int(getattr(d, "m_nVolumeTraded", 0) or 0)
        price = float(getattr(d, "m_dTradedPrice", 0.0) or 0.0)
        traded += vol
        if vol > 0 and price > 0.0:
            amount += float(vol) * price
        sysid = getattr(d, "m_strOrderSysID", "") or ""
        if sysid and str(sysid) not in sysids:
            sysids.append(str(sysid))
        statuses.append(int(getattr(d, "m_nOrderStatus", -1)))

    avg_price = (amount / float(traded)) if traded > 0 else 0.0
    qmt_order_id = ",".join(sysids)
    req = int(requested_qty)
    all_terminal = all(s in TERMINAL_ORDER_STATUS for s in statuses)
    any_canceled = any(
        s in (STATUS_PART_CANCEL, STATUS_CANCELED) for s in statuses
    )
    all_junk = all(s == STATUS_JUNK for s in statuses)

    if traded >= req and traded > 0:
        return {
            "traded": traded,
            "avg_price": avg_price,
            "qmt_order_id": qmt_order_id,
            "fill_status": "FILLED",
            "message": "",
        }
    if all_terminal and any_canceled:
        if traded > 0:
            return {
                "traded": traded,
                "avg_price": avg_price,
                "qmt_order_id": qmt_order_id,
                "fill_status": "PARTIAL",
                "message": "canceled",
            }
        return {
            "traded": 0,
            "avg_price": 0.0,
            "qmt_order_id": qmt_order_id,
            "fill_status": "EXPIRED",
            "message": "canceled",
        }
    if all_terminal and all_junk:
        return {
            "traded": 0,
            "avg_price": 0.0,
            "qmt_order_id": qmt_order_id,
            "fill_status": "REJECTED",
            "message": "junk order",
        }
    # All visible children SUCCEEDED but still short of requested_qty: more
    # split children may still appear; keep non-terminal.
    if traded > 0:
        return {
            "traded": traded,
            "avg_price": avg_price,
            "qmt_order_id": qmt_order_id,
            "fill_status": "ACCEPTED",
            "message": "partial in progress",
        }
    return None


def _get_can_use_volume(account_id, stock_code):
    symbol = stock_code.split(".")[0]
    try:
        positions = get_trade_detail_data(account_id, ACCOUNT_TYPE, "POSITION")
    except Exception:
        _log("get_trade_detail_data POSITION failed:\n" + traceback.format_exc())
        return 0
    for p in positions:
        if getattr(p, "m_strInstrumentID", "") == symbol:
            return int(getattr(p, "m_nCanUseVolume", 0))
    return 0


def _get_available_cash(account_id):
    try:
        accounts = get_trade_detail_data(account_id, ACCOUNT_TYPE, "ACCOUNT")
    except Exception:
        _log("get_trade_detail_data ACCOUNT failed:\n" + traceback.format_exc())
        return None
    if not accounts:
        _log("ACCOUNT query returned no rows for account %s type %s"
             % (_mask_account(account_id), ACCOUNT_TYPE))
        return None
    account = accounts[0]
    returned_id = str(getattr(account, "m_strAccountID", "") or "")
    if returned_id and returned_id != str(account_id):
        _log("ACCOUNT query id mismatch: requested %s returned %s"
             % (_mask_account(account_id), _mask_account(returned_id)))
        return None
    raw = getattr(account, "m_dAvailable", None)
    try:
        available = float(raw)
    except (TypeError, ValueError):
        _log("ACCOUNT query missing available cash for account %s"
             % _mask_account(account_id))
        return None
    if not math.isfinite(available) or available < 0.0:
        _log("ACCOUNT query invalid available cash for account %s: %s"
             % (_mask_account(account_id), raw))
        return None
    return available


def _real_account_preflight(account_id):
    """Validate the first REAL rollout account before any passorder call."""
    if ACCOUNT_ENVIRONMENT != "REAL":
        return True, ""
    if not ALLOW_REAL_MONEY:
        return False, "ALLOW_REAL_MONEY is not enabled"
    if not ACCOUNT_ID or str(account_id) != str(ACCOUNT_ID):
        return False, "configured account id mismatch"
    try:
        accounts = get_trade_detail_data(account_id, ACCOUNT_TYPE, "ACCOUNT")
    except Exception:
        return False, "ACCOUNT query failed"
    if not accounts:
        return False, "ACCOUNT query returned no rows"
    account = accounts[0]
    returned_id = str(getattr(account, "m_strAccountID", "") or "")
    if returned_id != str(account_id):
        return False, "ACCOUNT query returned a different account id"
    try:
        available = float(getattr(account, "m_dAvailable", None))
    except (TypeError, ValueError):
        return False, "available cash is unavailable"
    if (not math.isfinite(available)
            or abs(available - REAL_EXPECTED_INITIAL_CASH)
            > REAL_INITIAL_CASH_TOLERANCE):
        return False, "available cash %.2f outside expected range" % available
    if REAL_REQUIRE_EMPTY_POSITIONS:
        try:
            positions = get_trade_detail_data(
                account_id, ACCOUNT_TYPE, "POSITION")
        except Exception:
            return False, "POSITION query failed"
        held = [p for p in (positions or [])
                if int(getattr(p, "m_nVolume", 0) or 0) > 0]
        if held:
            return False, "real account is not empty"
    return True, ""


def _positive_price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(price) or price <= 0.0:
        return 0.0
    return price


def _tick_field(tick, name, default=None):
    if isinstance(tick, dict):
        return tick.get(name, default)
    return getattr(tick, name, default)


def _get_tick(ContextInfo, stock_code):
    try:
        ticks = ContextInfo.get_full_tick([stock_code])
        return ticks.get(stock_code) if ticks else None
    except Exception:
        _log("get_full_tick failed for %s:\n%s"
             % (stock_code, traceback.format_exc()))
        return None


def _official_close(ContextInfo, stock_code):
    """Return current QMT lastPrice for target sizing."""
    tick = _get_tick(ContextInfo, stock_code)
    if tick is None:
        return 0.0
    return _positive_price(_tick_field(tick, "lastPrice"))


def _instrument_limit_price(ContextInfo, stock_code, side):
    getter = getattr(ContextInfo, "get_instrument_detail", None)
    if getter is None:
        getter = getattr(ContextInfo, "get_instrumentdetail", None)
    if getter is None:
        raise ValueError("instrument detail API unavailable")
    try:
        detail = getter(stock_code)
    except Exception:
        raise ValueError("instrument detail unavailable for %s" % stock_code)
    field = "UpStopPrice" if side == "BUY" else "DownStopPrice"
    raw = detail.get(field) if isinstance(detail, dict) else getattr(
        detail, field, None)
    price = _positive_price(raw)
    if price <= 0.0:
        raise ValueError("invalid %s limit price for %s" % (side, stock_code))
    return price


def _estimated_buy_cost(quantity, price):
    amount = float(quantity) * float(price)
    commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
    return amount + commission + amount * TRANSFER_FEE_RATE


def _max_affordable_quantity(cash, price, requested_qty):
    if cash <= 0 or price <= 0 or requested_qty < 100:
        return 0
    lots = min(int(requested_qty) // 100, int(float(cash) / float(price)) // 100)
    while lots > 0:
        quantity = lots * 100
        if _estimated_buy_cost(quantity, price) <= float(cash) + 1e-9:
            return quantity
        lots -= 1
    return 0


def _board_min_shares(stock_code):
    """Minimum single-order size for after-hours fixed-price trading.

    STAR Market (SH688*) requires at least 200 shares per buy order; the main
    board and ChiNext take any multiple of 100. Derived from the exchange rule,
    not from the broker, because a rejected order costs us the whole layer.
    """
    symbol = str(stock_code).split(".")[0]
    return 200 if symbol.startswith("688") else 100


def _ladder_buy_shares(target_value, close_price, lot=100):
    """Share count for one ladder layer, share-for-share equal to the backtest.

    Mirrors qlib/backtest/exchange.py round_amount_by_trade_unit:
        (deal_amount * factor + 0.1) // trade_unit * trade_unit / factor
    The backtest feeds it an adjusted price and multiplies the result back by
    factor to get real shares, and raw_close == adj_close / factor, so factor
    cancels out entirely and the raw close below is exact.

    The +0.1 is not cosmetic. Dropping it costs a full lot whenever
    target_value / close_price lands in [x*100 - 0.1, x*100): at V/C = 299.95
    this returns 300 while a plain floor returns 200.
    """
    if close_price <= 0 or target_value <= 0:
        return 0
    return int((float(target_value) / float(close_price) + 0.1) // lot) * lot


def _sized_buy_shares(stock_code, target_value, close_price, lot=100):
    """Final B: lot-rounded shares, zeroed when below the board minimum."""
    shares = _ladder_buy_shares(target_value, close_price, lot)
    if shares < _board_min_shares(stock_code):
        return 0
    return shares


def _target_requested_quantity(price, target_value):
    if price <= 0 or target_value <= 0:
        return 0
    return int(float(target_value) / float(price) / 100.0) * 100


def _target_buy_quantity(cash, price, target_value):
    requested = _target_requested_quantity(price, target_value)
    if cash is None:
        return requested
    return _max_affordable_quantity(cash, price, requested)


def _security_detail_evidence(ContextInfo, stock_code):
    getter = getattr(ContextInfo, "get_instrument_detail", None)
    if getter is None:
        getter = getattr(ContextInfo, "get_instrumentdetail", None)
    raw = {}
    error = ""
    if getter is None:
        error = "instrument detail API unavailable"
    else:
        try:
            detail = getter(stock_code)
            raw = _safe_detail(detail, [
                ("instrument_id", "InstrumentID", "m_strInstrumentID"),
                ("instrument_name", "InstrumentName", "m_strInstrumentName"),
                ("exchange_id", "ExchangeID", "m_strExchangeID"),
                ("product_id", "ProductID", "m_strProductID"),
                ("instrument_status", "InstrumentStatus", "Status"),
                ("is_suspended", "IsSuspended", "SuspendFlag"),
                ("after_hours_flag", "IsAfterHoursTrading",
                 "AfterHoursTrading", "FixedPriceTrading"),
                ("up_stop_price", "UpStopPrice"),
                ("down_stop_price", "DownStopPrice"),
                ("pre_close_price", "PreClose", "PreClosePrice",
                 "LastClose"),
            ])
        except Exception as exc:
            error = _bounded_text(exc)
    after_hours = raw.get("after_hours_flag")
    if isinstance(after_hours, bool):
        eligible = after_hours
    elif isinstance(after_hours, (int, float)):
        eligible = after_hours != 0
    elif isinstance(after_hours, str):
        normalized = after_hours.strip().lower()
        if normalized in ("1", "true", "yes", "y", "enabled"):
            eligible = True
        elif normalized in ("0", "false", "no", "n", "disabled"):
            eligible = False
        else:
            eligible = None
    else:
        eligible = None
    return {
        "raw_fields": raw,
        "after_hours_eligible": eligible,
        "detail_available": bool(raw),
        "error": error,
    }


def _market_price_evidence(ContextInfo, stock_code, official_close):
    collected_at = datetime.datetime.now().isoformat()
    tick = _get_tick(ContextInfo, stock_code)
    tick_fields = _safe_detail(tick or {}, [
        ("latest_price", "lastPrice"),
        ("pre_close_price", "lastClose", "preClose", "preClosePrice"),
        ("open_price", "open", "openPrice"),
        ("high_price", "high", "highPrice"),
        ("low_price", "low", "lowPrice"),
    ])
    close_value = _positive_price(official_close)
    if close_value <= 0.0:
        close_value = _positive_price(tick_fields.get("latest_price"))
    return {
        "collected_at": collected_at,
        "tick_fields": tick_fields,
        "official_close": close_value,
        "official_close_source": "lastPrice",
        "official_close_valid": close_value > 0.0,
    }


def _preorder_broker_evidence(account_id, stock_code):
    result = {
        "account_id_masked": _mask_account(account_id),
        "available_cash": None,
        "frozen_cash": None,
        "total_asset": None,
        "position_shares": None,
        "can_use_shares": None,
        "frozen_shares": None,
        "query_error": "",
    }
    try:
        accounts = get_trade_detail_data(account_id, ACCOUNT_TYPE, "ACCOUNT")
        if accounts:
            account = accounts[0]
            result["available_cash"] = _opt_float(account, "m_dAvailable")
            result["frozen_cash"] = _opt_float(account, "m_dFrozenCash")
            result["total_asset"] = _opt_float(
                account, "m_dBalance", "m_dAssureAsset",
            )
        symbol = stock_code.split(".")[0]
        positions = get_trade_detail_data(
            account_id, ACCOUNT_TYPE, "POSITION",
        )
        for position in positions or []:
            if str(getattr(position, "m_strInstrumentID", "")) != symbol:
                continue
            result["position_shares"] = int(
                getattr(position, "m_nVolume", 0) or 0,
            )
            result["can_use_shares"] = int(
                getattr(position, "m_nCanUseVolume", 0) or 0,
            )
            result["frozen_shares"] = int(
                getattr(position, "m_nFrozenVolume", 0) or 0,
            )
            break
    except Exception as exc:
        result["query_error"] = _bounded_text(exc)
    return result


def _sanitized_passorder_arguments(batch, order, op_type, api_price):
    return {
        "op_type": int(op_type),
        "order_type": 1101,
        "account_id_masked": _mask_account(_account_id(batch)),
        "stock_code": order["stock_code"],
        "price_type": int(LIMIT_PRICE_TYPE),
        "price": float(api_price),
        "quantity": int(order["quantity"]),
        "strategy_name": STRATEGY_NAME,
        "quick_trade": 2,
        "remark": order["client_order_id"],
    }


def _submit(
        ContextInfo, batch, order, live,
        limit_price=None, official_close=None):
    """Submit one order. Returns True if submitted (or simulated)."""
    coid = order["client_order_id"]
    if coid in batch.submitted:
        return True
    if not live:
        batch.submitted[coid] = True
        _save_active_state(batch)
        _write_fill(batch, order, "SKIPPED", 0, 0.0, "", "simulated")
        return True

    fixed_price = EXECUTION_PROFILE == "AFTER_HOURS_FIXED_PRICE"
    try:
        if fixed_price:
            if official_close is None:
                official_close = _official_close(
                    ContextInfo, order["stock_code"])
            official_close = _positive_price(official_close)
            if official_close <= 0.0:
                raise ValueError("official close unavailable")
            api_price = 0.0
        else:
            if limit_price is None:
                limit_price = _instrument_limit_price(
                    ContextInfo, order["stock_code"], order["side"])
            api_price = float(limit_price)
    except Exception as exc:
        batch.submitted[coid] = True
        _save_active_state(batch)
        _write_fill(batch, order, "ERROR", 0, 0.0, "", str(exc))
        _log_event(
            "PRICE_ERROR", batch_id=batch.batch_id(),
            client_order_id=coid, stock_code=order["stock_code"],
            message=str(exc),
        )
        return False

    op_type = 23 if order["side"] == "BUY" else 24
    account_id = _account_id(batch)
    security = _security_detail_evidence(ContextInfo, order["stock_code"])
    _log_event(
        "SECURITY_DETAIL",
        batch_id=batch.batch_id(),
        client_order_id=coid,
        stock_code=order["stock_code"],
        execution_profile=EXECUTION_PROFILE,
        raw_fields=security["raw_fields"],
        after_hours_eligible=security["after_hours_eligible"],
        detail_available=security["detail_available"],
        error=security["error"],
        message="security detail captured",
    )
    if fixed_price and security["after_hours_eligible"] is not True:
        message = "after-hours fixed-price eligibility not confirmed"
        batch.submitted[coid] = True
        _save_active_state(batch)
        _log_event(
            "SECURITY_ELIGIBILITY_ERROR",
            batch_id=batch.batch_id(),
            client_order_id=coid,
            stock_code=order["stock_code"],
            after_hours_eligible=security["after_hours_eligible"],
            detail_available=security["detail_available"],
            error=security["error"],
            message=message,
        )
        _write_fill(batch, order, "ERROR", 0, 0.0, "", message)
        return False
    market = _market_price_evidence(
        ContextInfo, order["stock_code"], official_close,
    )
    broker = _preorder_broker_evidence(account_id, order["stock_code"])
    estimated_cost = None
    if order["side"] == "BUY":
        reference_price = market["official_close"] or api_price
        if reference_price > 0.0:
            estimated_cost = _estimated_buy_cost(
                int(order["quantity"]), reference_price,
            )
    passorder_args = _sanitized_passorder_arguments(
        batch, order, op_type, api_price,
    )
    _log_event(
        "PREORDER_SNAPSHOT",
        batch_id=batch.batch_id(),
        client_order_id=coid,
        stock_code=order["stock_code"],
        side=order["side"],
        quantity=int(order["quantity"]),
        market=market,
        broker=broker,
        estimated_buy_cost=estimated_cost,
        passorder_arguments=passorder_args,
        message="pre-order evidence captured",
    )
    # Persist before passorder. On a crash, an uncertain order is never
    # submitted twice; the safer failure direction is a missed order.
    batch.submitted[coid] = True
    evidence = _evidence_for(batch, coid)
    evidence["attempt_started"] = time.time()
    evidence["passorder_arguments"] = passorder_args
    _save_active_state(batch)
    _log_event(
        "PASSORDER_ATTEMPT",
        batch_id=batch.batch_id(),
        client_order_id=coid,
        passorder_arguments=passorder_args,
        persisted_before_api=True,
        message="calling passorder",
    )
    api_started = time.time()
    try:
        # orderType=1101 single stock by shares; profile selects prType/price;
        # quickTrade=2 submit immediately; userOrderId -> m_strRemark
        api_return = passorder(
            op_type, 1101, account_id, order["stock_code"],
            LIMIT_PRICE_TYPE, api_price, int(order["quantity"]),
            STRATEGY_NAME, 2, coid, ContextInfo,
        )
        elapsed_ms = max(0.0, (time.time() - api_started) * 1000.0)
        safe_api_return = _sanitize_value(
            api_return, account_ids=(account_id,))
        return_repr = _bounded_text(
            repr(safe_api_return), 512, (account_id,))
        return_type = type(api_return).__name__
        evidence["api_returned"] = True
        evidence["api_return"] = {
            "return_repr": return_repr,
            "return_type": return_type,
            "elapsed_ms": elapsed_ms,
        }
        _save_active_state(batch)
        _log_event(
            "PASSORDER_RETURNED",
            batch_id=batch.batch_id(),
            client_order_id=coid,
            returned_normally=True,
            return_repr=return_repr,
            return_type=return_type,
            elapsed_ms=elapsed_ms,
            message="passorder returned normally",
        )
        event_fields = {
            "batch_id": batch.batch_id(),
            "client_order_id": coid,
            "side": order["side"],
            "stock_code": order["stock_code"],
            "quantity": int(order["quantity"]),
            "price_type": LIMIT_PRICE_TYPE,
            "limit_price": api_price,
            "passorder_return": evidence["api_return"],
            "message": "passorder returned; awaiting QMT order",
        }
        if fixed_price:
            event_fields["official_close_reference"] = official_close
        _log_event("SUBMITTED_UNCONFIRMED", **event_fields)
        if fixed_price:
            _log("passorder %s %s x%d prType=49 price=0 close=%s (%s)"
                 % (order["side"], order["stock_code"], order["quantity"],
                    official_close, coid))
        else:
            _log("passorder %s %s x%d prType=11 limit=%s (%s)"
                 % (order["side"], order["stock_code"], order["quantity"],
                    limit_price, coid))
        return True
    except Exception as exc:
        elapsed_ms = max(0.0, (time.time() - api_started) * 1000.0)
        evidence["api_returned"] = False
        evidence["api_return"] = {
            "exception_type": type(exc).__name__,
            "exception_message": _bounded_text(exc, account_ids=(account_id,)),
            "elapsed_ms": elapsed_ms,
        }
        _save_active_state(batch)
        _log_event(
            "PASSORDER_RETURNED",
            batch_id=batch.batch_id(),
            client_order_id=coid,
            returned_normally=False,
            return_repr="",
            return_type="",
            elapsed_ms=elapsed_ms,
            exception_type=type(exc).__name__,
            exception_message=_bounded_text(
                exc, account_ids=(account_id,)),
            traceback=_bounded_text(
                traceback.format_exc(), 512, (account_id,)),
            message="passorder raised an exception",
        )
        _write_fill(batch, order, "ERROR", 0, 0.0, "",
                    "passorder exception: "
                    + _bounded_text(exc, 200, (account_id,)))
        return False


def _cancel_by_detail(detail, account_id, ContextInfo):
    try:
        order_id = getattr(detail, "m_strOrderSysID", "")
        if order_id and can_cancel_order(order_id, account_id, ACCOUNT_TYPE):
            cancel(order_id, account_id, ACCOUNT_TYPE, ContextInfo)
    except Exception:
        _log("cancel failed:\n" + traceback.format_exc())

# ======================= per-poll processing =======================


def _order_is_terminal(batch, coid):
    fill = batch.fills.get(coid)
    return fill is not None and fill["status"] in (
        "FILLED", "PARTIAL", "REJECTED", "SKIPPED", "EXPIRED", "ERROR")


def _order_detail_evidence(detail, mapped_remark, match_kind):
    fields = _safe_detail(detail, [
        ("remark", "m_strRemark", "userOrderId", "orderRemark"),
        ("order_id", "m_strOrderSysID", "orderID", "orderId"),
        ("contract_id", "m_strOrderID", "contractID"),
        ("status_code", "m_nOrderStatus", "orderStatus"),
        ("status_text", "m_strOrderStatus", "orderStatusText"),
        ("order_price", "m_dOrderPrice", "orderPrice"),
        ("order_quantity", "m_nOrderVolume", "orderVolume"),
        ("traded_quantity", "m_nVolumeTraded", "tradedVolume"),
        ("canceled_quantity", "m_nVolumeCanceled", "canceledVolume"),
        ("traded_price", "m_dTradedPrice", "tradedPrice"),
        ("error", "m_strErrorMsg", "errorMsg", "errorMessage"),
        ("stock_code", "m_strInstrumentID", "orderCode"),
        ("exchange_id", "m_strExchangeID", "exchangeID"),
        ("op_type", "m_nOpType", "opType"),
    ])
    fields.setdefault("remark", str(mapped_remark or ""))
    fields["match_kind"] = match_kind
    return fields


def _candidate_order_details(details_by_remark, coid):
    candidates = []
    exact_details = details_by_remark.get(coid) or []
    for detail in exact_details:
        candidates.append(_order_detail_evidence(detail, coid, "exact"))
    for remark, details in details_by_remark.items():
        remark_text = str(remark or "")
        if remark_text == coid:
            continue
        suspected = bool(
            remark_text and (coid in remark_text or remark_text in coid)
        )
        if not suspected:
            continue
        for detail in details or []:
            candidates.append(
                _order_detail_evidence(detail, remark_text, "suspected")
            )
    return exact_details, candidates


def _real_order_ids(details):
    result = []
    for detail in details or []:
        order_id = _callback_value(
            detail, "m_strOrderSysID", "orderID", "orderId",
        )
        if order_id not in (None, "") and str(order_id) not in result:
            result.append(str(order_id))
    return result


def _poll_status(batch, details=None):
    """Update fills from broker order details (LIVE only)."""
    live = batch.execution_live and batch.broker_authorized
    if not live:
        return
    if details is None:
        details = _get_orders_by_remark(_account_id(batch))
    mapped_count = sum(len(rows or []) for rows in details.values())
    result_count = int(getattr(details, "result_count", mapped_count))
    query_error = str(getattr(details, "query_error", "") or "")
    for order in batch.orders:
        coid = order["client_order_id"]
        if coid not in batch.submitted or _order_is_terminal(batch, coid):
            continue
        evidence = _evidence_for(batch, coid)
        evidence["query_count"] = int(evidence.get("query_count", 0)) + 1
        attempt_started = evidence.get("attempt_started")
        try:
            elapsed_ms = max(
                0.0, (time.time() - float(attempt_started)) * 1000.0,
            )
        except (TypeError, ValueError):
            elapsed_ms = 0.0
        exact_details, candidates = _candidate_order_details(details, coid)
        _save_active_state(batch)
        _log_event(
            "ORDER_QUERY",
            batch_id=batch.batch_id(),
            client_order_id=coid,
            query_number=evidence["query_count"],
            elapsed_ms_since_attempt=elapsed_ms,
            result_count=result_count,
            match_count=len(exact_details),
            suspected_match_count=len([
                row for row in candidates
                if row.get("match_kind") == "suspected"
            ]),
            candidates=candidates,
            query_error=query_error,
            message="broker ORDER query completed",
        )
        order_ids = _real_order_ids(exact_details)
        if not order_ids:
            _log_event(
                "ORDER_NOT_OBSERVED",
                batch_id=batch.batch_id(),
                client_order_id=coid,
                query_number=evidence["query_count"],
                elapsed_ms_since_attempt=elapsed_ms,
                result_count=result_count,
                match_count=len(exact_details),
                message="client order remark has no real QMT order id",
            )
            continue
        if not evidence.get("order_observed", False):
            evidence["order_observed"] = True
            evidence["qmt_order_ids"] = order_ids
            _save_active_state(batch)
            _log_event(
                "ORDER_OBSERVED",
                batch_id=batch.batch_id(),
                client_order_id=coid,
                qmt_order_ids=order_ids,
                query_number=evidence["query_count"],
                elapsed_ms_since_attempt=elapsed_ms,
                source="ORDER_QUERY",
                message="real QMT order id observed",
            )
            _write_fill(
                batch, order, "ACCEPTED", 0, 0.0,
                ",".join(order_ids), "broker order observed",
            )

        status_signature = [
            "%s:%s" % (
                row.get("order_id", ""), row.get("status_code", ""),
            )
            for row in candidates if row.get("match_kind") == "exact"
        ]
        if status_signature != evidence.get("last_broker_statuses", []):
            previous = evidence.get("last_broker_statuses", [])
            evidence["last_broker_statuses"] = status_signature
            _save_active_state(batch)
            _log_event(
                "ORDER_STATUS_CHANGED",
                batch_id=batch.batch_id(),
                client_order_id=coid,
                previous_status=previous,
                status=status_signature,
                candidates=candidates,
                message="broker order status changed",
            )
        summary = _summarize_remark_orders(
            exact_details, order["quantity"],
        )
        if summary is None:
            continue
        _write_fill(
            batch, order,
            summary["fill_status"],
            summary["traded"],
            summary["avg_price"],
            summary["qmt_order_id"],
            summary["message"],
        )


def _finalize_dual_authorization_block(batch):
    batch.execution_authorized = False
    batch.execution_live = False
    _save_active_state(batch)
    trade_date = batch.header.get("trade_date", "")
    authorization_path = _authorization_path(trade_date)
    other_authorization_path = _other_authorization_path(trade_date)
    _log_event(
        "DUAL_AUTHORIZATION_BLOCKED",
        batch_id=batch.batch_id(),
        execution_profile=EXECUTION_PROFILE,
        authorization_path=authorization_path,
        other_authorization_path=other_authorization_path,
        message="both execution profiles are authorized; all trading disabled",
    )
    for order in batch.orders:
        coid = order["client_order_id"]
        if _order_is_terminal(batch, coid):
            continue
        batch.submitted[coid] = True
        _save_active_state(batch)
        _write_fill(
            batch, order, "SKIPPED", 0, 0.0, "",
            "dual authorization blocked",
        )
    _finalize_batch(batch)


def _process_batch(ContextInfo, batch):
    if batch.dual_authorization_blocked:
        _finalize_dual_authorization_block(batch)
        return
    if not batch.orders:
        _finalize_batch(batch)
        return
    now = _now_hms()
    if now < TRADE_START:
        return
    if now >= CANCEL_AT:
        # _force_finalize_if_near_close owns polling/cancel from this point.
        # Never place a fresh order after the cancellation cutoff.
        return
    if not batch.trading_started:
        # batch may have been claimed hours before the trade window opens;
        # freeze both the sell-wait timer and LIVE safety decision at the
        # first trading pass. A late LIVE_OK file cannot enable half a batch.
        batch.trading_started = True
        batch.phase_started = time.time()
        trade_date = batch.header.get("trade_date", "")
        # Legacy marker files are ignored.  Account/runtime selection happens
        # in the QMT strategy instance, not in the publisher's inbox.
        batch.dual_authorization_blocked = False
        if batch.dual_authorization_blocked:
            batch.execution_authorized = False
            batch.execution_live = False
            _save_active_state(batch)
            _finalize_dual_authorization_block(batch)
            return
        # The publisher is execution-neutral.  Whether this instance sends
        # orders to a paper or real account is selected in QMT itself.
        batch.execution_authorized = batch.broker_authorized
        if batch.execution_authorized and ACCOUNT_ENVIRONMENT == "REAL":
            preflight_ok, preflight_message = _real_account_preflight(
                _account_id(batch))
            if not preflight_ok:
                batch.broker_authorized = False
                batch.execution_authorized = False
                batch.execution_live = False
                for order in batch.orders:
                    coid = order["client_order_id"]
                    batch.submitted[coid] = True
                    _write_fill(
                        batch, order, "SKIPPED", 0, 0.0, "",
                        "REAL preflight failed: " + preflight_message,
                    )
                _save_active_state(batch)
                _finalize_batch(batch)
                return
        batch.execution_live = batch.execution_authorized
        _save_active_state(batch)

    mode_live = batch.execution_authorized

    account_id = _account_id(batch)
    sells = [o for o in batch.orders if o["side"] == "SELL"]
    buys = [o for o in batch.orders if o["side"] == "BUY"]

    if batch.phase == "SELL":
        for order in sells:
            if order["client_order_id"] in batch.submitted:
                continue
            if mode_live:
                can_use = _get_can_use_volume(account_id, order["stock_code"])
                if can_use < order["quantity"]:
                    if can_use >= 100:
                        order["quantity"] = (can_use // 100) * 100
                        _log("shrink sell %s to can_use %d"
                             % (order["stock_code"], order["quantity"]))
                    else:
                        _write_fill(batch, order, "SKIPPED", 0, 0.0, "",
                                    "insufficient sellable volume: %d" % can_use)
                        batch.submitted[order["client_order_id"]] = True
                        _save_active_state(batch)
                        continue
                if (MAX_ORDER_QUANTITY > 0
                        and order["quantity"] > MAX_ORDER_QUANTITY):
                    order["quantity"] = (
                        int(MAX_ORDER_QUANTITY) // 100
                    ) * 100
                    _log("rollout gate shrinks sell %s to %d shares"
                         % (order["stock_code"], order["quantity"]))
            _submit(ContextInfo, batch, order, mode_live)

        _poll_status(batch)
        sells_done = all(_order_is_terminal(batch, o["client_order_id"])
                         for o in sells) if sells else True
        wait_elapsed = (
            time.time() - batch.phase_started
        ) >= SELL_WAIT_TIMEOUT_SEC
        if not sells or wait_elapsed:
            batch.phase = "BUY"
            batch.phase_started = time.time()
            _save_active_state(batch)
            if not sells_done:
                _log("sell phase timeout, starting buys with actual cash")

    if batch.phase == "BUY":
        if mode_live and batch.remaining_cash is None:
            cash = _get_available_cash(account_id)
            if cash is None:
                return
            batch.remaining_cash = cash
            _save_active_state(batch)
        for order in buys:
            if order["client_order_id"] in batch.submitted:
                continue
            close_price = _official_close(ContextInfo, order["stock_code"])
            target_requested = _target_requested_quantity(
                close_price, float(order["target_value"]))
            if close_price <= 0.0:
                order["quantity"] = 100
                batch.submitted[order["client_order_id"]] = True
                _write_fill(batch, order, "ERROR", 0, 0.0, "",
                            "official close unavailable")
                continue
            if target_requested <= 0:
                order["quantity"] = 100
                batch.submitted[order["client_order_id"]] = True
                _write_fill(batch, order, "SKIPPED", 0, 0.0, "",
                            "target_value below one board lot")
                continue

            limit_price = None
            reservation_price = close_price
            if mode_live:
                if EXECUTION_PROFILE == "CLOSE_AUCTION":
                    try:
                        limit_price = _instrument_limit_price(
                            ContextInfo, order["stock_code"], "BUY")
                    except Exception as exc:
                        order["quantity"] = target_requested
                        batch.submitted[order["client_order_id"]] = True
                        _save_active_state(batch)
                        _write_fill(
                            batch, order, "ERROR", 0, 0.0, "", str(exc),
                        )
                        continue
                    reservation_price = limit_price
                requested = _target_requested_quantity(
                    close_price, float(order["target_value"]))
                quantity = _max_affordable_quantity(
                    batch.remaining_cash, reservation_price, requested)
            else:
                quantity = _target_buy_quantity(
                    None, close_price, float(order["target_value"]))
            if mode_live and MAX_ORDER_QUANTITY > 0:
                quantity = min(
                    quantity, (int(MAX_ORDER_QUANTITY) // 100) * 100,
                )
            authorized_max = int(order.get("max_quantity", 0) or 0)
            if authorized_max > 0:
                quantity = min(quantity, authorized_max)
            if quantity <= 0:
                order["quantity"] = (
                    min(target_requested, authorized_max)
                    if authorized_max > 0 else target_requested
                )
                batch.submitted[order["client_order_id"]] = True
                _save_active_state(batch)
                _write_fill(batch, order, "SKIPPED", 0, 0.0, "",
                            "insufficient actual cash: %.2f"
                            % batch.remaining_cash)
                continue
            order["quantity"] = quantity
            if mode_live:
                reserved = _estimated_buy_cost(quantity, reservation_price)
                batch.remaining_cash = max(0.0, batch.remaining_cash - reserved)
                _save_active_state(batch)
                if EXECUTION_PROFILE == "AFTER_HOURS_FIXED_PRICE":
                    _submit(
                        ContextInfo, batch, order, True,
                        official_close=close_price,
                    )
                else:
                    _submit(
                        ContextInfo, batch, order, True,
                        limit_price=limit_price,
                    )
            else:
                _submit(ContextInfo, batch, order, False)

        _poll_status(batch)
        all_done = all(_order_is_terminal(batch, o["client_order_id"])
                       for o in batch.orders)
        if all_done:
            _finalize_batch(batch)


def _force_finalize_if_near_close(ContextInfo, batch):
    if batch.dual_authorization_blocked:
        _finalize_dual_authorization_block(batch)
        return
    now = _now_hms()
    if now < CANCEL_AT:
        return
    # LIVE_OK gates *new* submissions only. Once a LIVE order was submitted,
    # removing the switch must not disable status polling or close-time cancel.
    if batch.execution_live and batch.broker_authorized:
        details = _get_orders_by_remark(_account_id(batch))
        for order in batch.orders:
            coid = order["client_order_id"]
            if coid in batch.submitted and not _order_is_terminal(batch, coid):
                for d in details.get(coid) or []:
                    status = int(getattr(d, "m_nOrderStatus", -1))
                    if status not in TERMINAL_ORDER_STATUS:
                        _cancel_by_detail(d, _account_id(batch), ContextInfo)
        _poll_status(batch, details)

    if now >= FINALIZE_AT:
        cash_unavailable = (
            batch.phase == "BUY" and batch.remaining_cash is None
        )
        for order in batch.orders:
            coid = order["client_order_id"]
            if not _order_is_terminal(batch, coid):
                fill = batch.fills.get(coid)
                evidence = _evidence_for(batch, coid)
                observed = bool(evidence.get("order_observed", False))
                traded = fill["filled_qty"] if fill else 0
                price = fill["avg_price"] if fill else 0.0
                if (cash_unavailable and order["side"] == "BUY"
                        and coid not in batch.submitted):
                    _write_fill(batch, order, "ERROR", 0, 0.0, "",
                                "account cash unavailable at close")
                elif traded > 0:
                    _write_fill(batch, order, "PARTIAL", traded, price, "",
                                "expired at close")
                elif coid in batch.submitted and not observed:
                    _write_fill(
                        batch, order, "ERROR", 0, 0.0, "",
                        "QMT order not observed after passorder",
                    )
                elif coid in batch.submitted and observed:
                    _write_fill(
                        batch, order, "ERROR", 0, 0.0,
                        ",".join(evidence.get("qmt_order_ids", [])),
                        "QMT order observed but final status unavailable at close",
                    )
                else:
                    _write_fill(batch, order, "EXPIRED", 0, 0.0, "",
                                "expired at close")
        _finalize_batch(batch)

# ======================= QMT entry points =======================


def _advance(ContextInfo):
    if not g.trading_enabled:
        return
    if not _acquire_snapshot_advance_gate():
        _persist_snapshot_state_or_block({
            "state": "ERROR",
            "severity": "ERROR",
            "blocking": True,
            "classification": "ADVANCE_GATE_BUSY",
            "artifacts": ["state/" + SNAPSHOT_ADVANCE_GATE_NAME],
        })
        return
    try:
        _advance_with_snapshot_gate(ContextInfo)
    finally:
        _release_snapshot_advance_gate()


def _advance_with_snapshot_gate(ContextInfo):
    if _process_snapshot_requests():
        # A snapshot-only wakeup is observation-exclusive: do not claim or
        # execute any order batch in the same dynamic entry invocation.
        return
    if g.batch is not None and g.batch.dual_authorization_blocked:
        _finalize_dual_authorization_block(g.batch)
        return
    now = time.time()
    if now - g.last_poll < POLL_SECONDS:
        return
    g.last_poll = now

    _recover_processing_batch()
    _claim_new_batch()
    if g.batch is not None and g.batch.dual_authorization_blocked:
        _finalize_dual_authorization_block(g.batch)
        return
    if g.batch is not None:
        _force_finalize_if_near_close(ContextInfo, g.batch)
    if g.batch is not None:
        _process_batch(ContextInfo, g.batch)
    _refresh_account_snapshots_after_close()


def timer_callback(ContextInfo):
    """Timer-driven path; continues after the last market tick."""
    try:
        if not g.loaded:
            init(ContextInfo)
        _advance(ContextInfo)
    except Exception:
        _log("timer_callback error:\n" + traceback.format_exc())


def _bind_context_account(ContextInfo, event_name):
    if not ACCOUNT_ID:
        return
    ContextInfo.set_account(ACCOUNT_ID)
    _log_event(
        event_name,
        account_id_masked=_mask_account(ACCOUNT_ID),
        message="QMT account callback binding enabled",
    )


def _init_snapshot_observer(ContextInfo):
    """Initialize only observation resources; never initialize order state."""
    if g.snapshot_observer_loaded:
        return g.snapshot_observer_enabled
    _ensure_snapshot_dirs()
    try:
        profile = _profile_settings()
        _validate_profile_roots()
    except ValueError as exc:
        g.snapshot_observer_loaded = True
        g.snapshot_observer_enabled = False
        _log_event(
            "SNAPSHOT_OBSERVER_CONFIG_ERROR",
            severity="ERROR",
            execution_profile=EXECUTION_PROFILE,
            bridge_root=BRIDGE_ROOT,
            other_bridge_root=OTHER_BRIDGE_ROOT,
            message=str(exc),
        )
        return False
    _bind_context_account(ContextInfo, "SNAPSHOT_ACCOUNT_BOUND")
    g.snapshot_observer_loaded = True
    g.snapshot_observer_enabled = True
    _log_event(
        "SNAPSHOT_OBSERVER_CONFIG",
        account_id_masked=_mask_account(ACCOUNT_ID),
        account_binding_configured=bool(ACCOUNT_ID),
        account_type=ACCOUNT_TYPE,
        account_environment=ACCOUNT_ENVIRONMENT,
        allow_real_money=bool(ALLOW_REAL_MONEY),
        execution_profile=EXECUTION_PROFILE,
        bridge_root=BRIDGE_ROOT,
        signal_price_type=profile["signal_price_type"],
        message="snapshot-only observer runtime configuration",
    )
    return True


def snapshot_timer_callback(ContextInfo):
    """Read-only observer timer; it never calls the order state machine."""
    try:
        if not g.snapshot_observer_loaded:
            _init_snapshot_observer(ContextInfo)
        if g.snapshot_observer_enabled:
            _process_snapshot_requests()
    except Exception:
        _log("snapshot_timer_callback error:\n" + traceback.format_exc())


def _register_snapshot_timer(ContextInfo):
    if g.snapshot_timer_registered:
        return
    day = datetime.date.today()
    first_compact = (
        day.strftime("%Y%m%d") + SNAPSHOT_OBSERVER_START.replace(":", "")
    )
    g.snapshot_timer_registered = True
    method = "schedule_run" if hasattr(ContextInfo, "schedule_run") else "run_time"
    first_wakeup = first_compact
    timer_result = None
    try:
        if hasattr(ContextInfo, "schedule_run"):
            timer_result = ContextInfo.schedule_run(
                snapshot_timer_callback,
                first_compact,
                -1,
                datetime.timedelta(seconds=POLL_SECONDS),
                "qlib_snapshot_observer",
            )
        else:
            first_legacy = (
                day.strftime("%Y-%m-%d") + " " + SNAPSHOT_OBSERVER_START
            )
            first_wakeup = first_legacy
            timer_result = ContextInfo.run_time(
                "snapshot_timer_callback",
                "%dnSecond" % int(POLL_SECONDS),
                first_legacy,
            )
        _log_event(
            "SNAPSHOT_TIMER_REGISTERED",
            method=method,
            registered=True,
            first_wakeup=first_wakeup,
            interval_seconds=int(POLL_SECONDS),
            callback="snapshot_timer_callback",
            timer_name="qlib_snapshot_observer",
            return_repr=_bounded_text(repr(timer_result), 256),
            message="snapshot-only observer timer registered",
        )
    except Exception as exc:
        g.snapshot_timer_registered = False
        _log_event(
            "SNAPSHOT_TIMER_REGISTERED",
            method=method,
            registered=False,
            first_wakeup=first_wakeup,
            interval_seconds=int(POLL_SECONDS),
            callback="snapshot_timer_callback",
            error_type=type(exc).__name__,
            error_message=_bounded_text(exc),
            message="snapshot-only observer timer registration failed",
        )
        raise


def _register_postclose_timer(ContextInfo):
    if g.timer_registered:
        return
    day = datetime.date.today()
    timer_start = _profile_settings()["timer_start"]
    first_compact = day.strftime("%Y%m%d") + timer_start.replace(":", "")
    # Some QMT builds invoke an overdue callback synchronously while the
    # timer is registered. Mark it first so callback -> init cannot recurse.
    g.timer_registered = True
    method = "schedule_run" if hasattr(ContextInfo, "schedule_run") else "run_time"
    first_wakeup = first_compact
    timer_result = None
    try:
        if hasattr(ContextInfo, "schedule_run"):
            timer_result = ContextInfo.schedule_run(
                timer_callback,
                first_compact,
                -1,
                datetime.timedelta(seconds=POLL_SECONDS),
                "qlib_postclose_poll",
            )
        else:
            first_legacy = day.strftime("%Y-%m-%d") + " " + timer_start
            first_wakeup = first_legacy
            timer_result = ContextInfo.run_time(
                "timer_callback", "%dnSecond" % int(POLL_SECONDS), first_legacy,
            )
        _log_event(
            "TIMER_REGISTERED",
            method=method,
            registered=True,
            first_wakeup=first_wakeup,
            interval_seconds=int(POLL_SECONDS),
            callback="timer_callback",
            timer_name="qlib_postclose_poll",
            return_repr=_bounded_text(repr(timer_result), 256),
            message="post-close timer registered",
        )
    except Exception as exc:
        g.timer_registered = False
        _log_event(
            "TIMER_REGISTERED",
            method=method,
            registered=False,
            first_wakeup=first_wakeup,
            interval_seconds=int(POLL_SECONDS),
            callback="timer_callback",
            error_type=type(exc).__name__,
            error_message=_bounded_text(exc),
            message="post-close timer registration failed",
        )
        raise


def _source_evidence():
    source_path = globals().get("__file__", "")
    source_sha = ""
    try:
        import hashlib
        digest = hashlib.sha256()
        with open(source_path, "rb") as source_file:
            while True:
                chunk = source_file.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
        source_sha = "sha256:" + digest.hexdigest()
    except Exception:
        source_sha = ""
    return SOURCE_VERSION, source_sha


def _context_version(ContextInfo):
    for name in (
            "qmt_version", "client_version", "version", "get_qmt_version",
            "get_client_version"):
        value = getattr(ContextInfo, name, None)
        if value in (None, ""):
            continue
        try:
            if callable(value):
                value = value()
        except Exception:
            continue
        if value not in (None, ""):
            return _bounded_text(value, 128)
    return "unavailable"


def init(ContextInfo):
    _ensure_dirs()
    try:
        _activate_profile_settings()
    except ValueError as exc:
        g.loaded = True
        g.trading_enabled = False
        _log_event(
            "INVALID_EXECUTION_PROFILE",
            execution_profile=EXECUTION_PROFILE,
            message=str(exc),
        )
        return
    try:
        _validate_profile_roots()
    except ValueError as exc:
        g.loaded = True
        g.trading_enabled = False
        _log_event(
            "PROFILE_ISOLATION_ERROR",
            execution_profile=EXECUTION_PROFILE,
            bridge_root=BRIDGE_ROOT,
            other_bridge_root=OTHER_BRIDGE_ROOT,
            message=str(exc),
        )
        return
    g.trading_enabled = True
    source_version, source_sha = _source_evidence()
    profile = _profile_settings()
    _log_event(
        "RUNTIME_CONFIG",
        source_version=source_version,
        source_file=os.path.basename(globals().get("__file__", "")),
        source_sha256=source_sha,
        strategy_name=STRATEGY_NAME,
        qmt_version=_context_version(ContextInfo),
        account_id_masked=_mask_account(ACCOUNT_ID),
        account_binding_configured=bool(ACCOUNT_ID),
        account_type=ACCOUNT_TYPE,
        account_environment=ACCOUNT_ENVIRONMENT,
        execution_profile=EXECUTION_PROFILE,
        bridge_root=BRIDGE_ROOT,
        other_bridge_root=OTHER_BRIDGE_ROOT,
        signal_price_type=profile["signal_price_type"],
        qmt_price_type=int(profile["qmt_price_type"]),
        max_order_quantity=int(MAX_ORDER_QUANTITY),
        max_orders_per_batch=int(MAX_ORDERS_PER_BATCH),
        poll_seconds=int(POLL_SECONDS),
        sell_wait_seconds=int(SELL_WAIT_TIMEOUT_SEC),
        submit_after=TRADE_START,
        cancel_at=CANCEL_AT,
        finalize_at=FINALIZE_AT,
        snapshot_after=SNAPSHOT_REFRESH_AT,
        snapshot_observer_start=SNAPSHOT_OBSERVER_START,
        timer_start=profile["timer_start"],
        authorization_path=_authorization_path(_today()),
        authorization_present=os.path.isfile(_authorization_path(_today())),
        other_authorization_path=_other_authorization_path(_today()),
        other_authorization_present=_other_profile_authorized(_today()),
        message="QMT bridge runtime configuration",
    )
    _load_processed()
    _recover_processing_batch()
    g.loaded = True
    try:
        _init_snapshot_observer(ContextInfo)
        _register_snapshot_timer(ContextInfo)
        _register_postclose_timer(ContextInfo)
    except Exception:
        g.loaded = False
        g.trading_enabled = False
        raise
    _log("initialized, bridge_root=%s, %d processed batches"
         % (BRIDGE_ROOT, len(g.processed)))
    _log_event(
        "START", bridge_root=BRIDGE_ROOT,
        message="qmt bridge initialized",
    )


def _callback_value(obj, *names):
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return ""


def _callback_int(obj, *names):
    try:
        return int(_callback_value(obj, *names) or 0)
    except (TypeError, ValueError):
        return 0


def _callback_float(obj, *names):
    try:
        return float(_callback_value(obj, *names) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _callback_remark(obj):
    remark = str(_callback_value(
        obj, "userOrderId", "m_strRemark", "orderRemark",
    ))
    strategy = str(_callback_value(
        obj, "strategyName", "m_strStrategyName",
    ))
    if not remark and "&&&_" in strategy:
        remark = strategy.rsplit("&&&_", 1)[-1]
    return remark


def _find_callback_order(batch, remark, code):
    if remark:
        for order in batch.orders:
            if order["client_order_id"] == remark:
                return order
        return None
    candidates = []
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    for order in batch.orders:
        if digits and order["stock_code"].split(".")[0] == digits:
            candidates.append(order)
    return candidates[0] if len(candidates) == 1 else None


def _callback_association_fields(batch, order, raw_remark):
    return {
        "associated": order is not None,
        "batch_id": batch.batch_id() if batch is not None and order is not None
        else "",
        "client_order_id": order["client_order_id"] if order is not None
        else "",
        "raw_remark": _bounded_text(raw_remark, 128),
    }


def _observe_callback_order(batch, order, order_id, source):
    if not order_id:
        return
    coid = order["client_order_id"]
    evidence = _evidence_for(batch, coid)
    if order_id not in evidence.get("qmt_order_ids", []):
        evidence.setdefault("qmt_order_ids", []).append(order_id)
    first_observation = not evidence.get("order_observed", False)
    evidence["order_observed"] = True
    _save_active_state(batch)
    if first_observation:
        _log_event(
            "ORDER_OBSERVED",
            batch_id=batch.batch_id(),
            client_order_id=coid,
            qmt_order_ids=evidence["qmt_order_ids"],
            query_number=int(evidence.get("query_count", 0)),
            elapsed_ms_since_attempt=0.0,
            source=source,
            message="real QMT order id observed by callback",
        )
        if not _order_is_terminal(batch, coid):
            _write_fill(
                batch, order, "ACCEPTED", 0, 0.0, order_id,
                "broker order observed by callback",
            )


def order_callback(ContextInfo, orderInfo):
    """Persist QMT order callback evidence and state transitions."""
    fields = _order_detail_evidence(
        orderInfo, _callback_remark(orderInfo), "callback",
    )
    remark = str(fields.get("remark", "") or "")
    order_id = str(fields.get("order_id", "") or "")
    batch = g.batch
    order = None
    if batch is not None:
        order = _find_callback_order(
            batch, remark, fields.get("stock_code", ""),
        )
    association = _callback_association_fields(batch, order, remark)
    _log_event(
        "ORDER_CALLBACK",
        order_id=order_id,
        callback=fields,
        message="QMT order callback received",
        **association
    )
    if order is None:
        return
    evidence = _evidence_for(batch, order["client_order_id"])
    evidence["callback_counts"]["order"] = min(
        int(evidence["callback_counts"].get("order", 0)) + 1, 999999,
    )
    _save_active_state(batch)
    _observe_callback_order(batch, order, order_id, "ORDER_CALLBACK")


def deal_callback(ContextInfo, dealInfo):
    """Persist QMT deal callback evidence and cumulative fill."""
    fields = _safe_detail(dealInfo, [
        ("remark", "m_strRemark", "userOrderId", "orderRemark"),
        ("order_id", "m_strOrderSysID", "orderID", "orderId"),
        ("deal_id", "m_strDealID", "dealID", "dealId"),
        ("deal_quantity", "m_nVolume", "dealVolume", "volume"),
        ("deal_price", "m_dPrice", "dealPrice", "price"),
        ("cumulative_traded_quantity", "m_nVolumeTraded",
         "tradedVolume"),
        ("stock_code", "m_strInstrumentID", "orderCode"),
        ("error", "m_strErrorMsg", "errorMsg", "errorMessage"),
    ])
    remark = str(fields.get("remark", "") or _callback_remark(dealInfo))
    order_id = str(fields.get("order_id", "") or "")
    batch = g.batch
    order = None
    if batch is not None:
        order = _find_callback_order(
            batch, remark, fields.get("stock_code", ""),
        )
    association = _callback_association_fields(batch, order, remark)
    _log_event(
        "DEAL_CALLBACK",
        order_id=order_id,
        deal_id=str(fields.get("deal_id", "") or ""),
        callback=fields,
        message="QMT deal callback received",
        **association
    )
    if order is None:
        return
    evidence = _evidence_for(batch, order["client_order_id"])
    evidence["callback_counts"]["deal"] = min(
        int(evidence["callback_counts"].get("deal", 0)) + 1, 999999,
    )
    _save_active_state(batch)
    _observe_callback_order(batch, order, order_id, "DEAL_CALLBACK")


def orderError_callback(ContextInfo, orderArgs, errMsg):
    """Persist asynchronous broker rejection details across QMT restarts."""
    remark = _callback_remark(orderArgs)
    code = str(_callback_value(
        orderArgs, "orderCode", "m_strInstrumentID"))
    op_type = _callback_value(orderArgs, "opType", "m_nOpType")
    message = _bounded_text(errMsg, 512)
    order_id = str(_callback_value(
        orderArgs, "m_strOrderSysID", "orderID", "orderId",
    ))
    batch = g.batch
    order = None
    if batch is not None:
        order = _find_callback_order(batch, remark, code)
    association = _callback_association_fields(batch, order, remark)
    _log_event(
        "ORDER_ERROR_CALLBACK", stock_code=code,
        order_id=order_id,
        op_type=op_type,
        quantity=_callback_int(
            orderArgs, "m_nOrderVolume", "orderVolume", "volume",
        ),
        price=_callback_float(orderArgs, "m_dOrderPrice", "orderPrice"),
        status=_callback_value(orderArgs, "m_nOrderStatus", "orderStatus"),
        error_type=type(errMsg).__name__,
        error_message=message,
        message=message,
        **association
    )
    if order is not None:
        evidence = _evidence_for(batch, order["client_order_id"])
        evidence["callback_counts"]["error"] = min(
            int(evidence["callback_counts"].get("error", 0)) + 1,
            999999,
        )
        batch.submitted[order["client_order_id"]] = True
        _save_active_state(batch)
        _observe_callback_order(
            batch, order, order_id, "ORDER_ERROR_CALLBACK",
        )
        _write_fill(batch, order, "REJECTED", 0, 0.0, "", message)


def handlebar(ContextInfo):
    try:
        if not ContextInfo.is_last_bar():
            return
        if not g.loaded:
            init(ContextInfo)
        _advance(ContextInfo)
    except Exception:
        _log("handlebar error:\n" + traceback.format_exc())
