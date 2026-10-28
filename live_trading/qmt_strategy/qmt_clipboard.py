# coding:gbk
"""Standalone QMT Python 3.6 clipboard executor; no file or network I/O.

Copy this entire ASCII source into a QMT strategy and set ACCOUNT_ID locally.
The sibling clipboard_relay.py imports the same Windows clipboard adapter.
"""

import ctypes
import datetime
import hashlib
import json
import math
import re
import sys
import threading
import uuid
from collections import OrderedDict, deque


ACCOUNT_ID = ""
STRATEGY_NAME = "qlib_clipboard"
PROTOCOL = "qlib-clipboard-v1"
MAX_REQUEST_CHARS = 262144
MAX_TEXT_CHARS = 1024 * 1024  # Responses include the whole 200-order session.
MAX_ORDERS = 40
MAX_SESSION_ORDERS = 200
TERMINAL = ("FILLED", "CANCELED", "REJECTED", "EXPIRED")
SHANGHAI = datetime.timezone(datetime.timedelta(hours=8))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


class Clipboard:
    """Unicode text with bounded reads and atomic compare/replace under Win32's lock."""

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Windows interactive desktop required")
        from ctypes import wintypes as w

        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = [
            (self.user.OpenClipboard, [w.HWND], w.BOOL),
            (self.user.CloseClipboard, [], w.BOOL),
            (self.user.IsClipboardFormatAvailable, [w.UINT], w.BOOL),
            (self.user.GetClipboardData, [w.UINT], w.HANDLE),
            (self.user.EmptyClipboard, [], w.BOOL),
            (self.user.SetClipboardData, [w.UINT, w.HANDLE], w.HANDLE),
            (
                self.user.CreateWindowExW,
                [
                    w.DWORD,
                    w.LPCWSTR,
                    w.LPCWSTR,
                    w.DWORD,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    w.HWND,
                    w.HMENU,
                    w.HINSTANCE,
                    ctypes.c_void_p,
                ],
                w.HWND,
            ),
            (self.user.DestroyWindow, [w.HWND], w.BOOL),
            (self.kernel.GlobalAlloc, [w.UINT, ctypes.c_size_t], w.HGLOBAL),
            (self.kernel.GlobalLock, [w.HGLOBAL], ctypes.c_void_p),
            (self.kernel.GlobalUnlock, [w.HGLOBAL], w.BOOL),
            (self.kernel.GlobalSize, [w.HGLOBAL], ctypes.c_size_t),
            (self.kernel.GlobalFree, [w.HGLOBAL], w.HGLOBAL),
        ]
        for function, args, result in signatures:
            function.argtypes = args
            function.restype = result

    def _read_locked(self):
        if not self.user.IsClipboardFormatAvailable(13):  # CF_UNICODETEXT
            return ""
        handle = self.user.GetClipboardData(13)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        size = self.kernel.GlobalSize(handle)
        if size < 2 or size > (MAX_TEXT_CHARS + 1) * 2 or size % 2:
            raise ValueError("clipboard text size outside limit")
        pointer = self.kernel.GlobalLock(handle)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            data = ctypes.string_at(pointer, size)
            end = next((i for i in range(0, size, 2) if data[i : i + 2] == b"\x00\x00"), None)
            if end is None:
                raise ValueError("unterminated clipboard text")
            return data[:end].decode("utf-16-le")
        finally:
            self.kernel.GlobalUnlock(handle)

    def read(self):
        if not self.user.OpenClipboard(None):
            raise OSError("clipboard busy")
        try:
            return self._read_locked()
        finally:
            self.user.CloseClipboard()

    def replace(self, expected, text):
        data = text.encode("utf-16-le") + b"\x00\x00"
        if "\x00" in text or len(data) > (MAX_TEXT_CHARS + 1) * 2:
            raise ValueError("invalid or oversized clipboard output")
        # A real owner HWND is required by EmptyClipboard/SetClipboardData.
        owner = self.user.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0, ctypes.c_void_p(-3), None, None, None)
        if not owner:
            raise ctypes.WinError(ctypes.get_last_error())
        memory = None
        opened = False
        try:
            if not self.user.OpenClipboard(owner):
                raise OSError("clipboard busy")
            opened = True
            if self._read_locked() != expected:
                return False
            memory = self.kernel.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
            if not memory:
                raise ctypes.WinError(ctypes.get_last_error())
            pointer = self.kernel.GlobalLock(memory)
            if not pointer:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                self.kernel.GlobalUnlock(memory)
            if not self.user.EmptyClipboard() or not self.user.SetClipboardData(13, memory):
                raise ctypes.WinError(ctypes.get_last_error())
            memory = None  # Windows owns it now.
            return True
        finally:
            if memory:
                self.kernel.GlobalFree(memory)
            if opened:
                self.user.CloseClipboard()
            self.user.DestroyWindow(owner)


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("invalid " + name)
    return value


