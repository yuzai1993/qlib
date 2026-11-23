"""Independent broker evidence for post-close settlement."""

import datetime as dt
from copy import deepcopy
from pathlib import Path

import pytest


DAY = "2026-11-20"
STRATEGY = "test-strategy"
SESSION = "test-session"
CLOSE = dt.datetime.fromisoformat(DAY + "T17:00:00+08:00")


def _reconcile(*args, **kwargs):
    from clipboard_settlement import reconcile_day_orders

    return reconcile_day_orders(*args, **kwargs)


def _position(stock_code, shares, available=None):
    return dict(stock_code=stock_code, shares=shares, can_use_volume=shares if available is None else available)


def _deal(broker_order_id, trade_id, stock_code, side, quantity, price, amount):
    return dict(
        broker_order_id=broker_order_id,
        trade_id=trade_id,
        stock_code=stock_code,
        side=side,
        quantity=quantity,
        price=price,
        amount=amount,
        trade_date="20261120",
        trade_time="150000",
    )


@pytest.fixture
def evidence(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "live_trading/qmt_strategy"))
    submitted = [
        dict(id="sold", symbol="600001.SH", side="SELL", quantity=400),
        dict(id="buy-a", symbol="000002.SZ", side="BUY", quantity=100),
        dict(id="buy-b", symbol="600003.SH", side="BUY", quantity=200),
        dict(id="day-order", symbol="600004.SH", side="BUY", quantity=300),
    ]
    parents = []
    for request_order, broker_id, filled, average in zip(
        submitted, ["s", "a", "b", "z"], [400, 100, 200, 0], [12.307, 10.1, 7.5, 0]
    ):
        child = dict(
            broker_order_id=broker_id,
            quantity=request_order["quantity"],
            filled_quantity=filled,
            status=56 if filled else 50,
            average_price=average,
        )
        parents.append(
            dict(
                request_order,
                filled_quantity=filled,
                status="FILLED" if filled else "ACCEPTED",
                average_price=average,
                broker_orders=[child],
                remark="remark-" + broker_id,
            )
        )
    baseline_deal = dict(_deal("manual", "t-5", "600009.SH", "BUY", 100, 5, 500), trade_time="140001")
    baseline = dict(
        account=dict(account_id="test-account"),
        positions=[_position("600001.SH", 600), _position("600009.SH", 100, 0)],
        working_orders=[],
        deals=[baseline_deal],
        deals_error="",
    )
    working = dict(
        broker_order_id="z",
        stock_code="600004.SH",
        side="BUY",
        quantity=300,
        filled_quantity=0,
        status=50,
        insert_date="20261120",
        insert_time="14:58:12",
        remark="remark-z",
    )
    closing = dict(
        account=dict(account_id="test-account"),
        positions=[
            _position("600001.SH", 200),
            _position("600009.SH", 100, 0),
            _position("000002.SZ", 100, 0),
            _position("600003.SH", 200, 0),
        ],
        working_orders=[working],
        deals_error="",
        deals=[
            deepcopy(baseline_deal),
            _deal("s", "s1", "600001.SH", "SELL", 100, 12.30, 1230),
            _deal("s", "s2", "600001.SH", "SELL", 300, 12.31, 3693),
            _deal("a", "s1", "000002.SZ", "BUY", 100, 10.1, 1010),
            _deal("b", "b1", "600003.SH", "BUY", 200, 7.5, 1500),
        ],
    )
    batch_orders = [dict(client_order_id=o["id"], stock_code=o["symbol"], side=o["side"]) for o in submitted]
    batch_orders.append(dict(client_order_id="skipped", stock_code="600005.SH", side="BUY"))
    journal = dict(
        day=DAY,
        session=SESSION,
        strategy=STRATEGY,
        batch=dict(header=dict(batch_id="test-batch", trade_date=DAY, strategy_id=STRATEGY), orders=batch_orders),
        settlement_baseline=dict(snapshot=baseline, ts=DAY + "T06:54:00Z"),
        phases=dict(
            SELL=dict(request=dict(session=SESSION, orders=submitted[:1]), acknowledged=True, skipped=[]),
            BUY=dict(
                request=None,
                acknowledged=True,
                requests=[
                    dict(request=dict(session=SESSION, orders=[o]), attempted=True, acknowledged=True, skipped=[])
                    for o in submitted[1:]
                ],
                skipped=[dict(id="skipped", status="SKIPPED", filled_quantity=0, broker_orders=[])],
            ),
        ),
    )
    observation = dict(
        day=DAY, session=SESSION, ok=True, query_error="", ts=CLOSE.isoformat(), snapshot=closing, orders=parents
    )
    return journal, observation


