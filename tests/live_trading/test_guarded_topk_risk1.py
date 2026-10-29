"""Risk1 boundary checks with local files and fake QMT APIs only."""

import importlib.util
from pathlib import Path

import pytest


# Reuse the existing isolated bridge fixture without importing an unrelated
# installed ``tests`` package or registering the helpers' tests for collection.
_spec = importlib.util.spec_from_file_location(
    "_guarded_risk1_test_helpers", Path(__file__).with_name("test_qmt_bridge_logic.py")
)
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
bridge = _helpers.bridge


def risk1_orders(count):
    orders = _helpers._guarded_orders(count)
    for order in orders:
        order["cash_risk_degree"] = 1.0
    return orders


@pytest.mark.parametrize(
    "cash,price,requested,expected",
    [
        (1000.0, 10.0, 100, 0),
        (1005.0, 10.0, 100, 0),
        (1005.01, 10.0, 100, 100),
        (100000.0, 10.02, 10000, 9900),
    ],
)
def test_affordability_includes_minimum_commission_transfer_fee_and_limit(
    bridge, cash, price, requested, expected
):
    assert bridge._max_affordable_quantity(cash, price, requested) == expected


def test_risk1_uses_full_cash_budget_but_affords_all_five_at_limit(bridge, monkeypatch):
    state = _helpers._guarded_broker(bridge, monkeypatch, cash=300000.0)
    batch = _helpers._guarded_batch(bridge, risk1_orders(5))
    bridge._process_batch(_helpers._TickCtx(10.0), batch)

    assert [order["cash_target_value"] for order in batch.orders] == [60000.0] * 5
    assert [order["cash_limit_price"] for order in batch.orders] == [10.02] * 5
    assert [call[6] for call in state["calls"]] == [6000, 6000, 6000, 6000, 5900]
    # Hand-calculated at 10.02: notional + max(0.0000854*notional, 5)
    # + 0.00001*notional. The final candidate loses a lot to preserve cash.
    assert sum(order["cash_reserved"] for order in batch.orders) == pytest.approx(299626.5816492)
    assert batch.remaining_cash == pytest.approx(373.4183508)


def test_risk1_quote_failure_keeps_candidate_divisor_and_unused_cash(bridge, monkeypatch):
    state = _helpers._guarded_broker(bridge, monkeypatch, cash=100000.0)
    batch = _helpers._guarded_batch(bridge, risk1_orders(2))
    quote = bridge._official_close
    monkeypatch.setattr(
        bridge, "_official_close",
        lambda context, code: 0.0 if code == "600000.SH" else quote(context, code),
    )
    bridge._process_batch(_helpers._TickCtx(10.0), batch)

    assert len(batch.cash_plan["candidates"]) == 2
    assert batch.orders[1]["cash_target_value"] == 50000.0
    assert [(call[3], call[6]) for call in state["calls"]] == [("600001.SH", 5000)]
    assert batch.remaining_cash == pytest.approx(49894.499)


def test_risk1_restart_preserves_fee_reservation_and_never_reallocates(bridge, monkeypatch):
    state = _helpers._guarded_broker(bridge, monkeypatch, held_count=3, cash=100000.0)
    batch = _helpers._guarded_batch(bridge, risk1_orders(2))

    def interrupted(*args):
        state["calls"].append(args)
        raise KeyboardInterrupt("after first fake broker call")

    monkeypatch.setattr(bridge, "passorder", interrupted)
    with pytest.raises(KeyboardInterrupt):
        bridge._process_batch(_helpers._TickCtx(10.0), batch)
    assert [call[6] for call in state["calls"]] == [5000]
    assert batch.remaining_cash == pytest.approx(49894.499)

    state["cash"] = 1000000.0
    bridge.g.batch = None
    bridge._recover_processing_batch()
    restored = bridge.g.batch
    monkeypatch.setattr(bridge, "passorder", lambda *args: state["calls"].append(args))
    bridge._process_batch(_helpers._TickCtx(20.0), restored)

    assert [call[6] for call in state["calls"]] == [5000, 4900]
    assert [order["cash_target_value"] for order in restored.orders] == [50000.0, 50000.0]
    assert [order["cash_limit_price"] for order in restored.orders] == [10.02, 10.02]
    assert restored.remaining_cash == pytest.approx(791.00802)


def test_risk1_at_daily_limit_still_reserves_fees(bridge, monkeypatch):
    state = _helpers._guarded_broker(bridge, monkeypatch, cash=10000.0)
    batch = _helpers._guarded_batch(bridge, risk1_orders(1))
    bridge._process_batch(_helpers._TickCtx(10.0, up_stop=10.0), batch)

    assert batch.orders[0]["cash_target_value"] == 10000.0
    assert batch.orders[0]["cash_limit_price"] == 10.0
    assert [call[6] for call in state["calls"]] == [900]
    assert batch.remaining_cash == pytest.approx(994.91)
