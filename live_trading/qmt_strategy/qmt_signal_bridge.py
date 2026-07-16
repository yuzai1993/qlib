#coding:gbk
# qmt_signal_bridge -- QMT built-in strategy that consumes qlib signal batches.
#
# Protocol: docs/superpowers/specs/2026-07-11-qmt-live-signal-bridge-design.md
# Runtime:  QMT built-in Python 3.6. ASCII only (file declares gbk; ascii is a
#           strict subset, so it is valid in both encodings).
#
# Flow per batch:
#   inbox/signal_{batch}.jsonl + .done
#     -> claim to processing/ (skip if expired / duplicate / bad checksum)
#     -> 14:45 late-session window (execute near close, align w/ backtest):
#        phase SELL: passorder all sells, wait terminal (or timeout)
#        phase BUY : check available cash, passorder buys
#     -> price: ask-one / bid-one +/- buffer, clamped to daily price limits
#        (signal limit_price is used only when realtime data is unavailable)
#     -> poll order status by remark (client_order_id)
#     -> 14:56 cancel pending, mark EXPIRED; write outbound/fills_{batch}.done
#
# LIVE double switch: header.mode == "LIVE" AND state/LIVE_OK_{trade_date} exists.
# Otherwise orders are simulated: fill status SKIPPED, message "simulated".

import json
import math
import os
import time
import datetime
import traceback

# ======================= user settings =======================

BRIDGE_ROOT = r"D:\qmt_bridge"
ACCOUNT_ID = ""            # override header.account_id if non-empty
ACCOUNT_TYPE = "STOCK"
STRATEGY_NAME = "qlib_bridge"

POLL_SECONDS = 3           # min interval between polls (handlebar is tick-driven)
SELL_WAIT_TIMEOUT_SEC = 4 * 60    # max wait for sells before starting buys
TRADE_START = "14:45:00"   # late-session window: execute near close price
CANCEL_AT = "14:56:00"     # cancel all pending orders
FINALIZE_AT = "14:57:00"   # force-write fills .done

# First-order pricing: cross the current opposing quote with a small buffer.
# QMT daily stop prices are the hard bounds; the signal price is a fallback.
INTRADAY_BUY_SLIPPAGE = 0.003
INTRADAY_SELL_SLIPPAGE = 0.003

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
        self.submitted = {}           # client_order_id -> True
        self.fills = {}               # client_order_id -> fill dict (latest)
        self.remaining_cash = None    # one broker cash snapshot for BUY phase
        self.processing_jsonl = None
        self.processing_done = None
        self.finalized = False

    def batch_id(self):
        return self.header["batch_id"]


class State(object):
    def __init__(self):
        self.last_poll = 0.0
        self.batch = None             # current Batch or None
        self.processed = set()        # batch ids finished (persisted)
        self.loaded = False


g = State()

# ======================= small utils =======================


def _log(msg):
    print("[qlib_bridge] " + str(msg))


def _now_hms():
    return datetime.datetime.now().strftime("%H:%M:%S")


def _today():
    return datetime.date.today().strftime("%Y-%m-%d")


def _path(*parts):
    return os.path.join(BRIDGE_ROOT, *parts)


def _ensure_dirs():
    for d in ("inbox", "processing", "outbound", "archive", "state", "logs"):
        p = _path(d)
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
    g.processed.add(batch_id)
    with open(_state_file(), "a") as f:
        f.write(batch_id + "\n")


def _sha256_of_lines(lines):
    import hashlib
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
    return "sha256:" + h.hexdigest()


def _live_ok(trade_date):
    return os.path.isfile(_path("state", "LIVE_OK_" + trade_date))


def _active_state_path(batch_id):
    return _path("state", "active_" + batch_id + ".json")