def test_reconciles_three_fills_and_one_day_order_without_mutating_raw_evidence(evidence):
    before = deepcopy(evidence)
    assert _reconcile(*evidence, CLOSE) == {
        "zero_fill_orders": [
            {
                "client_order_id": "day-order",
                "broker_order_id": "z",
                "stock_code": "600004.SH",
                "quantity": 300,
                "filled_quantity": 0,
                "terminal_status": "EXPIRED",
            }
        ],
        "exact_fills": {
            "sold": {"filled_quantity": 400, "amount": 4923, "average_price": 12.3075},
            "buy-a": {"filled_quantity": 100, "amount": 1010, "average_price": 10.1},
            "buy-b": {"filled_quantity": 200, "amount": 1500, "average_price": 7.5},
        },
    }
    assert evidence == before


def test_before_close_remains_unresolved(evidence):
    with pytest.raises(ValueError, match="17:00"):
        _reconcile(*evidence, CLOSE - dt.timedelta(seconds=1))


def test_preclose_observation_is_insufficient_at_close(evidence):
    journal, observation = evidence
    observation["ts"] = DAY + "T16:59:59+08:00"
    with pytest.raises(ValueError, match="17:00"):
        _reconcile(journal, observation, CLOSE)


@pytest.mark.parametrize(
    "ts", [None, "yesterday", "2026-11-20T17:00:00", "2026-11-20T17:00:06+08:00", "2026-11-20T17:00:00+08:00"]
)
def test_invalid_or_stale_observation_is_unresolved(evidence, ts):
    journal, observation = evidence
    observation["ts"] = ts
    now = CLOSE + dt.timedelta(seconds=31) if ts == CLOSE.isoformat() else CLOSE
    with pytest.raises(ValueError, match="observation timestamp"):
        _reconcile(journal, observation, now)


@pytest.mark.parametrize("seconds", [-5, 0, 30])
def test_freshness_boundary(evidence, seconds):
    journal, observation = evidence
    observation["ts"] = (CLOSE + dt.timedelta(seconds=5)).isoformat()
    result = _reconcile(journal, observation, CLOSE + dt.timedelta(seconds=5 + seconds))
    assert len(result["zero_fill_orders"]) == 1


@pytest.mark.parametrize(
    ("target", "field", "value", "reason"),
    [
        ("observation", "day", "2026-11-19", "day"),
        ("observation", "session", "other-session", "session"),
        ("observation", "ok", False, "observation"),
        ("observation", "query_error", "disconnected", "observation"),
        ("account", "account_id", "other-account", "account"),
        ("journal", "settlement_baseline", None, "baseline"),
        ("baseline", "ts", "2026-11-19T06:54:00Z", "baseline"),
        ("baseline", "ts", CLOSE.isoformat(), "baseline"),
    ],
)
def test_mismatched_settlement_context_is_unresolved(evidence, target, field, value, reason):
    journal, observation = evidence
    objects = dict(
        journal=journal,
        observation=observation,
        account=observation["snapshot"]["account"],
        baseline=journal["settlement_baseline"],
    )
    objects[target][field] = value
    with pytest.raises(ValueError, match=reason):
        _reconcile(journal, observation, CLOSE)


