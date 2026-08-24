#coding:gbk
"""Read-only QMT observer. Never places an order.

Copy this into a standalone QMT strategy. Do not overwrite the main bridge
or the retired probe instance. Output is JSONL under D:\\qmt_bridge\\observe\\.
"""

import datetime
import json
import os
import re
import time

BRIDGE_ROOT = r"D:\qmt_bridge"
OBSERVE_DIR = os.path.join(BRIDGE_ROOT, "observe")
STRATEGY_NAME = "qlib_observe_security"
POLL_SECONDS = 1
CODES = [
    "600000.SH",
    "688001.SH",
    "688981.SH",
    "300750.SZ",
    "300059.SZ",
    "000001.SZ",
    "000858.SZ",
    "002415.SZ",
    "601318.SH",
    "600519.SH",
]

_AFTER_HOURS_KEYS = (
    "IsAfterHoursTrading", "AfterHoursTrading", "FixedPriceTrading",
)
_TIMETAG_KEYS = ("timetag", "m_strTime", "time")


def _first_present(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def eligible_from_detail(detail):
    """True / False / None. None means the broker did not expose the flag."""
    after_hours = _first_present(detail, _AFTER_HOURS_KEYS)
    if isinstance(after_hours, bool):
        return after_hours
    if isinstance(after_hours, (int, float)) and after_hours == after_hours:
        return after_hours != 0
    if isinstance(after_hours, str):
        normalized = after_hours.strip().lower()
        if normalized in ("1", "true", "yes", "y", "enabled"):
            return True
        if normalized in ("0", "false", "no", "n", "disabled"):
            return False
        return None
    return None


def close_is_final(tick):
    """Whether the tick is stamped at or after the 15:00:00 close.

    None means QMT did not expose a usable clock field.
    """
    if not isinstance(tick, dict):
        return None
    timetag = _first_present(tick, _TIMETAG_KEYS)
    if not isinstance(timetag, str):
        return None
    stamp = timetag.strip()[-8:]
    if not re.match(r"^\d{2}:\d{2}:\d{2}$", stamp):
        return None
    return stamp >= "15:00:00"


def _safe_mapping(value):
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(value)
    except Exception:
        return {}


def _dump_path():
    name = "observe_%s.jsonl" % time.strftime("%Y%m%d")
    return os.path.join(OBSERVE_DIR, name)


def record_observation(kind, payload):
    os.makedirs(OBSERVE_DIR, exist_ok=True)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": kind}
    row.update(payload)
    with open(_dump_path(), "a") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _get_detail(ContextInfo, code):
    getter = getattr(ContextInfo, "get_instrument_detail", None)
    if getter is None:
        getter = getattr(ContextInfo, "get_instrumentdetail", None)
    if getter is None:
        return {}
    try:
        return _safe_mapping(getter(code))
    except Exception:
        return {}


def _get_tick(ContextInfo, code):
    getter = getattr(ContextInfo, "get_full_tick", None)
    if getter is None:
        getter = getattr(ContextInfo, "get_fulltick", None)
    if getter is None:
        return {}
    try:
        raw = getter([code])
    except Exception:
        return {}
    if isinstance(raw, dict):
        tick = raw.get(code) or raw.get(code.replace(".", "")) or raw
        return _safe_mapping(tick)
    return {}


def handlebar(ContextInfo):
    for code in CODES:
        detail = _get_detail(ContextInfo, code)
        tick = _get_tick(ContextInfo, code)
        record_observation("OBSERVE", {
            "code": code,
            "detail_raw": detail,
            "after_hours_eligible": eligible_from_detail(detail),
            "tick_raw": tick,
            "close_is_final": close_is_final(tick),
        })


def timer_callback(ContextInfo):
    try:
        handlebar(ContextInfo)
    except Exception as exc:
        record_observation("TIMER_ERROR", {
            "error": str(exc),
            "error_type": type(exc).__name__,
        })


def _register_timer(ContextInfo):
    day = time.strftime("%Y%m%d")
    first_compact = day + "145950"
    try:
        if hasattr(ContextInfo, "schedule_run"):
            ContextInfo.schedule_run(
                timer_callback, first_compact, -1,
                datetime.timedelta(seconds=POLL_SECONDS), "qlib_observe_poll",
            )
        elif hasattr(ContextInfo, "run_time"):
            first_wakeup = time.strftime("%Y-%m-%d") + " 14:59:50"
            ContextInfo.run_time(
                "timer_callback", "%dnSecond" % POLL_SECONDS, first_wakeup,
            )
    except Exception as exc:
        record_observation("TIMER_REGISTERED", {
            "registered": False,
            "error": str(exc),
        })
        raise
    record_observation("TIMER_REGISTERED", {
        "registered": True,
        "interval_seconds": POLL_SECONDS,
    })


def init(ContextInfo):
    record_observation("INIT", {"strategy": STRATEGY_NAME})
    _register_timer(ContextInfo)