def _save_active_state(batch):
    payload = {
        "batch_id": batch.batch_id(),
        "phase": batch.phase,
        "trading_started": batch.trading_started,
        "submitted": sorted(batch.submitted.keys()),
        "fills": batch.fills,
        "remaining_cash": batch.remaining_cash,
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
    batch.submitted = dict((coid, True) for coid in payload.get("submitted", []))
    batch.fills = payload.get("fills", {})
    batch.remaining_cash = payload.get("remaining_cash")
    if payload.get("orders"):
        batch.orders = payload["orders"]
    batch.phase_started = time.time()


def _remove_active_state(batch_id):
    path = _active_state_path(batch_id)
    if os.path.isfile(path):
        os.remove(path)

# ======================= fills output =======================


def _fills_path(batch_id):
    return _path("outbound", "fills_" + batch_id + ".jsonl")


def _write_fill(batch, order, status, filled_qty, avg_price, qmt_order_id, message):
    mode = batch.header.get("mode", "SIMULATE")
    event = {
        "type": "fill_event",
        "batch_id": batch.batch_id(),
        "client_order_id": order["client_order_id"],
        "mode": mode,
        "stock_code": order["stock_code"],
        "side": order["side"],
        "status": status,
        "requested_qty": order["quantity"],
        "filled_qty": int(filled_qty),
        "avg_price": float(avg_price),
        "qmt_order_id": str(qmt_order_id),
        "message": message,
        "ts": datetime.datetime.now().isoformat(),
    }
    prev = batch.fills.get(order["client_order_id"])
    if prev is not None and prev["status"] == status \
            and prev["filled_qty"] == event["filled_qty"]:
        return  # no change, do not spam the file
    batch.fills[order["client_order_id"]] = event
    with open(_fills_path(batch.batch_id()), "a") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")
    _save_active_state(batch)


def _finalize_batch(batch):
    if batch.finalized:
        return
    done = _fills_path(batch.batch_id()).replace(".jsonl", ".done")
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
        with open(jsonl_path, "r") as f:
            first = f.readline().strip()
        return json.loads(first).get("trade_date")
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
    for p in (jsonl_path, done_path):
        dst = _path("archive", os.path.basename(p))
        if os.path.isfile(dst):
            os.remove(dst)
        os.rename(p, dst)


def _parse_and_check(jsonl_path, done_path):
    """Return Batch or None (rejected batches get a fills file + done)."""
    with open(jsonl_path, "r") as f:
        lines = [l.strip() for l in f.read().splitlines() if l.strip()]
    if not lines:
        _log("empty batch file: " + jsonl_path)
        return None
    header = json.loads(lines[0])
    order_lines = lines[1:]
    orders = [json.loads(l) for l in order_lines]
    batch = Batch(header, orders)
    batch.processing_jsonl = jsonl_path
    batch.processing_done = done_path
    batch_id = header.get("batch_id", "unknown")

    def reject(reason):
        _log("reject batch %s: %s" % (batch_id, reason))
        for o in orders:
            _write_fill(batch, o, "SKIPPED", 0, 0.0, "", reason)
        _finalize_batch(batch)
        return None

    if batch_id in g.processed:
        return reject("duplicate batch")
    if header.get("trade_date") != _today():
        return reject("expired: trade_date=%s today=%s"
                      % (header.get("trade_date"), _today()))
    with open(done_path, "r") as f:
        expected = f.read().strip()
    actual = _sha256_of_lines(order_lines)
    if expected and expected != actual:
        return reject("checksum mismatch")
    if header.get("order_count") != len(orders):
        return reject("order_count mismatch")

    # sells first by priority, stable by client_order_id
    orders.sort(key=lambda o: (o.get("priority", 99), o.get("client_order_id", "")))
    batch.orders = orders
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
        _load_active_state(batch)
        g.batch = batch
        _log("recovered batch %s: phase=%s submitted=%d"
             % (batch.batch_id(), batch.phase, len(batch.submitted)))
        return

# ======================= QMT API wrappers =======================
# All QMT built-in API usage is isolated below so the pure logic above
# stays testable / reviewable.


def _account_id(batch):
    return ACCOUNT_ID or batch.header.get("account_id", "")


def _get_orders_by_remark(account_id):
    """remark -> order detail object."""
    result = {}
    try:
        details = get_trade_detail_data(account_id, ACCOUNT_TYPE, "ORDER")
    except Exception:
        _log("get_trade_detail_data ORDER failed:\n" + traceback.format_exc())
        return result
    for d in details:
        remark = getattr(d, "m_strRemark", "")
        if remark:
            result[remark] = d
    return result


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
             % (account_id, ACCOUNT_TYPE))
        return None
    account = accounts[0]
    returned_id = str(getattr(account, "m_strAccountID", "") or "")
    if returned_id and returned_id != str(account_id):
        _log("ACCOUNT query id mismatch: requested %s returned %s"
             % (account_id, returned_id))
        return None
    raw = getattr(account, "m_dAvailable", None)
    try:
        available = float(raw)
    except (TypeError, ValueError):
        _log("ACCOUNT query missing available cash for account %s"
             % account_id)
        return None
    if not math.isfinite(available) or available < 0.0:
        _log("ACCOUNT query invalid available cash for account %s: %s"
             % (account_id, raw))
        return None
    return available


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


def _first_book_price(tick, field):
    levels = _tick_field(tick, field, [])
    if not isinstance(levels, (list, tuple)) or not levels:
        return 0.0
    return _positive_price(levels[0])