@pytest.mark.parametrize("snapshot_name", ["closing", "baseline"])
@pytest.mark.parametrize("invalid", [None, "missing", "failure"])
def test_complete_deal_query_is_required(evidence, snapshot_name, invalid):
    journal, observation = evidence
    snapshot = observation["snapshot"] if snapshot_name == "closing" else journal["settlement_baseline"]["snapshot"]
    if invalid == "missing":
        del snapshot["deals"]
    elif invalid == "failure":
        snapshot["deals_error"] = "DEAL API unavailable"
    else:
        snapshot["deals"] = invalid
    with pytest.raises(ValueError, match="DEAL"):
        _reconcile(journal, observation, CLOSE)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", 0),
        ("quantity", 100.5),
        ("quantity", True),
        ("price", float("nan")),
        ("amount", float("inf")),
        ("amount", 0),
        ("trade_date", "20261131"),
        ("trade_time", "14:88:00"),
        ("stock_code", "unqualified"),
        ("side", ""),
        ("broker_order_id", ""),
        ("trade_id", " "),
    ],
)
def test_malformed_deal_can_never_be_interpreted_as_no_fill(evidence, field, value):
    journal, observation = evidence
    observation["snapshot"]["deals"][1][field] = value
    with pytest.raises(ValueError, match="DEAL"):
        _reconcile(journal, observation, CLOSE)


def test_identical_trade_duplicate_counts_once(evidence):
    journal, observation = evidence
    deals = observation["snapshot"]["deals"]
    deals.append(deepcopy(deals[1]))
    assert _reconcile(journal, observation, CLOSE)["exact_fills"]["sold"]["filled_quantity"] == 400


def test_integral_deal_volume_is_counted_in_whole_shares(evidence):
    journal, observation = evidence
    observation["snapshot"]["deals"][1]["quantity"] = 100.0
    filled = _reconcile(journal, observation, CLOSE)["exact_fills"]["sold"]["filled_quantity"]
    assert type(filled) is int and filled == 400


def test_conflicting_trade_duplicate_blocks_settlement(evidence):
    journal, observation = evidence
    deals = observation["snapshot"]["deals"]
    deals.append(dict(deals[1], amount=900))
    with pytest.raises(ValueError, match="conflicting.*DEAL"):
        _reconcile(journal, observation, CLOSE)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("side", "BUY"),
        ("quantity", 99),
        ("stock_code", "600008.SH"),
        ("amount", 1000),
        ("broker_order_id", "foreign-deal"),
        ("trade_date", "20261119"),
        ("trade_time", "14:50:00"),
    ],
)
def test_unmatched_or_inconsistent_delta_deal_blocks_settlement(evidence, field, value):
    journal, observation = evidence
    observation["snapshot"]["deals"][1][field] = value
    with pytest.raises(ValueError, match="DEAL"):
        _reconcile(journal, observation, CLOSE)


def test_hidden_fill_blocks_zero_fill_expiry(evidence):
    journal, observation = evidence
    observation["snapshot"]["deals"].append(_deal("z", "z1", "600004.SH", "BUY", 100, 8, 800))
    observation["snapshot"]["positions"].append(_position("600004.SH", 100, 0))
    with pytest.raises(ValueError, match="DEAL.*quantity"):
        _reconcile(journal, observation, CLOSE)


@pytest.mark.parametrize("change", ["drop", "quantity", "batch-fill"])
def test_baseline_deals_must_stay_intact_and_predate_batch(evidence, change):
    journal, observation = evidence
    if change == "drop":
        observation["snapshot"]["deals"].pop(0)
    elif change == "quantity":
        observation["snapshot"]["deals"][0]["quantity"] = 101
    else:
        journal["settlement_baseline"]["snapshot"]["deals"].append(deepcopy(observation["snapshot"]["deals"][1]))
    with pytest.raises(ValueError, match="baseline.*DEAL"):
        _reconcile(journal, observation, CLOSE)


