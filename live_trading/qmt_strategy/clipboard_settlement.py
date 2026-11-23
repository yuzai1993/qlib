"""Reconcile independent broker evidence for closing ordinary QMT day orders."""

import datetime as dt
import math
import re
from collections import defaultdict


SHANGHAI = dt.timezone(dt.timedelta(hours=8))
CLOSE = dt.time(17)
FIRST_INSERT, LAST_INSERT = dt.time(9, 15), dt.time(15)
# Native order averages can be truncated to three decimals.
PRICE_TOLERANCE = 0.001 + 1e-9
ACTIVE_STATUSES = (48, 49, 50, 51, 52, 55)
CHILD_STATUSES = {
    "ACCEPTED": (50,),
    "FILLED": (56,),
    "CANCELED": (53, 54),
    "REJECTED": (57,),
    "EXPIRED": (53, 54, 57),
}


def _record(value, label):
    if not isinstance(value, dict):
        raise ValueError("missing or invalid " + label)
    return value


def _rows(value, label):
    if not isinstance(value, list):
        raise ValueError("missing or invalid " + label)
    return value


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("invalid " + label)
    return value


def _symbol(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", value):
        raise ValueError("invalid " + label + " symbol")
    return value


def _side(value, label):
    if value not in ("BUY", "SELL"):
        raise ValueError("invalid " + label + " side")
    return value


def _quantity(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("invalid " + label + " quantity")
    return value


def _price(value, label, *, positive=False):
    try:
        if type(value) in (int, float) and math.isfinite(value) and value >= 0 and (value or not positive):
            return float(value)
    except OverflowError:
        pass
    raise ValueError("invalid " + label)


def _timestamp(value, label):
    try:
        if isinstance(value, str):
            value = dt.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if isinstance(value, dt.datetime) and value.utcoffset() is not None:
            return value.astimezone(SHANGHAI)
    except (ValueError, OverflowError):
        pass
    raise ValueError("invalid " + label + " timestamp")


def _broker_timestamp(date, time, label):
    if (
        not isinstance(date, str)
        or not re.fullmatch(r"[0-9]{8}", date)
        or not isinstance(time, str)
        or not re.fullmatch(r"(?:[0-9]{6}|[0-9]{2}:[0-9]{2}:[0-9]{2})", time)
    ):
        raise ValueError("invalid " + label + " date/time")
    try:
        return dt.datetime.strptime(date + time.replace(":", ""), "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
    except ValueError as exc:
        raise ValueError("invalid " + label + " date/time") from exc


def _positions(snapshot):
    holdings = {}
    for row in _rows(snapshot.get("positions"), "POSITION rows"):
        position = _record(row, "POSITION row")
        symbol = _symbol(position.get("stock_code"), "POSITION")
        shares = _quantity(position.get("shares"), "POSITION")
        usable = _quantity(position.get("can_use_volume"), "POSITION sellable")
        if usable > shares or symbol in holdings:
            raise ValueError("duplicate or inconsistent POSITION for " + symbol)
        holdings[symbol] = shares
    return {symbol: shares for symbol, shares in holdings.items() if shares}


def _deals(snapshot, until, label):
    if snapshot.get("deals_error") != "":
        raise ValueError(label + " query incomplete")
    result = {}
    for row in _rows(snapshot.get("deals"), label + " rows"):
        deal = _record(row, label + " row")
        stamp = _broker_timestamp(deal.get("trade_date"), deal.get("trade_time"), label)
        if stamp > until:
            raise ValueError(label + " event is later than its snapshot")
        symbol = _symbol(deal.get("stock_code"), label)
        trade_id = _identifier(deal.get("trade_id"), label + " trade ID")
        _identifier(deal.get("broker_order_id"), label + " broker order ID")
        _side(deal.get("side"), label)
        quantity = deal.get("quantity")
        if type(quantity) is float and quantity.is_integer():
            quantity = int(quantity)
        _quantity(quantity, label, minimum=1)
        _price(deal.get("price"), label + " price", positive=True)
        _price(deal.get("amount"), label + " amount", positive=True)
        key = stamp.date(), symbol[-2:], trade_id
        if key in result and result[key][0] != deal:
            raise ValueError("conflicting duplicate " + label + " trade ID " + trade_id)
        result[key] = deal, stamp
    return result


def _journal_orders(journal, day, session):
    batch = _record(journal.get("batch"), "ORDER batch")
    header = _record(batch.get("header"), "ORDER batch header")
    strategy = _identifier(journal.get("strategy"), "ORDER strategy")
    if header.get("trade_date", day) != day or header.get("strategy_id", strategy) != strategy:
        raise ValueError("ORDER batch day or strategy mismatch")
    planned = {}
    for row in _rows(batch.get("orders"), "ORDER batch rows"):
        order = _record(row, "ORDER batch row")
        client_id = _identifier(order.get("client_order_id"), "ORDER client ID")
        if client_id in planned:
            raise ValueError("duplicate ORDER planned client ID " + client_id)
        planned[client_id] = (_symbol(order.get("stock_code"), "ORDER"), _side(order.get("side"), "ORDER"))

    requests, attempted, skipped = {}, set(), set()
    for entry in _record(journal.get("phases"), "ORDER journal phases").values():
        phase = _record(entry, "ORDER phase")
        for entry in [phase] + _rows(phase.get("requests", []), "ORDER phase requests"):
            state = _record(entry, "ORDER request state")
            for row in _rows(state.get("skipped", []), "ORDER skipped rows"):
                skip = _record(row, "ORDER skipped row")
                client_id = skip.get("id")
                if (
                    not isinstance(client_id, str)
                    or client_id not in planned
                    or skip.get("status") not in ("SKIPPED", "EXPIRED")
                    or _quantity(skip.get("filled_quantity"), "ORDER skip")
                    or skip.get("broker_orders") != []
                ):
                    raise ValueError("invalid ORDER skipped result")
                skipped.add(client_id)
            request = state.get("request")
            if request is None:
                continue
            request = _record(request, "ORDER durable request")
            if request.get("session", session) != session:
                raise ValueError("ORDER durable request session mismatch")
            for row in _rows(request.get("orders"), "ORDER durable request rows"):
                order = _record(row, "ORDER durable request row")
                client_id = _identifier(order.get("id"), "ORDER durable request client ID")
                identity = order.get("symbol"), order.get("side")
                if planned.get(client_id) != identity or client_id in requests:
                    raise ValueError("invalid or duplicate ORDER durable request identity " + client_id)
                requests[client_id] = (*identity, _quantity(order.get("quantity"), "ORDER request", minimum=1))
                if state.get("attempted", state.get("acknowledged")) is True:
                    attempted.add(client_id)
    if planned.keys() - (attempted | skipped):
        raise ValueError("ORDER batch contains unconfirmed submissions or missing skipped results")
    return {client_id: requests[client_id] for client_id in requests if client_id in attempted - skipped}


def _observed_orders(observation, submitted):
    parents, children = {}, {}
    for row in _rows(observation.get("orders"), "ORDER parents"):
        parent = _record(row, "ORDER parent")
        client_id = _identifier(parent.get("id"), "ORDER parent client ID")
        quantity = _quantity(parent.get("quantity"), "ORDER parent", minimum=1)
        if client_id in parents or submitted.get(client_id) != (parent.get("symbol"), parent.get("side"), quantity):
            raise ValueError("duplicate or unknown ORDER parent identity " + client_id)
        status = parent.get("status")
        if not isinstance(status, str) or status not in CHILD_STATUSES:
            raise ValueError("unresolved ORDER parent status for " + client_id)
        rows = _rows(parent.get("broker_orders"), "ORDER broker children")
        if len(rows) != 1:
            raise ValueError("ORDER parent needs one broker child for " + client_id)
        child = _record(rows[0], "ORDER broker child")
        broker_id = _identifier(child.get("broker_order_id"), "ORDER broker child ID")
        filled = _quantity(parent.get("filled_quantity"), "ORDER parent fill")
        parent_price = _price(parent.get("average_price"), "ORDER parent average price", positive=bool(filled))
        child_price = _price(child.get("average_price"), "ORDER child average price", positive=bool(filled))
        child_status = child.get("status")
        if (
            broker_id in children
            or type(child_status) is not int
            or child_status not in CHILD_STATUSES[status]
            or _quantity(child.get("quantity"), "ORDER child", minimum=1) != quantity
            or _quantity(child.get("filled_quantity"), "ORDER child fill") != filled
            or child.get("stock_code", parent["symbol"]) != parent["symbol"]
            or child.get("side", parent["side"]) != parent["side"]
            or child.get("remark", parent.get("remark")) != parent.get("remark", child.get("remark"))
            or (status == "FILLED" and filled != quantity)
            or (status == "CANCELED" and filled >= quantity)
            or (status in ("ACCEPTED", "REJECTED", "EXPIRED") and filled)
            or (not filled and (parent_price or child_price))
        ):
            raise ValueError("inconsistent ORDER broker child for " + client_id)
        parents[client_id] = parent
        children[broker_id] = parent, child
    if parents.keys() != submitted.keys():
        raise ValueError("missing submitted ORDER parent")
    return children


def _working_orders(snapshot, children, day, ignored):
    seen, current = set(), set()
    for row in _rows(snapshot.get("working_orders"), "working ORDER rows"):
        order = _record(row, "working ORDER row")
        broker_id = _identifier(order.get("broker_order_id"), "working ORDER broker ID")
        if broker_id in seen:
            raise ValueError("duplicate working ORDER broker ID " + broker_id)
        seen.add(broker_id)
        stamp = _broker_timestamp(order.get("insert_date"), order.get("insert_time"), "working ORDER")
        if stamp.date() < day and broker_id not in children and order in ignored:
            continue
        if broker_id not in children or stamp.date() != day:
            raise ValueError("foreign or wrong-day working ORDER " + broker_id)
        parent, child = children[broker_id]
        status = order.get("status")
        remark = order.get("remark")
        if (
            type(status) is not int
            or status not in ACTIVE_STATUSES
            or status != child["status"]
            or not isinstance(remark, str)
            or remark != parent.get("remark", child.get("remark", remark))
            or order.get("stock_code") != parent["symbol"]
            or order.get("side") != parent["side"]
            or _quantity(order.get("quantity"), "working ORDER", minimum=1) != child["quantity"]
            or _quantity(order.get("filled_quantity"), "working ORDER fill") != child["filled_quantity"]
            or not FIRST_INSERT <= stamp.time() <= LAST_INSERT
        ):
            raise ValueError("inconsistent or nonregular working ORDER " + broker_id)
        current.add(broker_id)
    return current


def _delta_deals(baseline, closing, baseline_at, close_at, children):
    previous = _deals(baseline, baseline_at, "baseline DEAL")
    current = _deals(closing, close_at, "closing DEAL")
    for key, (deal, _) in previous.items():
        if key not in current or current[key][0] != deal:
            raise ValueError("baseline DEAL missing or changed in closing snapshot")
        order = children.get(deal["broker_order_id"])
        if order and key[0] == close_at.date() and key[1] == order[0]["symbol"][-2:]:
            raise ValueError("baseline DEAL already belongs to this batch")
    delta = defaultdict(list)
    for key in current.keys() - previous.keys():
        deal, stamp = current[key]
        broker_id = deal["broker_order_id"]
        if stamp.date() != close_at.date() or stamp < baseline_at.replace(microsecond=0):
            raise ValueError("unmatched DEAL time for " + broker_id)
        if broker_id not in children:
            raise ValueError("unmatched account DEAL for " + broker_id)
        parent, _ = children[broker_id]
        if deal["stock_code"] != parent["symbol"] or deal["side"] != parent["side"]:
            raise ValueError("DEAL symbol or side mismatch for " + broker_id)
        delta[broker_id].append(deal)
    return delta


def _deal_totals(deals, filled, identifier, *native_prices):
    if sum(int(deal["quantity"]) for deal in deals) != filled:
        raise ValueError("DEAL fill quantity mismatch for " + identifier)
    if not filled:
        return 0.0, 0.0
    try:
        for deal in deals:
            if abs(deal["amount"] / int(deal["quantity"]) - deal["price"]) > PRICE_TOLERANCE:
                raise ValueError("DEAL unit price and amount mismatch for " + identifier)
        amount = math.fsum(deal["amount"] for deal in deals)
        average = amount / filled
    except OverflowError as exc:
        raise ValueError("invalid DEAL aggregate for " + identifier) from exc
    if any(abs(average - price) > PRICE_TOLERANCE for price in native_prices):
        raise ValueError("DEAL average price mismatch for " + identifier)
    return amount, average


def confirmed_fill_prices(observation):
    """Return independent deal averages for positive fills on terminal orders."""
    observation = _record(observation, "fill observation")
    if observation.get("ok") is not True or observation.get("query_error") != "":
        raise ValueError("fill observation query incomplete")
    candidates = []
    for row in _rows(observation.get("orders"), "ORDER parents"):
        parent = _record(row, "ORDER parent")
        if parent.get("status") in ("FILLED", "CANCELED"):
            filled = _quantity(parent.get("filled_quantity"), "ORDER parent fill")
            if filled:
                candidates.append(parent)
    if not candidates:
        return {}

    observed_at = _timestamp(observation.get("ts"), "fill observation")
    if observation.get("day") != observed_at.date().isoformat():
        raise ValueError("fill observation day mismatch")
    snapshot = _record(observation.get("snapshot"), "fill DEAL snapshot")
    by_broker = defaultdict(list)
    for deal, stamp in _deals(snapshot, observed_at, "fill DEAL").values():
        if stamp.date() == observed_at.date():
            by_broker[deal["stock_code"][-2:], deal["broker_order_id"]].append(deal)

    prices, seen_children = {}, set()
    for parent in candidates:
        client_id = _identifier(parent.get("id"), "ORDER parent client ID")
        symbol = _symbol(parent.get("symbol"), "ORDER parent")
        side = _side(parent.get("side"), "ORDER parent")
        quantity = _quantity(parent.get("quantity"), "ORDER parent", minimum=1)
        filled = parent["filled_quantity"]
        parent_price = _price(parent.get("average_price"), "ORDER parent average price", positive=True)
        if client_id in prices or filled > quantity or (parent["status"] == "FILLED") != (filled == quantity):
            raise ValueError("inconsistent terminal ORDER parent for " + client_id)

        child_quantity, all_deals = 0, []
        for row in _rows(parent.get("broker_orders"), "ORDER broker children"):
            child = _record(row, "ORDER broker child")
            broker_id = _identifier(child.get("broker_order_id"), "ORDER broker child ID")
            key = symbol[-2:], broker_id
            submitted = _quantity(child.get("quantity"), "ORDER child", minimum=1)
            child_fill = _quantity(child.get("filled_quantity"), "ORDER child fill")
            child_price = _price(child.get("average_price"), "ORDER child average price", positive=bool(child_fill))
            status = child.get("status")
            if (
                key in seen_children
                or type(status) is not int
                or status not in (53, 54, 56, 57)
                or child_fill > submitted
                or (status == 56) != (child_fill == submitted)
                or (status == 57 and child_fill)
                or (not child_fill and child_price)
                or child.get("stock_code", symbol) != symbol
                or child.get("side", side) != side
            ):
                raise ValueError("inconsistent terminal ORDER broker child for " + client_id)
            seen_children.add(key)
            deals = by_broker[key]
            if any(deal["stock_code"] != symbol or deal["side"] != side for deal in deals):
                raise ValueError("DEAL symbol or side mismatch for " + broker_id)
            _deal_totals(deals, child_fill, broker_id, child_price)
            child_quantity += submitted
            all_deals.extend(deals)
        if child_quantity != quantity:
            raise ValueError("ORDER child quantity does not match terminal parent " + client_id)
        _, prices[client_id] = _deal_totals(all_deals, filled, client_id, parent_price)
    return prices


def reconcile_day_orders(journal, observation, now, ignored_prior_orders=()):
    """Return expiry overlays and exact deal totals, or raise when proof is incomplete."""
    journal = _record(journal, "settlement journal")
    observation = _record(observation, "closing observation")
    now = _timestamp(now, "current")
    close_at = _timestamp(observation.get("ts"), "closing observation")
    day = now.date().isoformat()
    if journal.get("day") != day or observation.get("day") != day or close_at.date() != now.date():
        raise ValueError("settlement day mismatch")
    session = _identifier(journal.get("session"), "settlement session")
    if observation.get("session") != session:
        raise ValueError("settlement session mismatch")
    if observation.get("ok") is not True or observation.get("query_error") != "":
        raise ValueError("closing observation query incomplete")
    if not -5 <= (now - close_at).total_seconds() <= 30:
        raise ValueError("closing observation timestamp outside freshness window")
    if now.time() < CLOSE or close_at.time() < CLOSE:
        raise ValueError("settlement requires 17:00 or later in Asia/Shanghai")

    baseline = _record(journal.get("settlement_baseline"), "settlement baseline")
    baseline_at = _timestamp(baseline.get("ts"), "settlement baseline")
    if baseline_at.date() != close_at.date() or baseline_at >= close_at:
        raise ValueError("settlement baseline must precede the closing observation on the same day")
    baseline = _record(baseline.get("snapshot"), "baseline snapshot")
    closing = _record(observation.get("snapshot"), "closing snapshot")
    baseline_account = _record(baseline.get("account"), "baseline account")
    closing_account = _record(closing.get("account"), "closing account")
    if _identifier(baseline_account.get("account_id"), "baseline account ID") != closing_account.get("account_id"):
        raise ValueError("settlement account identity mismatch")

    children = _observed_orders(observation, _journal_orders(journal, day, session))
    working = _working_orders(closing, children, now.date(), tuple(ignored_prior_orders))
    delta = _delta_deals(baseline, closing, baseline_at, close_at, children)
    holdings = _positions(baseline)
    zero_fill_orders, exact_fills = [], {}
    for broker_id, (parent, child) in children.items():
        filled = child["filled_quantity"]
        amount, average = _deal_totals(
            delta[broker_id], filled, broker_id, parent["average_price"], child["average_price"]
        )
        symbol = parent["symbol"]
        if filled:
            exact_fills[parent["id"]] = dict(filled_quantity=filled, amount=amount, average_price=average)
            holdings[symbol] = holdings.get(symbol, 0) + (filled if parent["side"] == "BUY" else -filled)
        elif parent["status"] == "ACCEPTED":
            if broker_id not in working:
                raise ValueError("missing active ORDER evidence for " + broker_id)
            zero_fill_orders.append(
                dict(
                    client_order_id=parent["id"],
                    broker_order_id=broker_id,
                    stock_code=symbol,
                    quantity=parent["quantity"],
                    filled_quantity=0,
                    terminal_status="EXPIRED",
                )
            )
    if {symbol: shares for symbol, shares in holdings.items() if shares} != _positions(closing):
        raise ValueError("closing POSITION does not reconcile to baseline and DEAL volumes")
    return dict(zero_fill_orders=zero_fill_orders, exact_fills=exact_fills)