def _get_price_limits(ContextInfo, stock_code):
    try:
        detail = ContextInfo.get_instrumentdetail(stock_code)
    except Exception:
        _log("get_instrumentdetail failed for %s:\n%s"
             % (stock_code, traceback.format_exc()))
        return 0.0, 0.0
    if detail is None:
        return 0.0, 0.0
    if isinstance(detail, dict):
        upper = detail.get("UpStopPrice")
        lower = detail.get("DownStopPrice")
    else:
        upper = getattr(detail, "UpStopPrice", None)
        lower = getattr(detail, "DownStopPrice", None)
    return _positive_price(upper), _positive_price(lower)


def _effective_price(ContextInfo, order):
    """Marketable first-order price with the signal price as data fallback."""
    fallback_price = float(order["limit_price"])
    tick = _get_tick(ContextInfo, order["stock_code"])
    if tick is None:
        return fallback_price

    last = _positive_price(_tick_field(tick, "lastPrice"))
    if order["side"] == "BUY":
        reference = _first_book_price(tick, "askPrice") or last
    else:
        reference = _first_book_price(tick, "bidPrice") or last
    if reference <= 0.0:
        return fallback_price

    upper, lower = _get_price_limits(ContextInfo, order["stock_code"])
    if order["side"] == "BUY":
        price = round(reference * (1.0 + INTRADAY_BUY_SLIPPAGE), 2)
        if upper > 0.0:
            price = min(price, upper)
    else:
        price = round(reference * (1.0 - INTRADAY_SELL_SLIPPAGE), 2)
        if lower > 0.0:
            price = max(price, lower)
    return round(price, 2)


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


def _submit(ContextInfo, batch, order, live, price=None):
    """Submit one order. Returns True if submitted (or simulated)."""
    coid = order["client_order_id"]
    if coid in batch.submitted:
        return True
    if not live:
        batch.submitted[coid] = True
        _save_active_state(batch)
        _write_fill(batch, order, "SKIPPED", 0, 0.0, "", "simulated")
        return True

    op_type = 23 if order["side"] == "BUY" else 24
    if price is None:
        price = _effective_price(ContextInfo, order)
    # Persist before passorder. On a crash, an uncertain order is never
    # submitted twice; the safer failure direction is a missed order.
    batch.submitted[coid] = True
    _save_active_state(batch)
    try:
        # orderType=1101 single stock by shares; prType=11 fixed price;
        # quickTrade=2 submit immediately; userOrderId -> m_strRemark
        passorder(op_type, 1101, _account_id(batch), order["stock_code"],
                  11, price, int(order["quantity"]),
                  STRATEGY_NAME, 2, coid, ContextInfo)
        _write_fill(batch, order, "ACCEPTED", 0, 0.0, "", "submitted")
        _log("passorder %s %s x%d @ %s (fallback_price %s) (%s)"
             % (order["side"], order["stock_code"], order["quantity"],
                price, order["limit_price"], coid))
        return True
    except Exception:
        _write_fill(batch, order, "ERROR", 0, 0.0, "",
                    "passorder exception: " + traceback.format_exc()[-200:])
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


def _poll_status(batch):
    """Update fills from broker order details (LIVE only)."""
    live = batch.header.get("mode") == "LIVE"
    if not live:
        return
    details = _get_orders_by_remark(_account_id(batch))
    for order in batch.orders:
        coid = order["client_order_id"]
        if coid not in batch.submitted or _order_is_terminal(batch, coid):
            continue
        d = details.get(coid)
        if d is None:
            continue
        status = int(getattr(d, "m_nOrderStatus", -1))
        traded = int(getattr(d, "m_nVolumeTraded", 0))
        price = float(getattr(d, "m_dTradedPrice", 0.0))
        sysid = getattr(d, "m_strOrderSysID", "")
        if status == STATUS_SUCCEEDED:
            _write_fill(batch, order, "FILLED", traded, price, sysid, "")
        elif status in (STATUS_PART_CANCEL, STATUS_CANCELED):
            if traded > 0:
                _write_fill(batch, order, "PARTIAL", traded, price, sysid, "canceled")
            else:
                _write_fill(batch, order, "EXPIRED", 0, 0.0, sysid, "canceled")
        elif status == STATUS_JUNK:
            _write_fill(batch, order, "REJECTED", 0, 0.0, sysid, "junk order")
        elif status == STATUS_PART_SUCC and traded > 0:
            # intermediate partial; record progress, stays non-terminal via ACCEPTED
            _write_fill(batch, order, "ACCEPTED", traded, price, sysid, "partial in progress")