@pytest.mark.parametrize(
    "positions",
    [
        None,
        [],
        [_position("600010.SH", 100)],
        [_position("600001.SH", 200), _position("600001.SH", 200)],
        [_position("600008.SH", -100)],
        [_position("600008.SH", 100, 101)],
    ],
)
def test_whole_account_position_reconciliation(evidence, positions):
    journal, observation = evidence
    if positions and positions[0]["stock_code"] == "600010.SH":
        positions = observation["snapshot"]["positions"] + positions
    observation["snapshot"]["positions"] = positions
    with pytest.raises(ValueError, match="POSITION"):
        _reconcile(journal, observation, CLOSE)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "PARTIAL"),
        ("status", "UNKNOWN"),
        ("broker_orders", []),
        ("id", "not-durable"),
        ("quantity", 400),
        ("side", "SELL"),
    ],
)
def test_parent_identity_and_unresolved_state_block_settlement(evidence, field, value):
    journal, observation = evidence
    observation["orders"][-1][field] = value
    with pytest.raises(ValueError, match="ORDER"):
        _reconcile(journal, observation, CLOSE)


def test_missing_submitted_parent_is_unresolved(evidence):
    journal, observation = evidence
    observation["orders"].pop()
    observation["snapshot"]["working_orders"].clear()
    with pytest.raises(ValueError, match="ORDER"):
        _reconcile(journal, observation, CLOSE)


@pytest.mark.parametrize(
    "field",
    [
        "attempted",
        "acknowledged",
        "durable request",
        "duplicate broker child",
        "child quantity",
        "child status",
        "working missing",
        "pending batch order",
    ],
)
def test_missing_or_inconsistent_order_proof_is_unresolved(evidence, field):
    journal, observation = evidence
    durable = journal["phases"]["BUY"]["requests"][-1]
    children = observation["orders"][-1]["broker_orders"]
    if field == "attempted":
        durable["attempted"] = durable["acknowledged"] = False
    elif field == "acknowledged":
        durable["acknowledged"] = False
        observation["orders"].pop()
    elif field == "durable request":
        durable["request"]["orders"][0]["symbol"] = "600005.SH"
    elif field == "duplicate broker child":
        children.append(dict(children[0], broker_order_id="second"))
    elif field == "child quantity":
        children[0]["quantity"] = 400
    elif field == "child status":
        children[0]["status"] = 49
    elif field == "working missing":
        observation["snapshot"]["working_orders"] = []
    else:
        journal["phases"]["BUY"]["skipped"].clear()
    with pytest.raises(ValueError, match="ORDER"):
        _reconcile(journal, observation, CLOSE)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("insert_date", "20261119"),
        ("insert_date", "20261121"),
        ("insert_date", "20261131"),
        ("insert_time", "09:14:59"),
        ("insert_time", "150001"),
        ("insert_time", "21:00:00"),
        ("status", 49),
        ("stock_code", "600005.SH"),
        ("filled_quantity", 1),
        ("remark", "wrong"),
    ],
)
def test_mismatching_or_nonregular_working_order_blocks_expiry(evidence, field, value):
    journal, observation = evidence
    observation["snapshot"]["working_orders"][0][field] = value
    with pytest.raises(ValueError, match="ORDER"):
        _reconcile(journal, observation, CLOSE)


@pytest.mark.parametrize("value", ["091500", "15:00:00"])
def test_regular_trading_time_boundary(evidence, value):
    journal, observation = evidence
    observation["snapshot"]["working_orders"][0]["insert_time"] = value
    assert _reconcile(journal, observation, CLOSE)["zero_fill_orders"][0]["broker_order_id"] == "z"


