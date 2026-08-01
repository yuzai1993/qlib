import pandas as pd
import pytest

from live_trading.modules.order_manager import OrderManager


def _manager(**strategy_overrides):
    strategy = {"topk": 10, "n_drop": 2}
    strategy.update(strategy_overrides)
    return OrderManager({
        "strategy": strategy,
        "exchange": {"trade_unit": 100},
    })


def _scores(count=14):
    instruments = [f"SH600{i:03d}" for i in range(count)]
    return pd.Series(
        range(count, 0, -1), index=instruments, dtype=float,
    )


def _positions(instruments):
    return {
        instrument: {"shares": 100, "cost_price": 10.0}
        for instrument in instruments
    }


def _prices(scores):
    return {instrument: 10.0 for instrument in scores.index}


def _instruments(orders, direction):
    return [
        order["instrument"]
        for order in orders
        if order["direction"] == direction
    ]


def test_full_portfolio_rotates_two_positions():
    scores = _scores()
    held = list(scores.index[:8]) + list(scores.index[10:12])

    orders = _manager().generate_orders(
        scores, _positions(held), 10_000.0, _prices(scores), 20_000.0,
    )

    assert set(_instruments(orders, "SELL")) == set(scores.index[10:12])
    assert _instruments(orders, "BUY") == list(scores.index[8:10])
    assert [order["target_value"] for order in orders if order["direction"] == "BUY"] == [
        pytest.approx(1_900.0),
        pytest.approx(1_900.0),
    ]


def test_underfilled_portfolio_rotates_and_fills_gap():
    scores = _scores()
    held = list(scores.index[:7]) + list(scores.index[10:12])

    orders = _manager().generate_orders(
        scores, _positions(held), 10_000.0, _prices(scores), 19_000.0,
    )

    assert set(_instruments(orders, "SELL")) == set(scores.index[10:12])
    assert _instruments(orders, "BUY") == list(scores.index[7:10])


def test_underfilled_top_ranked_portfolio_only_fills_gap():
    scores = _scores()
    held = list(scores.index[:9])

    orders = _manager().generate_orders(
        scores, _positions(held), 10_000.0, _prices(scores), 19_000.0,
    )

    assert _instruments(orders, "SELL") == []
    assert _instruments(orders, "BUY") == [scores.index[9]]


def test_empty_portfolio_buys_topk():
    scores = _scores()

    orders = _manager().generate_orders(
        scores, {}, 100_000.0, _prices(scores), 100_000.0,
    )

    assert _instruments(orders, "SELL") == []
    assert _instruments(orders, "BUY") == list(scores.index[:10])


def test_buy_target_values_do_not_depend_on_previous_close():
    scores = _scores()
    prices = _prices(scores)
    prices[scores.index[1]] = 0.0

    orders = _manager().generate_orders(
        scores, {}, 100_000.0, prices, 100_000.0,
    )

    buys = [order for order in orders if order["direction"] == "BUY"]
    assert len(buys) == 10
    assert [order["target_value"] for order in buys] == [
        pytest.approx(9_500.0)
    ] * 10


@pytest.mark.parametrize(
    "scores",
    [
        pd.Series({"SH600000": float("nan")}),
        pd.Series(dtype=float),
    ],
    ids=["all_nan", "empty"],
)
def test_empty_effective_scores_with_positions_generate_no_orders(scores):
    orders = _manager().generate_orders(
        scores,
        _positions(["SH600000", "SH600001"]),
        10_000.0,
        {"SH600000": 10.0, "SH600001": 10.0},
        12_000.0,
    )

    assert orders == []


def test_eleven_positions_sell_two_and_buy_one_to_reach_topk():
    scores = _scores()
    held = list(scores.index[:9]) + list(scores.index[10:12])

    orders = _manager().generate_orders(
        scores, _positions(held), 10_000.0, _prices(scores), 21_000.0,
    )

    sells = _instruments(orders, "SELL")
    buys = _instruments(orders, "BUY")
    assert set(sells) == set(scores.index[10:12])
    assert buys == [scores.index[9]]
    assert len(held) - len(sells) + len(buys) == 10


def test_eleven_top_ranked_positions_sell_one_and_buy_none_to_reach_topk():
    scores = _scores()
    held = list(scores.index[:11])

    orders = _manager().generate_orders(
        scores, _positions(held), 10_000.0, _prices(scores), 21_000.0,
    )

    sells = _instruments(orders, "SELL")
    buys = _instruments(orders, "BUY")
    assert sells == [scores.index[10]]
    assert buys == []
    assert len(held) - len(sells) + len(buys) == 10


def test_twelve_positions_sell_two_and_buy_none_to_reach_topk():
    scores = _scores()
    held = list(scores.index[:8]) + list(scores.index[10:14])

    orders = _manager().generate_orders(
        scores, _positions(held), 10_000.0, _prices(scores), 22_000.0,
    )

    sells = _instruments(orders, "SELL")
    buys = _instruments(orders, "BUY")
    assert set(sells) == set(scores.index[12:14])
    assert buys == []
    assert len(held) - len(sells) + len(buys) == 10


