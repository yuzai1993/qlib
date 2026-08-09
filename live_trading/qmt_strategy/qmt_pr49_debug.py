"""Standalone QMT prType=49 smoke strategy.

Copy this file into a separate QMT strategy instance.  It intentionally has
no dependency on the main bridge, LIVE_OK markers, monitor, or main ledger.
The operator drops one request JSON into ``D:\\qmt_bridge\\pr49_debug`` and
reviews the append-only event log after QMT returns.
"""

import json
import os
import time

BRIDGE_ROOT = r"D:\qmt_bridge\pr49_debug"
REQUEST = os.path.join(BRIDGE_ROOT, "request.json")
EVENT_LOG = os.path.join(BRIDGE_ROOT, "qmt_pr49_events.jsonl")
STRATEGY_NAME = "qlib_pr49_debug"
ACCOUNT_ID = ""  # select/bind this in QMT before starting the strategy


def _event(kind, **payload):
    os.makedirs(BRIDGE_ROOT, exist_ok=True)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": kind, **payload}
    with open(EVENT_LOG, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def init(ContextInfo):
    _event("INIT", strategy=STRATEGY_NAME, account_bound=bool(ACCOUNT_ID), prType=49)


def handlebar(ContextInfo):
    if not os.path.isfile(REQUEST):
        return
    try:
        with open(REQUEST, "r", encoding="utf-8") as stream:
            request = json.load(stream)
        side = request["side"].upper()
        stock_code = request["stock_code"]
        quantity = int(request["quantity"])
        if side not in {"BUY", "SELL"} or quantity <= 0 or quantity % 100:
            raise ValueError("side must be BUY/SELL and quantity a positive whole lot")
        op_type = 23 if side == "BUY" else 24
        _event("PASSORDER_ATTEMPT", request=request, prType=49, price=0)
        result = passorder(
            op_type, 1101, ACCOUNT_ID, stock_code, 49, 0,
            quantity, STRATEGY_NAME, 2, request.get("request_id", "pr49"), ContextInfo,
        )
        _event("PASSORDER_RETURN", result=repr(result), prType=49)
    except Exception as exc:
        _event("ERROR", error=repr(exc), prType=49)
    finally:
        os.replace(REQUEST, REQUEST + ".processed")