def test_duplicate_raw_working_order_is_unresolved(evidence):
    journal, observation = evidence
    active = observation["snapshot"]["working_orders"]
    active.append(deepcopy(active[0]))
    with pytest.raises(ValueError, match="duplicate.*ORDER"):
        _reconcile(journal, observation, CLOSE)


def test_only_exact_caller_validated_prior_working_orders_are_ignored(evidence):
    journal, observation = evidence
    active = observation["snapshot"]["working_orders"]
    prior = dict(active[0], broker_order_id="prior-z", insert_date="20261119")
    active.append(prior)
    with pytest.raises(ValueError, match="ORDER"):
        _reconcile(journal, observation, CLOSE, ignored_prior_orders=[dict(prior, insert_time="145812")])
    assert _reconcile(journal, observation, CLOSE, ignored_prior_orders=[deepcopy(prior)])["zero_fill_orders"]


def test_real_terminal_cancellation_preserves_independently_verified_partial_fill(evidence):
    journal, observation = evidence
    parent = observation["orders"][0]
    parent.update(status="CANCELED", filled_quantity=100, average_price=12.30)
    parent["broker_orders"][0].update(status=53, filled_quantity=100, average_price=12.30)
    observation["snapshot"]["deals"].pop(2)
    observation["snapshot"]["positions"][0].update(shares=500, can_use_volume=500)
    assert _reconcile(journal, observation, CLOSE)["exact_fills"]["sold"] == {
        "filled_quantity": 100,
        "amount": 1230,
        "average_price": 12.30,
    }


def _confirmed(observation):
    from clipboard_settlement import confirmed_fill_prices

    return confirmed_fill_prices(observation)


@pytest.fixture
def intraday(evidence):
    _, observation = evidence
    observation["ts"] = DAY + "T15:01:00+08:00"
    del observation["snapshot"]["positions"]
    return observation


@pytest.mark.parametrize("status", ["ACCEPTED", "PARTIAL", "UNKNOWN"])
def test_confirmed_fills_are_available_before_close_with_unresolved_orders(intraday, status):
    intraday["orders"][-1]["status"] = status
    deals = intraday["snapshot"]["deals"]
    deals.append(deepcopy(deals[1]))
    deals.append(_deal("manual", "m2", "600005.SH", "BUY", 100, 2, 200))
    before = deepcopy(intraday)
    assert _confirmed(intraday) == {"sold": 12.3075, "buy-a": 10.1, "buy-b": 7.5}
    assert intraday == before


def test_confirmed_fills_aggregate_terminal_children_with_partial_cancellation(intraday):
    parent = intraday["orders"][0]
    parent.update(status="CANCELED", filled_quantity=100, average_price=12.30)
    parent["broker_orders"] = [
        dict(broker_order_id="s", quantity=100, filled_quantity=100, average_price=12.30, status=56),
        dict(broker_order_id="cancel", quantity=300, filled_quantity=0, average_price=0, status=54),
    ]
    intraday["snapshot"]["deals"].pop(2)
    assert _confirmed(intraday)["sold"] == 12.30


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", 200),
        ("side", "BUY"),
        ("stock_code", "600007.SH"),
        ("price", 12.40),
        ("amount", 1235.0),
        ("trade_date", "20261119"),
    ],
)
def test_confirmed_fills_reject_inconsistent_independent_deal(intraday, field, value):
    intraday["snapshot"]["deals"][1][field] = value
    with pytest.raises(ValueError, match="DEAL"):
        _confirmed(intraday)


def test_confirmed_fills_reject_missing_deal_query(intraday):
    intraday["snapshot"]["deals"] = None
    with pytest.raises(ValueError, match="DEAL"):
        _confirmed(intraday)


def test_confirmed_fills_require_terminal_child_quantity_and_average(intraday):
    child = intraday["orders"][0]["broker_orders"][0]
    child["average_price"] = 12.30
    with pytest.raises(ValueError, match="DEAL.*average"):
        _confirmed(intraday)
