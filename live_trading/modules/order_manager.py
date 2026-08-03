"""Order generation based on TopkDropout strategy logic."""

import logging

import pandas as pd

from qlib.contrib.strategy.topk_dropout import select_topk_dropout

logger = logging.getLogger("live_trading.order")


class OrderManager:
    """Generates buy/sell orders using TopkDropout strategy."""

    def __init__(self, config: dict):
        strategy = config["strategy"]
        self.topk = strategy.get("topk", 10)
        self.n_drop = strategy.get("n_drop", 2)
        self.initial_buy_count = strategy.get("initial_buy_count")
        self.hold_thresh = int(strategy.get("hold_thresh", 1))
        self.risk_degree = float(strategy.get("risk_degree", 0.95))
        self.trade_unit = config["exchange"].get("trade_unit", 100)

    def _can_sell(
        self,
        position: dict,
        *,
        signal_date: str | None,
        trade_dates: list[str] | None,
    ) -> bool:
        if self.hold_thresh <= 1:
            return True
        opened = position.get("opened_trade_date")
        if not opened or not signal_date or not trade_dates:
            return False
        opened_ts = pd.Timestamp(opened)
        signal_ts = pd.Timestamp(signal_date)
        eligible_dates = {
            pd.Timestamp(value)
            for value in trade_dates
            if opened_ts <= pd.Timestamp(value) <= signal_ts
        }
        return opened_ts in eligible_dates and len(eligible_dates) >= self.hold_thresh

    def generate_orders(
        self,
        scores: pd.Series,
        current_positions: dict,
        cash: float,
        close_prices: dict,
        total_value: float,
        *,
        signal_date: str | None = None,
        trade_dates: list[str] | None = None,
    ) -> list[dict]:
        """Generate buy/sell orders based on TopkDropout logic.

        Args:
            scores: Series {instrument: score}, the T-1 prediction signals
            current_positions: {instrument: {shares, cost_price, ...}}
            cash: retained for caller compatibility; broker cash is applied by QMT
            close_prices: retained for caller compatibility; QMT resolves official close
            total_value: current total account value
            signal_date: prediction date used to measure completed holding days
            trade_dates: trading calendar through signal_date

        Returns:
            SELL intents carry target_shares; BUY intents carry target_value.
        """
        selection = select_topk_dropout(
            scores,
            current_positions,
            topk=self.topk,
            n_drop=self.n_drop,
            initial_buy_count=self.initial_buy_count,
        )
        if scores.dropna().empty:
            logger.warning("No effective scores available; refusing to generate orders")
            return []
        sell_from_candidates = tuple(
            instrument
            for instrument in selection.sell
            if self._can_sell(
                current_positions[instrument],
                signal_date=signal_date,
                trade_dates=trade_dates,
            )
        )
        buy_count = max(
            len(sell_from_candidates) + self.topk - len(current_positions),
            0,
        )
        buy_list = selection.buy[:buy_count]

        orders = []

        for inst in sell_from_candidates:
            if inst in current_positions:
                orders.append({
                    "instrument": inst,
                    "direction": "SELL",
                    "target_shares": current_positions[inst]["shares"],
                })

        if buy_list and total_value > 0:
            per_stock_budget = total_value * self.risk_degree / self.topk
            for inst in buy_list:
                orders.append({
                    "instrument": inst,
                    "direction": "BUY",
                    "target_value": per_stock_budget,
                })

        return orders
