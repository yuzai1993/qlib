import pandas as pd
import pytest

from live_trading.modules.order_manager import OrderManager


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
    assert [order["target_shares"] for order in orders if order["direction"] == "BUY"] == [
        500,
        500,
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


def test_buy_orders_keep_price_filter_and_board_lot_rounding():
    scores = _scores()
    prices = _prices(scores)
    prices[scores.index[1]] = 0.0

    orders = _manager().generate_orders(
        scores, {}, 100_000.0, prices, 100_000.0,
    )

    buys = [order for order in orders if order["direction"] == "BUY"]
    assert scores.index[1] not in _instruments(orders, "BUY")
    assert len(buys) == 9
    assert [order["target_shares"] for order in buys] == [900] * 9


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
