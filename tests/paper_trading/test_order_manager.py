import pandas as pd

from paper_trading.modules.order_manager import OrderManager


def _manager():
    return OrderManager({
        "strategy": {"topk": 10, "n_drop": 2},
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


def test_buy_orders_keep_price_filter_and_board_lot_rounding():
    scores = _scores()
    prices = _prices(scores)
    prices[scores.index[1]] = 0.0

    orders = _manager().generate_orders(
        scores, {}, 100_000.0, prices, 100_000.0,
    )

    buys = [order for order in orders if order["direction"] == "BUY"]
    assert scores.index[1] not in _instruments(orders, "BUY")
    assert buys
    assert all(order["target_shares"] > 0 for order in buys)
    assert all(order["target_shares"] % 100 == 0 for order in buys)
