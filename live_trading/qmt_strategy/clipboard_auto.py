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

GUARDED_TOPK_PRICE_TYPE = "CLOSE_AUCTION_TOPK_CASH_V1"


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
    guarded = any(o.get("price_type") == GUARDED_TOPK_PRICE_TYPE for o in orders)
    if guarded:
        maximum, risk = orders[0].get("max_positions"), orders[0].get("cash_risk_degree")
        if type(maximum) is not int or not 0 < maximum <= 40 or not 0 < finite(risk, "cash risk degree") <= 1:
            raise ValueError("invalid guarded cash contract")
        if any(o.get("price_type") != GUARDED_TOPK_PRICE_TYPE
               or o.get("max_positions") != maximum or o.get("cash_risk_degree") != risk
               or type(o.get("max_positions")) is not int
               or type(o.get("cash_risk_degree")) not in (int, float) for o in orders):
            raise ValueError("all orders must share one guarded cash contract")
        if len({o.get("stock_code") for o in orders}) != len(orders):
            raise ValueError("guarded contract forbids duplicate or paired symbols")
    elif any(o.get("max_positions", 0) != 0 or o.get("cash_risk_degree", 0) != 0 for o in orders):
        raise ValueError("cash contract requires guarded price_type")
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
            or order.get("price_type") != (GUARDED_TOPK_PRICE_TYPE if guarded else "CLOSE_AUCTION_LIMIT")
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
        if (order["side"] == "BUY" and (qty != 0 or (target != 0 if guarded else target <= 0))) or (
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


def unsubmitted(order, reason, status="SKIPPED"):
    return dict(id=order["client_order_id"], status=status, error=reason,
                quantity=order["quantity"], filled_quantity=0, average_price=0, broker_orders=[])


def buy_cost(order):
    amount = order["quantity"] * order["price"]
    return amount + max(amount * 0.0000854, 5) + amount * 0.00001


def guarded_snapshot(snapshot):
    """Fail closed on incomplete broker evidence, including unrelated holdings/orders."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("account"), dict):
        raise ValueError("invalid guarded account snapshot")
    finite(snapshot["account"].get("available_cash"), "available cash")
    finite(snapshot["account"].get("frozen_cash"), "frozen cash")
    if not isinstance(snapshot.get("positions"), list) or not isinstance(snapshot.get("working_orders"), list):
        raise ValueError("complete positions and working_orders snapshot required")
    held, seen = set(), set()
    for position in snapshot["positions"]:
        code = position.get("stock_code")
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code) or code in seen:
            raise ValueError("invalid guarded positions")
        seen.add(code)
        shares, usable = position.get("shares"), position.get("can_use_volume")
        if type(shares) is not int or type(usable) is not int or not 0 <= usable <= shares:
            raise ValueError("invalid guarded position volume")
        if shares:
            held.add(code)
    for order in snapshot["working_orders"]:
        if (not isinstance(order, dict) or not isinstance(order.get("remark"), str)
                or not isinstance(order.get("broker_order_id"), str)
                or type(order.get("status")) is not int or order["status"] not in (48, 49, 50, 51, 52, 55)
                or type(order.get("quantity")) is not int or order["quantity"] <= 0
                or type(order.get("filled_quantity")) is not int
                or not 0 <= order["filled_quantity"] <= order["quantity"]
                or order.get("side") not in ("BUY", "SELL")
                or not isinstance(order.get("stock_code"), str)
                or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", order["stock_code"])):
            raise ValueError("invalid working_orders snapshot")
    return held


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

    def guarded(self):
        return bool(self.journal and self.journal["batch"]["orders"]
                    and self.journal["batch"]["orders"][0]["price_type"] == GUARDED_TOPK_PRICE_TYPE)

    def has_requests(self):
        return any(p["request"] or p.get("requests") for p in self.journal["phases"].values())

    def request(self, kind, **fields):
        session = self.journal.get("session")
        if kind == "poll" and not self.has_requests():
            session = None  # Safe discovery until the first durable trade request.
        return dict(protocol=PROTOCOL, type=kind, session=session, request_id=uuid.uuid4().hex, **fields)

    def call(self, request, allow_expired=False):
        reply = self.exchange(request)
        expected = self.journal.get("session")
        if expected and reply.get("session") != expected and self.has_requests():
            raise RuntimeError("QMT session changed; automatic rebinding forbidden")
        if (
            reply.get("protocol") != PROTOCOL
            or reply.get("type") != "response"
            or reply.get("request_id") != request["request_id"]
        ):
            raise RuntimeError("invalid clipboard response")
        if "snapshot-v1" not in reply.get("capabilities", []):
            raise RuntimeError("update QMT strategy: snapshot-v1 capability missing")
        if self.guarded() and "topk-cash-v1" not in reply.get("capabilities", []):
            raise RuntimeError("update QMT strategy: topk-cash-v1 capability missing")
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
        if self.guarded():
            held = guarded_snapshot(response.get("snapshot"))
            if "BUY" in self.journal["phases"]:
                self.validate_guarded_plan(self.journal["phases"]["BUY"])
            self.working_orders_clear(response["snapshot"])
            if len(held) > self.journal["batch"]["orders"][0]["max_positions"]:
                raise ValueError("held positions exceed guarded max_positions")
        result = dict(
            day=self.day,
            status="READY",
            batch_id=self.journal["batch"]["header"]["batch_id"],
            session=self.journal["session"],
            available_cash=response["snapshot"]["account"]["available_cash"],
            positions=response["snapshot"]["positions"],
            quotes=response["snapshot"]["quotes"],
        )
        if self.guarded():
            contract = self.journal["batch"]["orders"][0]
            result.update(max_positions=contract["max_positions"], cash_risk_degree=contract["cash_risk_degree"],
                          working_orders=response["snapshot"]["working_orders"], held_names=len(held))
        return result

    def working_orders_clear(self, snapshot):
        """Only exact durably attempted BUY reservations may still be working."""
        buy = self.journal["phases"].get("BUY", {})
        reserved = set(buy.get("cash_plan", {}).get("reserved", []))
        known = {}
        for child in buy.get("requests", []):
            if not child.get("attempted"):
                continue
            for order in child["request"]["orders"]:
                if order["id"] in reserved:
                    remark = "qc" + hashlib.sha256((self.journal["session"] + "/" + order["id"]).encode("ascii")).hexdigest()[:20]
                    known[remark] = order
        remarks, broker_ids = set(), set()
        for active in snapshot["working_orders"]:
            order = known.get(active["remark"])
            if (order is None or active["side"] != "BUY" or active["stock_code"] != order["symbol"]
                    or active["quantity"] != order["quantity"]):
                raise RuntimeError("foreign or unreserved working order blocks guarded trading")
            if active["remark"] in remarks or (active["broker_order_id"] and active["broker_order_id"] in broker_ids):
                raise RuntimeError("multiple working broker orders exceed one guarded reservation")
            remarks.add(active["remark"])
            broker_ids.add(active["broker_order_id"])

    def guarded_observation(self, symbols):
        observation = self.call(self.request("snapshot", symbols=symbols))
        if observation.get("query_error"):
            raise RuntimeError(observation["query_error"])
        guarded_snapshot(observation.get("snapshot"))
        self.working_orders_clear(observation["snapshot"])
        return observation

    def guarded_plan(self, orders, observation, observed_at):
        snapshot = observation["snapshot"]
        held = guarded_snapshot(snapshot)
        contract = self.journal["batch"]["orders"][0]
        slots = max(0, contract["max_positions"] - len(held))
        candidates = [o for o in orders if o["stock_code"] not in held][:slots]
        cash = snapshot["account"]["available_cash"]
        allocation = cash * contract["cash_risk_degree"] / len(candidates) if candidates else 0
        prepared, skipped = [], []
        identifiers = {o["client_order_id"] for o in candidates}
        for order in orders:
            if order["client_order_id"] not in identifiers:
                skipped.append(unsubmitted(order, "no executable guarded position slot"))
                continue
            # A failed quote or minimum lot leaves this original allocation idle.
            local = dict(snapshot, account=dict(snapshot["account"], available_cash=allocation))
            try:
                ready, rejected = prepare_orders([dict(order, target_value=allocation)], local, observed_at)
                prepared.extend(ready)
                skipped.extend(rejected)
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                skipped.append(unsubmitted(order, "guarded quote/sizing unavailable: " + str(exc)))
        plan = dict(cash=cash, allocation=allocation, held=sorted(held),
                    candidates=[o["client_order_id"] for o in candidates],
                    reserved=[o["id"] for o in prepared], prepared=prepared,
                    snapshot=snapshot, observed_at=observed_at.isoformat())
        children = [dict(request=self.request("trade", orders=[o], expires_at=self.day + "T14:59:00+08:00"),
                         skipped=[], acknowledged=False, attempted=False) for o in prepared]
        return dict(request=None, requests=children, cash_plan=plan, skipped=skipped, acknowledged=not children)

    def validate_guarded_plan(self, phase):
        """Recompute every frozen ceiling from saved evidence before a resume can trade."""
        plan = phase.get("cash_plan")
        if plan is None:
            if phase.get("requests"):
                raise ValueError("guarded BUY requests have no durable cash plan")
            return
        snapshot = plan["snapshot"]
        held = guarded_snapshot(snapshot)
        orders = [o for o in self.journal["batch"]["orders"] if o["side"] == "BUY"]
        reference = self.guarded_plan(orders, {"snapshot": snapshot}, dt.datetime.fromisoformat(plan["observed_at"]))
        if reference["cash_plan"] != plan:
            raise ValueError("guarded frozen cash plan changed or exceeds its ceilings")
        children = phase.get("requests", [])
        if len(children) != len(plan["prepared"]):
            raise ValueError("guarded requests differ from frozen reservations")
        identifiers = set()
        for child, order in zip(children, plan["prepared"]):
            request = child["request"]
            identifier = _identifier(request.get("request_id"), "request_id")
            if (identifier in identifiers or request.get("orders") != [order]
                    or request.get("session") != self.journal["session"]
                    or request.get("expires_at") != self.day + "T14:59:00+08:00"
                    or request.get("type") != "trade" or request.get("protocol") != PROTOCOL):
                raise ValueError("guarded original trade request changed")
            identifiers.add(identifier)
        names = held | {o["symbol"] for o in plan["prepared"]}
        if len(names) > self.journal["batch"]["orders"][0]["max_positions"]:
            raise ValueError("guarded reservations exceed max_positions")

    def guarded_step(self, now):
        phases = self.journal["phases"]
        if "BUY" in phases:
            self.validate_guarded_plan(phases["BUY"])
        # Capability and session discovery always precede any resumed submission.
        reply = self.call(self.request("poll"))
        if reply.get("query_error"):
            return reply
        sell = phases.get("SELL")
        if sell and sell["request"] and not sell["acknowledged"]:
            self.dispatch(sell)
            reply = self.journal["latest"]
        orders = self.journal["batch"]["orders"]
        wall = now.strftime("%H:%M:%S")
        if sell is None:
            sells = [o for o in orders if o["side"] == "SELL"]
            if not sells or wall >= "14:55:00":
                prepared, skipped = [], []
                if sells and wall >= "14:57:05":
                    skipped = [unsubmitted(o, "submission window missed", "EXPIRED") for o in sells]
                elif sells:
                    observed = self.guarded_observation(sorted({o["stock_code"] for o in sells}))
                    observed_at = self.now().astimezone(SHANGHAI)
                    if observed_at.strftime("%H:%M:%S") >= "14:57:05":
                        return observed
                    for order in sells:
                        try:
                            ready, rejected = prepare_orders([order], observed["snapshot"], observed_at)
                            prepared.extend(ready)
                            skipped.extend(rejected)
                        except (ValueError, KeyError, TypeError, OverflowError) as exc:
                            skipped.append(unsubmitted(order, "guarded sell unavailable: " + str(exc)))
                request = self.request("trade", orders=prepared, expires_at=self.day + "T14:57:05+08:00") if prepared else None
                sell = phases["SELL"] = dict(request=request, skipped=skipped, acknowledged=not prepared)
                self.save()
                if request:
                    self.dispatch(sell)
                    reply = self.journal["latest"]
        if "BUY" not in phases and wall >= "14:57:05":
            buys = [o for o in orders if o["side"] == "BUY"]
            states = {o["id"]: o["status"] for o in reply["orders"]}
            if sell:
                states.update({o["id"]: o["status"] for o in sell["skipped"]})
            if wall >= "14:59:00":
                phases["BUY"] = dict(request=None, skipped=[unsubmitted(o, "submission window missed", "EXPIRED") for o in buys], acknowledged=True)
            elif sell is None or any(states.get(o["client_order_id"]) not in TERMINAL + ("SKIPPED",) for o in orders if o["side"] == "SELL"):
                return reply
            elif buys:
                observed = self.guarded_observation(sorted({o["stock_code"] for o in buys}))
                observed_at = self.now().astimezone(SHANGHAI)
                if observed_at.strftime("%H:%M:%S") >= "14:59:00":
                    return observed
                phases["BUY"] = self.guarded_plan(buys, observed, observed_at)
            else:
                phases["BUY"] = dict(request=None, skipped=[], acknowledged=True)
            self.save()  # Freeze ALL budgets, quantities, prices and original request IDs before the first BUY.
        buy = phases.get("BUY")
        if buy and buy.get("cash_plan"):
            self.validate_guarded_plan(buy)
            reserved_cost = sum(buy_cost(c["request"]["orders"][0]) for c in buy["requests"] if c["attempted"])
            for child in buy["requests"]:
                if child["acknowledged"]:
                    continue
                order = child["request"]["orders"][0]
                if child["attempted"]:
                    known = next((o for o in self.journal["latest"]["orders"] if o["id"] == order["id"]), None)
                    if known is not None:
                        if any(known.get(k) != v for k, v in order.items()):
                            raise RuntimeError("executor order differs from frozen guarded request")
                        # Only executor evidence proves this replay cannot submit a new order.
                        self.dispatch(child)
                        continue
                    # A crash can occur after reserving but BEFORE writing the clipboard.
                    # An absent executor ID must pass all current gates using the same frozen request.
                observed = self.guarded_observation([])
                current = self.now().astimezone(SHANGHAI)
                held = guarded_snapshot(observed["snapshot"])
                reserved_names = {o["symbol"] for o in buy["cash_plan"]["prepared"]}
                ceiling = orders[0]["max_positions"]
                remaining = buy["cash_plan"]["cash"] - reserved_cost + (buy_cost(order) if child["attempted"] else 0)
                cash = min(observed["snapshot"]["account"]["available_cash"], remaining)
                reason = ""
                if current.strftime("%H:%M:%S") >= "14:59:00":
                    reason = "submission window missed"
                elif order["symbol"] in held or len(held | set(buy["cash_plan"]["held"]) | reserved_names) > ceiling:
                    reason = "guarded residual positions/reservations exceed position slot"
                elif buy_cost(order) > cash + 1e-9:
                    reason = "fresh cash below frozen guarded order cost"
                if reason:
                    original = next(o for o in orders if o["client_order_id"] == order["id"])
                    child["skipped"].append(unsubmitted(original, reason, "EXPIRED" if "window" in reason else "SKIPPED"))
                    child["acknowledged"] = True
                    buy["skipped"].extend(child["skipped"])
                    self.save()
                    continue
                if not child["attempted"]:
                    reserved_cost += buy_cost(order)
                child["attempted"] = True
                self.save()  # Reserve names and cash even when submit/response is uncertain.
                self.dispatch(child)
            buy["acknowledged"] = all(c["acknowledged"] for c in buy["requests"])
            for child in buy["requests"]:
                for skipped in child["skipped"]:
                    if skipped not in buy["skipped"]:
                        buy["skipped"].append(skipped)
            self.save()
        self.export_fills()
        if now.strftime("%H:%M:%S") >= "17:00:00" and not self.journal["snapshot_exported"]:
            observed = self.guarded_observation([])
            self.export_account(observed["snapshot"], observed["ts"])
        return self.journal["latest"]

    def step(self):
        now = self.now().astimezone(SHANGHAI)
        if now.date().isoformat() != self.day:
            raise RuntimeError("daily process crossed midnight; start a new scheduled run")
        self.discover()
        if not self.journal:
            return None
        if self.guarded():
            return self.guarded_step(now)
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
