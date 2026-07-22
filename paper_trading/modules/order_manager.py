"""Order generation based on TopkDropout strategy logic."""

import logging

import pandas as pd

logger = logging.getLogger("paper_trading.order")


class OrderManager:
    """Generates buy/sell orders using TopkDropout strategy."""

    def __init__(self, config: dict):
        strategy = config["strategy"]
        self.topk = strategy.get("topk", 10)
        self.n_drop = strategy.get("n_drop", 2)
        self.trade_unit = config["exchange"].get("trade_unit", 100)

    def generate_orders(
        self,
        scores: pd.Series,
        current_positions: dict,
        cash: float,
        close_prices: dict,
        total_value: float,
    ) -> list[dict]:
        """Generate buy/sell orders based on TopkDropout logic.

        Args:
            scores: Series {instrument: score}, the T-1 prediction signals
            current_positions: {instrument: {shares, cost_price, ...}}
            cash: available cash
            close_prices: {instrument: price} for today
            total_value: current total account value

        Returns:
            List of order dicts [{instrument, direction, target_shares}, ...]
        """
        scores = scores.dropna().sort_values(ascending=False)
        current_stock_list = list(current_positions)
        last = scores.reindex(current_stock_list).sort_values(ascending=False).index
        gap = max(self.topk - len(last), 0)

        today = scores[~scores.index.isin(last)].head(self.n_drop + gap).index
        combined = scores.reindex(last.union(today)).sort_values(ascending=False).index
        bottom = set(combined[-self.n_drop:]) if self.n_drop > 0 else set()
        sell_from_candidates = [instrument for instrument in last if instrument in bottom]
        buy_list = list(today[:len(sell_from_candidates) + gap])

        orders = []

        for inst in sell_from_candidates:
            if inst in current_positions:
                orders.append({
                    "instrument": inst,
                    "direction": "SELL",
                    "target_shares": current_positions[inst]["shares"],
                })

        if buy_list:
            estimated_sell_proceeds = 0
            for inst in sell_from_candidates:
                if inst in current_positions and inst in close_prices:
                    estimated_sell_proceeds += (
                        current_positions[inst]["shares"] * close_prices[inst]
                    )

            available_cash = cash + estimated_sell_proceeds
            n_positions = max(len(buy_list), 1)
            per_stock_budget = available_cash / n_positions * 0.95

            for inst in buy_list:
                price = close_prices.get(inst)
                if price is None or price <= 0:
                    continue
                target_shares = int(per_stock_budget / price // self.trade_unit) * self.trade_unit
                if target_shares > 0:
                    orders.append({
                        "instrument": inst,
                        "direction": "BUY",
                        "target_shares": target_shares,
                    })

        return orders
