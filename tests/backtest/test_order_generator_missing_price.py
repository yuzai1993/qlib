import math

from qlib.contrib.strategy.order_generator import _calculate_current_stock_value


class MockPosition:
    def get_stock_amount_dict(self):
        return {"LIVE": 10, "SUSPENDED": 5}

    def get_stock_price(self, stock_id):
        assert stock_id == "SUSPENDED"
        return 8.0


class MockExchange:
    def get_deal_price(self, stock_id, start_time, end_time, direction):
        assert direction.name == "SELL"
        return {"LIVE": 12.0, "SUSPENDED": None}[stock_id]


def test_current_stock_value_falls_back_to_recorded_price_when_deal_price_is_missing():
    value = _calculate_current_stock_value(
        MockPosition(), MockExchange(), start_time=None, end_time=None
    )

    assert value == 160.0


def test_current_stock_value_falls_back_when_deal_price_is_nan():
    class NanExchange(MockExchange):
        def get_deal_price(self, stock_id, start_time, end_time, direction):
            assert direction.name == "SELL"
            return {"LIVE": 12.0, "SUSPENDED": math.nan}[stock_id]

    value = _calculate_current_stock_value(
        MockPosition(), NanExchange(), start_time=None, end_time=None
    )

    assert value == 160.0


def test_current_stock_value_falls_back_when_deal_price_is_infinite():
    class InfiniteExchange(MockExchange):
        def get_deal_price(self, stock_id, start_time, end_time, direction):
            assert direction.name == "SELL"
            return {"LIVE": 12.0, "SUSPENDED": math.inf}[stock_id]

    value = _calculate_current_stock_value(
        MockPosition(), InfiniteExchange(), start_time=None, end_time=None
    )

    assert value == 160.0
