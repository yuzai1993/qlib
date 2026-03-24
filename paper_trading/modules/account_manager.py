"""Account state management: cash, positions, PnL calculations."""

import logging
from typing import Optional

import numpy as np
import pandas as pd
from qlib.data import D

from .recorder import Recorder

logger = logging.getLogger("paper_trading.account")


class AccountManager:
    """Manages account state: cash balance, positions, returns."""

    def __init__(self, config: dict, recorder: Recorder):
        self.config = config
        self.recorder = recorder
        self.initial_cash = config["paper_trading"]["initial_cash"]
        self.benchmark = config["data"]["benchmark"]

        self.cash = self.initial_cash
        self.positions: dict = {}  # {instrument: {shares, cost_price, holding_days}}
        self._loaded_date = None

    def load_state(self, date_str: str = None):
        """Load account state from DB. If date_str given, load that date's state."""
        if date_str:
            pos_df = self.recorder.get_positions(date_str)
            summary = self.recorder.get_account_summary(start=date_str, end=date_str)
        else:
            summary = self.recorder.get_account_summary()
            pos_df = self.recorder.get_positions()

        if not summary.empty:
            latest = summary.iloc[-1]
            self.cash = latest["cash"]
            self._loaded_date = latest["date"]
        else:
            self.cash = self.initial_cash
            self._loaded_date = None

        self.positions = {}
        if not pos_df.empty:
            for _, row in pos_df.iterrows():
                self.positions[row["instrument"]] = {
                    "shares": row["shares"],
                    "cost_price": row["cost_price"],
                    "holding_days": row.get("holding_days", 1),
                }

    def apply_orders(self, executed_orders: list[dict]):
        """Update cash and positions based on executed orders."""
        for order in executed_orders:
            if order["status"] not in ("FILLED", "PARTIAL"):
                continue

            inst = order["instrument"]
            filled = order["filled_shares"]
            price = order["price"]
            amount = order["amount"]
            commission = order["commission"]

            if order["direction"] == "SELL":
                self.cash += amount - commission
                if inst in self.positions:
                    self.positions[inst]["shares"] -= filled
                    if self.positions[inst]["shares"] <= 0:
                        del self.positions[inst]
            elif order["direction"] == "BUY":
                total_cost = amount + commission
                self.cash -= total_cost
                if inst in self.positions:
                    old = self.positions[inst]
                    old_total = old["shares"] * old["cost_price"]
                    new_total = old_total + total_cost
                    new_shares = old["shares"] + filled
                    self.positions[inst]["shares"] = new_shares
                    self.positions[inst]["cost_price"] = new_total / new_shares
                else:
                    self.positions[inst] = {
                        "shares": filled,
                        "cost_price": total_cost / filled,
                        "holding_days": 0,
                    }

    def update_market_values(self, close_prices: dict):
        """Update position market values with latest close prices."""
        for inst in list(self.positions.keys()):
            if inst in close_prices:
                self.positions[inst]["current_price"] = close_prices[inst]
            self.positions[inst]["holding_days"] = self.positions[inst].get("holding_days", 0) + 1

    def calculate_summary(self, date_str: str) -> dict:
        """Calculate and return full account summary for the date."""
        market_value = sum(
            pos.get("current_price", pos["cost_price"]) * pos["shares"]
            for pos in self.positions.values()
        )
        total_value = self.cash + market_value

        prev = self.recorder.get_latest_account_summary()
        if prev and prev.get("date") != "init":
            prev_value = float(prev["total_value"] or self.initial_cash)
            daily_return = (total_value - prev_value) / prev_value if prev_value else 0
            cumulative_return = (total_value - self.initial_cash) / self.initial_cash
        else:
            daily_return = (total_value - self.initial_cash) / self.initial_cash
            cumulative_return = daily_return

        benchmark_return = self._get_benchmark_return(date_str)
        prev_bench_cum = prev.get("benchmark_cumulative_return", 0) if prev else 0
        if prev_bench_cum is None:
            prev_bench_cum = 0
        prev_bench_cum = float(prev_bench_cum)
        benchmark_cumulative = (1 + prev_bench_cum) * (1 + benchmark_return) - 1

        excess_return = cumulative_return - benchmark_cumulative

        turnover = self._calculate_turnover(date_str, total_value)

        return {
            "date": date_str,
            "cash": self.cash,
            "total_value": total_value,
            "market_value": market_value,
            "daily_return": daily_return,
            "cumulative_return": cumulative_return,
            "benchmark_return": benchmark_return,
            "benchmark_cumulative_return": benchmark_cumulative,
            "excess_return": excess_return,
            "position_count": len(self.positions),
            "turnover": turnover,
        }

    def get_position_details(self, date_str: str, total_value: float) -> list[dict]:
        """Build position detail records for DB storage."""
        details = []
        for inst, pos in self.positions.items():
            current = pos.get("current_price", pos["cost_price"])
            mv = current * pos["shares"]
            cost_total = pos["cost_price"] * pos["shares"]
            profit = mv - cost_total
            profit_rate = profit / cost_total if cost_total > 0 else 0
            weight = mv / total_value if total_value > 0 else 0

            details.append({
                "instrument": inst,
                "shares": pos["shares"],
                "cost_price": pos["cost_price"],
                "current_price": current,
                "market_value": mv,
                "profit": profit,
                "profit_rate": profit_rate,
                "weight": weight,
                "holding_days": pos.get("holding_days", 1),
            })
        return details

    def _get_benchmark_return(self, date_str: str) -> float:
        try:
            df = D.features(
                [self.benchmark], ["$close"],
                start_time=date_str, end_time=date_str,
            )
            if df.empty:
                return 0.0
            prev_start = pd.Timestamp(date_str) - pd.Timedelta(days=10)
            prev_df = D.features(
                [self.benchmark], ["$close"],
                start_time=prev_start.strftime("%Y-%m-%d"), end_time=date_str,
            )
            if len(prev_df) < 2:
                return 0.0
            today_close = prev_df.iloc[-1, 0]
            prev_close = prev_df.iloc[-2, 0]
            if np.isnan(today_close) or np.isnan(prev_close) or prev_close == 0:
                return 0.0
            return float((today_close - prev_close) / prev_close)
        except Exception as e:
            logger.warning("Failed to get benchmark return: %s", e)
            return 0.0

    def _calculate_turnover(self, date_str: str, total_value: float) -> float:
        orders_df = self.recorder.get_orders(start=date_str, end=date_str)
        if orders_df.empty or total_value <= 0:
            return 0.0
        filled = orders_df[orders_df["status"].isin(["FILLED", "PARTIAL"])]
        trade_amount = filled["amount"].sum()
        return trade_amount / total_value / 2