def test_tied_boundary_is_independent_of_signal_and_position_order():
    scores = pd.Series(
        [1.0, 1.0, 1.0],
        index=["SZ000002", "SH600001", "SH600000"],
    )
    manager = OrderManager({
        "strategy": {"topk": 2, "n_drop": 1},
        "exchange": {"trade_unit": 100},
    })
    prices = {instrument: 10.0 for instrument in scores.index}

    first = manager.generate_orders(
        scores,
        _positions(["SZ000002", "SH600001"]),
        1_000.0,
        prices,
        3_000.0,
    )
    second = manager.generate_orders(
        scores.iloc[::-1],
        _positions(["SH600001", "SZ000002"]),
        1_000.0,
        prices,
        3_000.0,
    )

    assert _instruments(first, "SELL") == ["SZ000002"]
    assert _instruments(first, "BUY") == ["SH600000"]
    assert _instruments(second, "SELL") == ["SZ000002"]
    assert _instruments(second, "BUY") == ["SH600000"]


def test_buy_budget_uses_configured_risk_degree():
    scores = pd.Series({"SH600000": 1.0})
    manager = OrderManager({
        "strategy": {"topk": 1, "n_drop": 0, "risk_degree": 0.5},
        "exchange": {"trade_unit": 100},
        "fees": {},
    })

    orders = manager.generate_orders(
        scores, {}, 10_000.0, {"SH600000": 10.0}, 10_000.0,
    )

    assert orders == [{
        "instrument": "SH600000",
        "direction": "BUY",
        "target_value": 5_000.0,
    }]


def test_replacement_buy_keeps_one_slot_target_value():
    scores = pd.Series({"SH600000": 2.0, "SZ000001": 1.0})
    manager = OrderManager({
        "strategy": {"topk": 1, "n_drop": 1, "risk_degree": 1.0},
        "exchange": {"trade_unit": 1},
        "fees": {
            "commission_rate": 0.00020,
            "min_commission": 5.0,
            "stamp_duty_rate": 0.0005,
            "transfer_fee_rate": 0.00001,
            "dividend_tax_rate": 0.20,
        },
    })

    orders = manager.generate_orders(
        scores,
        _positions(["SZ000001"]),
        0.0,
        {"SH600000": 10.0, "SZ000001": 10.0},
        1_000.0,
    )

    buy = next(order for order in orders if order["direction"] == "BUY")
    assert buy["target_value"] == pytest.approx(1_000.0)


def test_staged_initialization_buys_two_slots_without_sells():
    scores = _scores(40)
    manager = _manager(
        topk=30,
        n_drop=2,
        initial_buy_count=2,
        risk_degree=0.95,
        hold_thresh=20,
    )

    orders = manager.generate_orders(
        scores, {}, 500_000.0, {}, 500_000.0,
    )

    assert _instruments(orders, "SELL") == []
    assert _instruments(orders, "BUY") == list(scores.index[:2])
    assert [order["target_value"] for order in orders] == [
        pytest.approx(15_833.333333333334),
        pytest.approx(15_833.333333333334),
    ]
    assert all("target_shares" not in order for order in orders)


def test_staged_initialization_preserves_low_ranked_holding_until_full():
    scores = _scores(40)
    held = list(scores.index[:8]) + [scores.index[39]]
    manager = _manager(
        topk=30,
        n_drop=2,
        initial_buy_count=2,
        hold_thresh=20,
    )

    orders = manager.generate_orders(
        scores, _positions(held), 400_000.0, {}, 500_000.0,
    )

    assert _instruments(orders, "SELL") == []
    assert _instruments(orders, "BUY") == list(scores.index[8:10])


def _aged_positions(instruments, opened_trade_date):
    positions = _positions(instruments)
    for position in positions.values():
        position["opened_trade_date"] = opened_trade_date
    return positions


def test_hold20_blocks_sell_after_only_nineteen_signal_days():
    scores = _scores()
    held = list(scores.index[:8]) + list(scores.index[10:12])
    manager = _manager(hold_thresh=20)
    trade_dates = pd.bdate_range("2026-07-01", periods=19).strftime("%Y-%m-%d").tolist()

    orders = manager.generate_orders(
        scores,
        _aged_positions(held, trade_dates[0]),
        10_000.0,
        _prices(scores),
        20_000.0,
        signal_date=trade_dates[-1],
        trade_dates=trade_dates,
    )

    assert _instruments(orders, "SELL") == []
    assert _instruments(orders, "BUY") == []


def test_hold20_allows_sell_after_twentieth_signal_day():
    scores = _scores()
    held = list(scores.index[:8]) + list(scores.index[10:12])
    manager = _manager(hold_thresh=20)
    trade_dates = pd.bdate_range("2026-07-01", periods=20).strftime("%Y-%m-%d").tolist()

    orders = manager.generate_orders(
        scores,
        _aged_positions(held, trade_dates[0]),
        10_000.0,
        _prices(scores),
        20_000.0,
        signal_date=trade_dates[-1],
        trade_dates=trade_dates,
    )

    assert set(_instruments(orders, "SELL")) == set(scores.index[10:12])


def test_hold_filter_fails_closed_without_open_date():
    scores = _scores()
    held = list(scores.index[:8]) + list(scores.index[10:12])
    manager = _manager(hold_thresh=20)

    orders = manager.generate_orders(
        scores,
        _positions(held),
        10_000.0,
        _prices(scores),
        20_000.0,
        signal_date="2026-07-31",
        trade_dates=["2026-07-31"],
    )

    assert _instruments(orders, "SELL") == []
    assert _instruments(orders, "BUY") == []
