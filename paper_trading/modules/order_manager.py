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
        top_instruments = list(scores.head(self.topk).index)
        held_instruments = set(current_positions.keys())

        sell_candidates = held_instruments - set(top_instruments)

        held_in_top = [s for s in top_instruments if s in held_instruments]
        not_held_in_top = [s for s in top_instruments if s not in held_instruments]

        if len(sell_candidates) == 0 and len(not_held_in_top) == 0:
            return []

        if len(held_instruments) > 0:
            held_scores = scores.reindex(list(held_instruments)).dropna()
            held_scores_sorted = held_scores.sort_values(ascending=True)
            n_sell = min(self.n_drop, len(sell_candidates), len(not_held_in_top))
            if n_sell > 0 and len(sell_candidates) > 0:
                sell_from_candidates = list(
                    held_scores_sorted[held_scores_sorted.index.isin(sell_candidates)]
                    .head(n_sell).index
                )
            else:
                sell_from_candidates = []
        else:
            sell_from_candidates = []
            n_sell = 0

        orders = []

        for inst in sell_from_candidates:
            if inst in current_positions:
                orders.append({
                    "instrument": inst,
                    "direction": "SELL",
                    "target_shares": current_positions[inst]["shares"],
                })

        n_buy = len(sell_from_candidates)
        if len(current_positions) == 0:
            buy_list = not_held_in_top[:self.topk]
        else:
            buy_list = not_held_in_top[:n_buy]

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
