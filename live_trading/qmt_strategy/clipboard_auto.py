"""External Windows daily orchestration. No Qlib or third-party dependencies."""

import datetime as dt
import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path

from qmt_clipboard import MAX_REQUEST_CHARS, PROTOCOL, SHANGHAI, TERMINAL, _identifier, dumps


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def finite(value, name, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError("invalid " + name)
    return float(value)


def load_batch(path, strategy, today):
    """Validate the complete immutable v2 batch before preparing any order."""
    path = Path(path)
    if path.stat().st_size > MAX_REQUEST_CHARS:
        raise ValueError("signal file too large")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines or any(not line.strip() for line in lines):
        raise ValueError("empty signal or blank record")
    header = json.loads(lines[0])
    checksum = "sha256:" + hashlib.sha256("".join(lines[1:]).encode("utf-8")).hexdigest()
    if checksum != header.get("checksum") or checksum != path.with_suffix(".done").read_text().strip():
        raise ValueError("signal checksum mismatch")
    batch_id = _identifier(header.get("batch_id"), "batch_id")
    if (
        header.get("type") != "batch_header"
        or header.get("schema_version") != "2.0"
        or header.get("strategy_id") != strategy
        or header.get("trade_date") != today
        or header.get("account_type") != "STOCK"
        or header.get("mode", "LIVE") not in ("", "LIVE")
        or path.name != "signal_" + batch_id + ".jsonl"
        or not batch_id.startswith(today.replace("-", "") + "_" + strategy + "_")
    ):
        raise ValueError("wrong batch identity/date/protocol/account type or simulation mode")
    orders = [json.loads(line) for line in lines[1:]]
    if type(header.get("order_count")) is not int or header["order_count"] != len(orders) or len(orders) > 40:
        raise ValueError("batch order count mismatch or exceeds 40")
    seen = set()
    for order in orders:
        identifier = _identifier(order.get("client_order_id"), "client_order_id")
        if identifier in seen or len(identifier) > 24:
            raise ValueError("duplicate or long client_order_id")
        seen.add(identifier)
        if (
            order.get("type") != "order"
            or order.get("batch_id") != batch_id
            or order.get("side") not in ("BUY", "SELL")
            or not isinstance(order.get("stock_code"), str)
            or not re.match(r"^(60\d{4}\.SH|68\d{4}\.SH|00\d{4}\.SZ|30\d{4}\.SZ)$", order["stock_code"])
            or order.get("price_type") != "CLOSE_AUCTION_LIMIT"
            or finite(order.get("limit_price"), "limit_price") != 0
        ):
            raise ValueError("unsupported order instrument/type/price")
        qty = order.get("quantity")
        target = finite(order.get("target_value"), "target_value")
        cap = order.get("max_quantity", 0) or 0
        if type(qty) is not int or not 0 <= qty <= 10000000:
            raise ValueError("invalid quantity")
        if type(cap) is not int or cap < 0 or cap % 100 or type(order.get("priority")) is not int:
            raise ValueError("invalid cap or priority")
        if (order["side"] == "BUY" and (qty != 0 or target <= 0)) or (
            order["side"] == "SELL" and (qty <= 0 or target != 0 or cap != 0)
        ):
            raise ValueError("invalid side quantity/target/cap")
    return dict(header=header, orders=sorted(orders, key=lambda o: o["priority"]))


def prepare_orders(orders, snapshot, now):
    """Resolve the existing live policy using current broker cash and price evidence."""
    cash = finite(snapshot["account"]["available_cash"], "available cash")
    positions = {p["stock_code"]: p for p in snapshot["positions"]}
    prepared, skipped = [], []
    for item in orders:
        code, side = item["stock_code"], item["side"]
        quote = snapshot["quotes"][code]
        stamp = dt.datetime.strptime(quote["timetag"], "%Y%m%d %H:%M:%S").replace(tzinfo=SHANGHAI)
        if stamp.date() != now.date() or not -5 <= (now - stamp).total_seconds() <= 180:
            raise ValueError("stale quote for " + code)
        last = finite(quote["last"], "last price", minimum=0.000001)
        if side == "BUY":
            price = math.ceil(round(last * 1.002 * 100, 6)) / 100
            if quote.get("upper") is not None and finite(quote["upper"], "upper limit") > 0:
                price = min(price, quote["upper"])
        else:
            price = math.floor(round(last * 0.998 * 100, 6)) / 100
            if quote.get("lower") is not None and finite(quote["lower"], "lower limit") > 0:
                price = max(price, quote["lower"])
        price = round(price, 2)
        finite(price, "resolved price", minimum=0.01)
        minimum = 200 if code.startswith("688") else 100
        reason = ""
        if side == "BUY":
            quantity = int((item["target_value"] / last + 0.1) // 100) * 100
            quantity = min(quantity, int((item["target_value"] / price + 0.1) // 100) * 100)
            if item.get("max_quantity"):
                quantity = min(quantity, item["max_quantity"])
            quantity = min(quantity, int(cash / price / 100) * 100)

            def cost(qty):
                amount = qty * price
                return amount + max(amount * 0.0000854, 5) + amount * 0.00001

            while quantity > 0 and cost(quantity) > cash + 1e-9:
                quantity -= 100
            if quantity < minimum:
                quantity = 0
                reason = "available cash/budget below minimum order size"
            else:
                cash -= cost(quantity)
        else:
            quantity = item["quantity"]
            holding = positions.get(code, {"shares": 0, "can_use_volume": 0})
            if quantity > holding["can_use_volume"]:
                reason = "requested sell exceeds broker sellable position"
            elif (quantity < minimum or quantity % 100) and quantity != holding["shares"]:
                reason = "odd/minimum sell must clear the entire position"
        if reason:
            skipped.append(
                dict(
                    id=item["client_order_id"],
                    status="SKIPPED",
                    error=reason,
                    quantity=quantity,
                    filled_quantity=0,
                    average_price=0,
                    broker_orders=[],
                )
            )
        else:
            prepared.append(dict(id=item["client_order_id"], symbol=code, side=side, quantity=quantity, price=price))
    return prepared, skipped


class AutoRelay:
    """One strategy/day; persisted requests are never rebound to a new QMT session."""

    def __init__(self, bridge_root, strategy, state_dir, exchange, now=None):
        self.root = Path(bridge_root)
        self.strategy = _identifier(strategy, "strategy")
        self.state_dir = Path(state_dir)
        self.exchange = exchange
        self.now = now or (lambda: dt.datetime.now(SHANGHAI))
        self.day = self.now().astimezone(SHANGHAI).date().isoformat()
        self.journal_path = self.state_dir / (self.day + "_" + strategy + ".json")
        self.journal = json.loads(self.journal_path.read_text(encoding="utf-8")) if self.journal_path.exists() else None

    def save(self):
        atomic_write(self.journal_path, dumps(self.journal))

    def request(self, kind, **fields):
        session = self.journal.get("session")
        if kind == "poll" and not any(p["request"] for p in self.journal["phases"].values()):
            session = None  # Safe discovery until the first durable trade request.
        return dict(protocol=PROTOCOL, type=kind, session=session, request_id=uuid.uuid4().hex, **fields)

    def call(self, request, allow_expired=False):
        reply = self.exchange(request)
        expected = self.journal.get("session")
        if expected and reply.get("session") != expected and any(p["request"] for p in self.journal["phases"].values()):
            raise RuntimeError("QMT session changed; automatic rebinding forbidden")
        if (
            reply.get("protocol") != PROTOCOL
            or reply.get("type") != "response"
            or reply.get("request_id") != request["request_id"]
        ):
            raise RuntimeError("invalid clipboard response")
        if "snapshot-v1" not in reply.get("capabilities", []):
            raise RuntimeError("update QMT strategy: snapshot-v1 capability missing")
        if reply.get("day") != self.day:
            raise RuntimeError("QMT has unresolved orders from a previous day")
        self.journal["session"] = _identifier(reply.get("session"), "session")
        self.journal["latest"] = reply
        self.save()
        if not reply.get("ok") and not (allow_expired and "expired" in reply.get("error", "")):
            raise RuntimeError(reply.get("error") or "QMT rejected request")
        return reply

    def discover(self):
        prefix = "signal_" + self.day.replace("-", "") + "_" + self.strategy + "_"
        paths = sorted((self.root / "inbox").glob(prefix + "*.done"))
        if len(paths) > 1:
            raise ValueError("multiple batches for today; refusing ambiguous orders")
        batch = load_batch(paths[0].with_suffix(".jsonl"), self.strategy, self.day) if paths else None
        if self.journal:
            if self.journal.get("day") != self.day or self.journal.get("strategy") != self.strategy:
                raise ValueError("journal identity mismatch")
            if batch and batch != self.journal["batch"]:
                raise ValueError("batch changed after journal creation")
        elif batch:
            for path in self.state_dir.glob("????-??-??_" + self.strategy + ".json"):
                previous = json.loads(path.read_text(encoding="utf-8"))
                if previous["day"] < self.day and not previous["exported"]:
                    raise RuntimeError("previous day has unresolved journal: " + path.name)
            self.journal = dict(
                day=self.day,
                strategy=self.strategy,
                batch=batch,
                session=None,
                phases={},
                latest=None,
                exported=False,
                snapshot_exported=False,
            )
            self.save()

    def dispatch(self, phase):
        request = phase["request"]
        reply = self.call(request, allow_expired=True)
        known = {item["id"] for item in reply["orders"]}
        if not reply["ok"]:
            phase["skipped"].extend(
                dict(
                    id=o["id"],
                    status="EXPIRED",
                    quantity=o["quantity"],
                    filled_quantity=0,
                    average_price=0,
                    broker_orders=[],
                    error=reply["error"],
                )
                for o in request["orders"]
                if o["id"] not in known
            )
        phase["acknowledged"] = True
        self.save()

    def check(self):
        """Deployment preflight: query only, even during a trading window."""
        self.discover()
        if self.journal is None:
            return dict(day=self.day, status="NO_SIGNAL", batch_id=None)
        if any(not p["acknowledged"] for p in self.journal["phases"].values()):
            raise RuntimeError("pending trade in journal; use auto to resume the original request")
        self.call(self.request("poll"))
        response = self.call(
            self.request("snapshot", symbols=sorted({o["stock_code"] for o in self.journal["batch"]["orders"]}))
        )
        if response.get("query_error"):
            raise RuntimeError(response["query_error"])
        return dict(
            day=self.day,
            status="READY",
            batch_id=self.journal["batch"]["header"]["batch_id"],
            session=self.journal["session"],
            available_cash=response["snapshot"]["account"]["available_cash"],
            positions=response["snapshot"]["positions"],
            quotes=response["snapshot"]["quotes"],
        )

    def step(self):
        now = self.now().astimezone(SHANGHAI)
        if now.date().isoformat() != self.day:
            raise RuntimeError("daily process crossed midnight; start a new scheduled run")
        self.discover()
        if not self.journal:
            return None
        phases = self.journal["phases"]
        # Resume an in-flight request before poll: never overwrite a pending trade.
        for phase in phases.values():
            if phase["request"] and not phase["acknowledged"]:
                self.dispatch(phase)
        reply = self.call(self.request("poll"))
        for side, start, end in (("SELL", "14:55:00", "14:57:05"), ("BUY", "14:57:05", "14:59:00")):
            if side in phases:
                continue
            orders = [o for o in self.journal["batch"]["orders"] if o["side"] == side]
            wall = now.strftime("%H:%M:%S")
            if orders and wall < start:
                continue
            prepared, skipped = [], []
            if orders and wall >= end:
                skipped = [
                    dict(
                        id=o["client_order_id"],
                        status="EXPIRED",
                        quantity=o["quantity"],
                        filled_quantity=0,
                        average_price=0,
                        broker_orders=[],
                        error="submission window missed",
                    )
                    for o in orders
                ]
            elif orders:
                if reply.get("query_error"):
                    return reply
                if side == "BUY":
                    states = {o["id"]: o["status"] for o in reply["orders"]}
                    sell = phases.get("SELL")
                    if sell is None or any(
                        states.get(o["id"]) in (None, "UNKNOWN", "SUBMITTED")
                        for o in (sell["request"] or {}).get("orders", [])
                    ):
                        return reply
                observation = self.call(self.request("snapshot", symbols=sorted({o["stock_code"] for o in orders})))
                if observation.get("query_error"):
                    return observation
                # The observation may have taken seconds; never submit outside the window.
                observed_at = self.now().astimezone(SHANGHAI)
                if observed_at.strftime("%H:%M:%S") >= end:
                    return observation
                prepared, skipped = prepare_orders(orders, observation["snapshot"], observed_at)
            request = (
                self.request("trade", orders=prepared, expires_at=self.day + "T" + end + "+08:00") if prepared else None
            )
            phase = dict(request=request, skipped=skipped, acknowledged=not prepared)
            phases[side] = phase
            self.save()  # Durable journal BEFORE any clipboard write that can trade.
            if request:
                self.dispatch(phase)
                reply = self.journal["latest"]
        self.export_fills()
        if now.strftime("%H:%M:%S") >= "17:00:00" and not self.journal["snapshot_exported"]:
            observed = self.call(self.request("snapshot", symbols=[]))
            self.export_account(observed["snapshot"], observed["ts"])
        return self.journal["latest"]

    def publish(self, prefix, rows):
        batch_id = self.journal["batch"]["header"]["batch_id"]
        path = self.root / "outbound" / (prefix + "_" + batch_id + ".jsonl")
        atomic_write(path, "".join(dumps(row) + "\n" for row in rows))
        atomic_write(path.with_suffix(".done"), "done\n")

    def export_fills(self):
        state = self.journal
        if state["exported"] or len(state["phases"]) != 2 or state["latest"].get("query_error"):
            return
        known = {item["id"]: item for item in state["latest"]["orders"]}
        for phase in state["phases"].values():
            if not phase["acknowledged"]:
                return
            known.update({item["id"]: item for item in phase["skipped"]})
        rows = []
        for order in state["batch"]["orders"]:
            result = known.get(order["client_order_id"])
            if result is None or result["status"] not in TERMINAL + ("SKIPPED",):
                return
            filled = result["filled_quantity"]
            average = finite(result["average_price"], "fill price")
            if filled and average <= 0:
                raise ValueError("positive fill has no usable price")
            status = result["status"]
            if status == "CANCELED":
                status = "PARTIAL" if filled else "SKIPPED"
            requested = result["quantity"]
            message = result.get("error", "")
            if order["side"] == "BUY" and requested == 0 and filled == 0:
                # Legacy importer requires positive requested_qty even when no
                # order was submitted; match the old bridge's 100-share marker.
                requested = 100
                message = "not submitted (legacy requested_qty marker=100); " + message
            rows.append(
                dict(
                    type="fill_event",
                    batch_id=state["batch"]["header"]["batch_id"],
                    client_order_id=order["client_order_id"],
                    mode="LIVE",
                    stock_code=order["stock_code"],
                    side=order["side"],
                    status=status,
                    requested_qty=requested,
                    filled_qty=filled,
                    avg_price=average,
                    qmt_order_id=",".join(o["broker_order_id"] for o in result["broker_orders"]),
                    message=message,
                    ts=state["latest"]["ts"],
                    netted_qty=0,
                    netting_close=0.0,
                    intended_qty=requested,
                )
            )
        self.publish("fills", rows)
        state["exported"] = True
        self.save()

    def export_account(self, snapshot, stamp):
        common = dict(batch_id=self.journal["batch"]["header"]["batch_id"], trade_date=self.day, ts=stamp)
        rows = [dict(type="account_snapshot", **common, **snapshot["account"])]
        rows.extend(dict(type="broker_position", **common, **position) for position in snapshot["positions"])
        self.publish("account", rows)
        self.journal["snapshot_exported"] = True
        self.save()

    def complete(self):
        return self.journal is None or (self.journal["exported"] and self.journal["snapshot_exported"])