def _process_batch(ContextInfo, batch):
    now = _now_hms()
    if now < TRADE_START:
        return
    if now >= CANCEL_AT:
        # _force_finalize_if_near_close owns polling/cancel from this point.
        # Never place a fresh order after the cancellation cutoff.
        return
    if not batch.trading_started:
        # batch may have been claimed hours before the trade window opens;
        # restart the sell-wait timer at the first real trading pass
        batch.trading_started = True
        batch.phase_started = time.time()

    mode_live = (batch.header.get("mode") == "LIVE"
                 and _live_ok(batch.header.get("trade_date", "")))
    if batch.header.get("mode") == "LIVE" and not mode_live:
        _log("LIVE batch but LIVE_OK switch missing -> simulate/skip")

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
            _submit(ContextInfo, batch, order, mode_live)

        _poll_status(batch)
        sells_done = all(_order_is_terminal(batch, o["client_order_id"])
                         for o in sells) if sells else True
        timed_out = (time.time() - batch.phase_started) > SELL_WAIT_TIMEOUT_SEC
        if sells_done or timed_out or not sells:
            batch.phase = "BUY"
            batch.phase_started = time.time()
            _save_active_state(batch)
            if timed_out and not sells_done:
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
            if mode_live:
                price = _effective_price(ContextInfo, order)
                quantity = _max_affordable_quantity(
                    batch.remaining_cash, price, order["quantity"])
                if quantity <= 0:
                    batch.submitted[order["client_order_id"]] = True
                    _save_active_state(batch)
                    _write_fill(batch, order, "SKIPPED", 0, 0.0, "",
                                "insufficient reserved cash: %.2f"
                                % batch.remaining_cash)
                    continue
                if quantity < order["quantity"]:
                    order["quantity"] = quantity
                    _log("shrink buy %s to %d shares (reserved cash %.2f)"
                         % (order["stock_code"], quantity,
                            batch.remaining_cash))
                reserved = _estimated_buy_cost(order["quantity"], price)
                batch.remaining_cash = max(0.0, batch.remaining_cash - reserved)
                _save_active_state(batch)
                _submit(ContextInfo, batch, order, True, price=price)
            else:
                _submit(ContextInfo, batch, order, False)

        _poll_status(batch)
        all_done = all(_order_is_terminal(batch, o["client_order_id"])
                       for o in batch.orders)
        if all_done:
            _finalize_batch(batch)


def _force_finalize_if_near_close(ContextInfo, batch):
    now = _now_hms()
    if now < CANCEL_AT:
        return
    # LIVE_OK gates *new* submissions only. Once a LIVE order was submitted,
    # removing the switch must not disable status polling or close-time cancel.
    if batch.header.get("mode") == "LIVE":
        details = _get_orders_by_remark(_account_id(batch))
        for order in batch.orders:
            coid = order["client_order_id"]
            if coid in batch.submitted and not _order_is_terminal(batch, coid):
                d = details.get(coid)
                if d is not None:
                    _cancel_by_detail(d, _account_id(batch), ContextInfo)
        _poll_status(batch)

    if now >= FINALIZE_AT:
        cash_unavailable = (
            batch.phase == "BUY" and batch.remaining_cash is None
        )
        for order in batch.orders:
            coid = order["client_order_id"]
            if not _order_is_terminal(batch, coid):
                fill = batch.fills.get(coid)
                traded = fill["filled_qty"] if fill else 0
                price = fill["avg_price"] if fill else 0.0
                if (cash_unavailable and order["side"] == "BUY"
                        and coid not in batch.submitted):
                    _write_fill(batch, order, "ERROR", 0, 0.0, "",
                                "account cash unavailable at close")
                elif traded > 0:
                    _write_fill(batch, order, "PARTIAL", traded, price, "",
                                "expired at close")
                else:
                    _write_fill(batch, order, "EXPIRED", 0, 0.0, "",
                                "expired at close")
        _finalize_batch(batch)

# ======================= QMT entry points =======================


def init(ContextInfo):
    _ensure_dirs()
    _load_processed()
    _recover_processing_batch()
    g.loaded = True
    _log("initialized, bridge_root=%s, %d processed batches"
         % (BRIDGE_ROOT, len(g.processed)))


def handlebar(ContextInfo):
    try:
        if not ContextInfo.is_last_bar():
            return
        if not g.loaded:
            init(ContextInfo)
        now = time.time()
        if now - g.last_poll < POLL_SECONDS:
            return
        g.last_poll = now

        _recover_processing_batch()
        _claim_new_batch()
        if g.batch is not None:
            _force_finalize_if_near_close(ContextInfo, g.batch)
        if g.batch is not None:
            _process_batch(ContextInfo, g.batch)
    except Exception:
        _log("handlebar error:\n" + traceback.format_exc())
