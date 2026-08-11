"""Standalone QMT prType=49 smoke strategy with a dated submit window.

The request may be staged early, but it is consumed only from 15:05 through
15:25 on its exact trade date.  This strategy is independent from the main
bridge, marker files, monitoring, and the main ledger.
"""

import json
import os
import time

BRIDGE_ROOT = r"D:\qmt_bridge\pr49_debug"
REQUEST = os.path.join(BRIDGE_ROOT, "request.json")
PENDING_REQUEST = os.path.join(BRIDGE_ROOT, "request.pending.json")
EVENT_LOG = os.path.join(BRIDGE_ROOT, "qmt_pr49_events.jsonl")
STRATEGY_NAME = "qlib_pr49_debug"
ACCOUNT_ID = ""  # QMT-local account id; never commit the broker account number
SUBMIT_START = "15:05:00"
SUBMIT_END = "15:25:00"
_LAST_WAIT_KEY = None


def _event(kind, **payload):
    os.makedirs(BRIDGE_ROOT, exist_ok=True)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": kind, **payload}
    with open(EVENT_LOG, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def init(ContextInfo):
    _event(
        "INIT", strategy=STRATEGY_NAME, account_bound=bool(ACCOUNT_ID),
        prType=49, submit_start=SUBMIT_START, submit_end=SUBMIT_END,
    )


def _now_parts():
    return time.strftime("%Y-%m-%d"), time.strftime("%H:%M:%S")


def _valid_date(value):
    return (
        isinstance(value, str) and len(value) == 10
        and value[4] == "-" and value[7] == "-"
        and (value[:4] + value[5:7] + value[8:]).isdigit()
    )


def _validate_request(request):
    request_id = request.get("request_id")
    trade_date = request.get("trade_date")
    side = str(request.get("side", "")).upper()
    stock_code = request.get("stock_code")
    quantity = int(request.get("quantity", 0))
    if not isinstance(request_id, str) or not request_id.isalnum():
        raise ValueError("request_id must be non-empty alphanumeric text")
    if not _valid_date(trade_date):
        raise ValueError("trade_date must be YYYY-MM-DD")
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if not isinstance(stock_code, str) or not stock_code.endswith((".SH", ".SZ")):
        raise ValueError("stock_code must use QMT suffix format")
    if quantity <= 0 or quantity % 100:
        raise ValueError("quantity must be a positive whole lot")
    return request_id, trade_date, side, stock_code, quantity


def request_action(request, current_date, current_time):
    """Return the only permitted action for the dated request."""
    _, trade_date, _, _, _ = _validate_request(request)
    if current_date < trade_date:
        return "WAIT_DATE"
    if current_date > trade_date:
        return "EXPIRE"
    if current_time < SUBMIT_START:
        return "WAIT_WINDOW"
    if current_time > SUBMIT_END:
        return "EXPIRE"
    return "SUBMIT"


def _mask_account(account_id):
    text = str(account_id or "")
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def _processed_path(request_id):
    return REQUEST + "." + request_id + ".processed"


def _wait_once(action, request, current_date, current_time):
    global _LAST_WAIT_KEY
    key = (action, request.get("request_id"), current_date)
    if key == _LAST_WAIT_KEY:
        return
    _LAST_WAIT_KEY = key
    _event(
        action, request_id=request.get("request_id"),
        trade_date=request.get("trade_date"), current_date=current_date,
        current_time=current_time, submit_start=SUBMIT_START,
        submit_end=SUBMIT_END,
    )


def handlebar(ContextInfo):
    if not os.path.isfile(REQUEST) and os.path.isfile(PENDING_REQUEST):
        os.replace(PENDING_REQUEST, REQUEST)
        _event("REQUEST_ACTIVATED", request_path=REQUEST)
    if not os.path.isfile(REQUEST):
        return
    request_id = "invalid"
    terminal = False
    try:
        with open(REQUEST, "r", encoding="utf-8") as stream:
            request = json.load(stream)
        request_id, trade_date, side, stock_code, quantity = _validate_request(request)
        current_date, current_time = _now_parts()
        action = request_action(request, current_date, current_time)
        if action in {"WAIT_DATE", "WAIT_WINDOW"}:
            _wait_once(action, request, current_date, current_time)
            return
        if action == "EXPIRE":
            terminal = True
            raise ValueError("request expired outside prType=49 window")
        if not ACCOUNT_ID:
            terminal = True
            raise ValueError("QMT account is not bound")

        op_type = 23 if side == "BUY" else 24
        passorder_arguments = {
            "op_type": op_type,
            "order_type": 1101,
            "account_id_masked": _mask_account(ACCOUNT_ID),
            "stock_code": stock_code,
            "prType": 49,
            "price": 0,
            "quantity": quantity,
            "strategy_name": STRATEGY_NAME,
            "quick_trade": 2,
            "remark": request_id,
        }
        terminal = True
        _event(
            "PASSORDER_ATTEMPT", request=request,
            passorder_arguments=passorder_arguments,
        )
        result = passorder(
            op_type, 1101, ACCOUNT_ID, stock_code, 49, 0,
            quantity, STRATEGY_NAME, 2, request_id, ContextInfo,
        )
        _event(
            "PASSORDER_RETURN", request_id=request_id,
            result=repr(result), prType=49,
        )
    except Exception as exc:
        terminal = True
        _event(
            "ERROR", request_id=request_id, error=str(exc),
            error_type=type(exc).__name__, prType=49,
        )
    finally:
        if terminal and os.path.isfile(REQUEST):
            os.replace(REQUEST, _processed_path(request_id))


def _callback_fields(detail):
    result = {}
    for name in (
        "m_strRemark", "orderRemark", "m_strInstrumentID", "orderCode",
        "m_strOrderSysID", "orderID", "m_nOrderStatus", "orderStatus",
        "m_nVolume", "m_nVolumeTraded", "m_dPrice",
    ):
        if hasattr(detail, name):
            try:
                result[name] = getattr(detail, name)
            except Exception:
                result[name] = "<unreadable>"
    return result


def order_callback(ContextInfo, orderInfo):
    _event("ORDER_CALLBACK", callback=_callback_fields(orderInfo))


def deal_callback(ContextInfo, dealInfo):
    _event("DEAL_CALLBACK", callback=_callback_fields(dealInfo))


def orderError_callback(ContextInfo, orderArgs, errMsg):
    _event(
        "ORDER_ERROR_CALLBACK", callback=_callback_fields(orderArgs),
        error=str(errMsg),
    )