def _expiry(text):
    if not isinstance(text, str) or not re.search(r"(?:Z|[+-]\d{2}:\d{2})$", text):
        raise ValueError("expires_at requires an ISO timestamp with timezone")
    # Python 3.6 strptime does not accept colons in UTC offsets.
    normalized = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", text.replace("Z", "+0000"))
    return datetime.datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S%z")


def _order(value):
    fields = {"id", "symbol", "side", "quantity", "price"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("order requires exactly id/symbol/side/quantity/price")
    _identifier(value["id"], "order id")
    if not isinstance(value["symbol"], str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", value["symbol"]):
        raise ValueError("invalid stock symbol")
    if value["side"] not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if type(value["quantity"]) is not int or not 0 < value["quantity"] <= 10000000:
        raise ValueError("quantity must be a positive integer <= 10000000")
    if type(value["price"]) not in (int, float) or not math.isfinite(value["price"]) or value["price"] <= 0:
        raise ValueError("price must be positive and finite")
    return dict(value)


class Engine:
    """Memory-only order state. A submit exception is uncertain, never retryable."""

    def __init__(self, submit, query, session=None, now=None, snapshot=None):
        self.submit = submit
        self.query = query
        self.session = session or uuid.uuid4().hex
        self.now = now or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self.snapshot = snapshot
        self.day = self.now().astimezone(SHANGHAI).date().isoformat()
        self.orders = OrderedDict()
        self.requests = {}
        self.logs = deque(maxlen=100)
        self.sequence = 0
        self.query_error = ""
        self.log("INFO", "started; orders are not restored after restart")

    def log(self, level, message):
        self.sequence += 1
        self.logs.append(dict(seq=self.sequence, ts=self.now().isoformat(), level=level, message=str(message)[:300]))

    def _trade(self, request):
        expires = _expiry(request.get("expires_at"))
        now = self.now()
        if expires <= now or expires.astimezone(SHANGHAI).date() != now.astimezone(SHANGHAI).date():
            raise ValueError("trade expired or expiry is not today in Asia/Shanghai")
        raw = request.get("orders")
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ORDERS:
            raise ValueError("trade requires 1..40 orders")
        orders = [_order(order) for order in raw]
        ids = [order["id"] for order in orders]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate order id within request")
        fingerprint = dumps(orders)
        previous = self.requests.get(request["request_id"])
        if previous is not None and previous != fingerprint:
            raise ValueError("request id reused with different orders")
        for order in orders:
            old = self.orders.get(order["id"])
            if old is not None and old["order"] != order:
                raise ValueError("order id reused with different content")
        if len(set(self.orders).union(ids)) > MAX_SESSION_ORDERS:
            raise ValueError("session order limit reached; reconcile before restarting")
        # Record the request and each order BEFORE attempting any broker submission.
        self.requests[request["request_id"]] = fingerprint
        for order in orders:
            if order["id"] in self.orders:
                continue
            remark = "qc" + hashlib.sha256((self.session + "/" + order["id"]).encode("ascii")).hexdigest()[:20]
            state = dict(
                order=order,
                remark=remark,
                status="SUBMITTED",
                filled_quantity=0,
                average_price=0.0,
                broker_orders={},
                error="",
            )
            self.orders[order["id"]] = state
            if self.now() >= expires:
                state["status"] = "EXPIRED"
                state["error"] = "expired before this order's submission"
                self.log("ERROR", order["id"] + ": " + state["error"])
                continue
            self.log(
                "INFO",
                "submit %s %s %s x%d @%s"
                % (order["id"], order["side"], order["symbol"], order["quantity"], order["price"]),
            )
            try:
                self.submit(order, remark)
            except Exception as exc:
                state["status"] = "UNKNOWN"
                state["error"] = str(exc)[:300]
                self.log("ERROR", "uncertain submit %s: %s" % (order["id"], exc))

    def _refresh(self):
        if not self.orders:
            return
        try:
            rows = self.query()
            if rows is None:
                raise ValueError("broker query returned no usable result")
            by_remark = {state["remark"]: state for state in self.orders.values()}
            for row in rows:
                state = by_remark.get(row.get("remark"))
                if state is None or not row.get("broker_order_id"):
                    continue
                if (
                    type(row["quantity"]) is not int
                    or row["quantity"] < 0
                    or type(row["filled_quantity"]) is not int
                    or row["filled_quantity"] < 0
                    or type(row["average_price"]) not in (int, float)
                    or not math.isfinite(row["average_price"])
                    or row["average_price"] < 0
                ):
                    raise ValueError("invalid broker quantity or average price")
                broker_id = str(row["broker_order_id"])
                old = state["broker_orders"].get(broker_id)
                # Missing/stale query rows never erase an already observed fill.
                if old and (
                    row["filled_quantity"] < old["filled_quantity"]
                    or (old["status"] in (53, 54, 56, 57) and row["status"] not in (53, 54, 56, 57))
                ):
                    continue
                state["broker_orders"][broker_id] = dict(row)
            self.query_error = ""
            for state in self.orders.values():
                children = list(state["broker_orders"].values())
                if not children:
                    continue
                filled = sum(child["filled_quantity"] for child in children)
                quantity = state["order"]["quantity"]
                old_status = state["status"]
                if filled > quantity:
                    state["status"] = "UNKNOWN"
                    state["error"] = "broker filled quantity exceeds requested quantity"
                elif filled == quantity:
                    state["status"] = "FILLED"
                elif any(child["status"] == 56 and child["filled_quantity"] != child["quantity"] for child in children):
                    state["status"] = "UNKNOWN"
                    state["error"] = "broker success status has incomplete volume evidence"
                elif sum(child["quantity"] for child in children) == quantity and all(
                    child["status"] in (53, 54, 56, 57) for child in children
                ):
                    state["status"] = "REJECTED" if all(child["status"] == 57 for child in children) else "CANCELED"
                else:
                    state["status"] = "PARTIAL" if filled else "ACCEPTED"
                state["filled_quantity"] = filled
                state["average_price"] = (
                    sum(child["filled_quantity"] * child["average_price"] for child in children) / filled
                    if filled
                    else 0.0
                )
                if state["status"] != "UNKNOWN":
                    state["error"] = "; ".join(child.get("error", "") for child in children if child.get("error"))[:300]
                if old_status != state["status"]:
                    self.log("INFO", "%s %s filled=%d" % (state["order"]["id"], state["status"], filled))
        except Exception as exc:
            error = str(exc)[:300]
            if error != self.query_error:
                self.log("ERROR", "query failed: " + error)
            self.query_error = error

    def handle(self, request):
        error = ""
        observation = None
        try:
            if request.get("protocol") != PROTOCOL or request.get("type") not in ("trade", "poll", "snapshot"):
                raise ValueError("unsupported protocol or request type")
            _identifier(request.get("request_id"), "request_id")
            today = self.now().astimezone(SHANGHAI).date().isoformat()
            if request["type"] == "poll" and not request.get("session") and today != self.day:
                if all(item["status"] in TERMINAL for item in self.orders.values()):
                    self.orders.clear()
                    self.requests.clear()
                    self.logs.clear()
                    self.sequence = 0
                    self.session = uuid.uuid4().hex
                    self.day = today
                    self.query_error = ""
                    self.log("INFO", "new trading day; settled session rotated")
            session = request.get("session")
            if session != self.session and not (request["type"] == "poll" and not session):
                raise ValueError("session changed; reconcile manually before sending new trades")
            if request["type"] == "trade":
                if self.day != today:
                    raise ValueError("previous day has unresolved orders; reconcile first")
                self._trade(request)
            elif request["type"] == "snapshot":
                symbols = request.get("symbols", [])
                if (
                    not isinstance(symbols, list)
                    or len(symbols) > MAX_ORDERS
                    or any(not isinstance(code, str) or not re.match(r"^\d{6}\.(SH|SZ|BJ)$", code) for code in symbols)
                ):
                    raise ValueError("invalid snapshot symbols")
                if self.snapshot is None:
                    raise ValueError("snapshot API unavailable; update QMT strategy")
                observation = self.snapshot(symbols)
            self._refresh()
        except Exception as exc:
            error = str(exc)[:300]
            self.log("ERROR", error)
        snapshot = []
        for state in self.orders.values():
            item = dict(state["order"])
            item.update({key: state[key] for key in ("status", "filled_quantity", "average_price", "error", "remark")})
            item["broker_orders"] = list(state["broker_orders"].values())
            snapshot.append(item)
        return dict(
            protocol=PROTOCOL,
            type="response",
            session=self.session,
            day=self.day,
            capabilities=["snapshot-v1"] if self.snapshot is not None else [],
            snapshot=observation,
            request_id=request.get("request_id"),
            ok=not error,
            error=error,
            ts=self.now().isoformat(),
            query_error=self.query_error,
            orders=snapshot,
            logs=list(self.logs),
        )


class QmtBroker:
    def __init__(self, context, account):
        self.context = context
        self.account = account

    def submit(self, order, remark):
        # The API's return value is NOT proof of broker acceptance or a fill.
        passorder(  # noqa: F821 -- supplied by QMT
            23 if order["side"] == "BUY" else 24,
            1101,
            self.account,
            order["symbol"],
            11,
            order["price"],
            order["quantity"],
            STRATEGY_NAME,
            2,
            remark,
            self.context,
        )

    def query(self):
        details = get_trade_detail_data(self.account, "STOCK", "ORDER")  # noqa: F821
        if details is None:
            raise RuntimeError("broker ORDER query returned None")
        result = []
        for item in details:
            result.append(
                dict(
                    remark=str(getattr(item, "m_strRemark", "") or ""),
                    broker_order_id=str(getattr(item, "m_strOrderSysID", "") or ""),
                    quantity=int(getattr(item, "m_nVolumeTotalOriginal", getattr(item, "m_nOrderVolume", 0)) or 0),
                    filled_quantity=int(getattr(item, "m_nVolumeTraded", 0) or 0),
                    average_price=float(getattr(item, "m_dTradedPrice", 0) or 0),
                    status=int(getattr(item, "m_nOrderStatus", -1)),
                    error=str(getattr(item, "m_strErrorMsg", "") or "")[:300],
                )
            )
        return result

    def snapshot(self, symbols):
        """Read broker facts and tick data; never infer cash from a strategy ledger."""

        def number(obj, *names):
            for name in names:
                raw = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
                try:
                    value = float(raw)
                    if math.isfinite(value):
                        return value
                except (ValueError, TypeError):
                    pass
            return None

        accounts = get_trade_detail_data(self.account, "STOCK", "ACCOUNT")  # noqa: F821
        if not accounts or len(accounts) != 1:
            raise ValueError("ACCOUNT query must return one account")
        account = accounts[0]
        identity = str(getattr(account, "m_strAccountID", "") or self.account)
        if identity != self.account:
            raise ValueError("broker account identity mismatch")
        cash = number(account, "m_dAvailable")
        if cash is None or cash < 0:
            raise ValueError("invalid available cash")
        raw_positions = get_trade_detail_data(self.account, "STOCK", "POSITION")  # noqa: F821
        if raw_positions is None:
            raise ValueError("POSITION query returned None")
        positions = []
        for item in raw_positions:
            shares = int(getattr(item, "m_nVolume"))
            available = int(getattr(item, "m_nCanUseVolume"))
            if shares < 0 or not 0 <= available <= shares:
                raise ValueError("invalid position volumes")
            if not shares:
                continue
            code = str(getattr(item, "m_strInstrumentID"))
            exchange = str(getattr(item, "m_strExchangeID", "")).upper()[:2]
            if exchange not in ("SH", "SZ", "BJ"):
                raise ValueError("unrecognized position exchange")
            positions.append(
                dict(
                    stock_code=code + "." + exchange,
                    shares=shares,
                    can_use_volume=available,
                    frozen_shares=int(getattr(item, "m_nFrozenVolume", 0) or 0),
                    avg_cost=number(item, "m_dOpenPrice", "m_dPositionCost"),
                    market_value=number(item, "m_dMarketValue"),
                )
            )
        quotes = {}
        ticks = self.context.get_full_tick(symbols) if symbols else {}
        getter = getattr(self.context, "get_instrument_detail", None)
        getter = getter or getattr(self.context, "get_instrumentdetail", None)
        for code in symbols:
            tick = (ticks or {}).get(code)
            if tick is None:
                raise ValueError("tick missing for " + code)
            detail = getter(code) if getter else {}
            tag = tick.get("timetag") if isinstance(tick, dict) else getattr(tick, "timetag", None)
            quotes[code] = dict(
                last=number(tick, "lastPrice"),
                timetag=tag,
                upper=number(detail, "UpStopPrice"),
                lower=number(detail, "DownStopPrice"),
            )
        return dict(
            account=dict(
                account_id=identity,
                available_cash=cash,
                total_asset=number(account, "m_dBalance", "m_dAssureAsset"),
                market_value=number(account, "m_dInstrumentValue", "m_dStockValue"),
                frozen_cash=number(account, "m_dFrozenCash"),
            ),
            positions=positions,
            quotes=quotes,
        )


class Runtime:
    def __init__(self, clipboard, engine):
        self.clipboard = clipboard
        self.engine = engine
        self.lock = threading.Lock()
        self.started = False
        self.announced_types = set()
        self.last_error = None

    def step(self):
        if not self.lock.acquire(False):
            return
        try:
            if not self.started:
                print("[qlib_clipboard] clipboard polling active")
                self.started = True
            text = self.clipboard.read()
            if not text or len(text) > MAX_REQUEST_CHARS:
                return
            try:
                request = json.loads(text)
            except (ValueError, TypeError):
                return
            if (
                not isinstance(request, dict)
                or request.get("protocol") != PROTOCOL
                or request.get("type") not in ("trade", "poll", "snapshot")
            ):
                return
            if request["type"] not in self.announced_types:
                print("[qlib_clipboard] received request=" + request["type"])
                self.announced_types.add(request["type"])
            response = self.engine.handle(request)
            wire = dumps(response)
            if len(wire.encode("utf-16-le")) > MAX_TEXT_CHARS * 2:
                # Never silently time out after a potentially successful submission.
                response.update(
                    ok=False, error="snapshot size limit exceeded; reconcile orders in QMT", orders=[], logs=[]
                )
                wire = dumps(response)
            self.clipboard.replace(text, wire)
            self.last_error = None
        except Exception as exc:
            error = "clipboard: " + str(exc)
            self.engine.log("ERROR", error)
            if error != self.last_error:
                print("[qlib_clipboard] " + error)
            self.last_error = error
        finally:
            self.lock.release()


_runtime = None


def init(ContextInfo):
    global _runtime
    _runtime = None
    if not ACCOUNT_ID:
        raise ValueError("set ACCOUNT_ID locally and bind the same account in QMT")
    ContextInfo.set_account(ACCOUNT_ID)
    broker = QmtBroker(ContextInfo, ACCOUNT_ID)
    runtime = Runtime(Clipboard(), Engine(broker.submit, broker.query, snapshot=broker.snapshot))
    first = datetime.datetime.now() + datetime.timedelta(seconds=1)
    # Install before registration, since some QMT versions call overdue timers inline.
    _runtime = runtime
    try:
        if hasattr(ContextInfo, "schedule_run"):
            ContextInfo.schedule_run(
                timer_callback, first.strftime("%Y%m%d%H%M%S"), -1, datetime.timedelta(seconds=1), "qlib_clipboard_poll"
            )
        else:
            ContextInfo.run_time("timer_callback", "1nSecond", first.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        _runtime = None
        raise
    print("[qlib_clipboard] ready session=" + runtime.engine.session + " capabilities=snapshot-v1 diagnostics=1")


def timer_callback(ContextInfo):
    if _runtime is not None:
        _runtime.step()


def handlebar(ContextInfo):
    timer_callback(ContextInfo)
