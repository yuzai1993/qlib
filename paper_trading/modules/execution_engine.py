"""Simulated execution engine: match orders against market data."""

import logging
import uuid
from typing import Optional

import numpy as np
import pandas as pd
from qlib.data import D

logger = logging.getLogger("paper_trading.execution")


class ExecutionEngine:
    """Simulates order execution with market constraints."""

    def __init__(self, config: dict):
        ex = config["exchange"]
        self.deal_price = ex.get("deal_price", "close")
        self.limit_threshold = ex.get("limit_threshold", 0.095)
        self.open_cost = ex.get("open_cost", 0.0005)
        self.close_cost = ex.get("close_cost", 0.0015)
        self.min_cost = ex.get("min_cost", 5)
        self.trade_unit = ex.get("trade_unit", 100)

    def get_market_data(self, date_str: str, instruments: list[str]) -> pd.DataFrame:
        """Fetch close price and change for the given date."""
        fields = ["$close", "$change"]
        try:
            df = D.features(instruments, fields, start_time=date_str, end_time=date_str)
            # D.features returns MultiIndex (instrument, datetime) - keep instrument level
            if isinstance(df.index, pd.MultiIndex):
                df = df.droplevel(1)  # drop datetime, keep instrument
            df.columns = ["close", "change"]
            return df
        except Exception as e:
            logger.error("Failed to fetch market data for %s: %s", date_str, e)
            return pd.DataFrame(columns=["close", "change"])

    def get_close_price(self, date_str: str, instrument: str) -> Optional[float]:
        """Get close price for a single instrument on a date."""
        try:
            df = D.features([instrument], ["$close"], start_time=date_str, end_time=date_str)
            if df.empty:
                return None
            val = df.iloc[0, 0]
            return float(val) if not np.isnan(val) else None
        except Exception:
            return None

    def get_all_close_prices(self, date_str: str, instruments: list[str]) -> dict:
        """Get close prices for multiple instruments. Returns {instrument: price}."""
        try:
            df = D.features(instruments, ["$close"], start_time=date_str, end_time=date_str)
            if df.empty:
                return {}
            result = {}
            # D.features returns MultiIndex (instrument, datetime)
            if isinstance(df.index, pd.MultiIndex):
                for idx, row in df.iterrows():
                    inst = idx[0]  # first level is instrument
                    val = row.iloc[0]
                    try:
                        fval = float(val)
                        if not np.isnan(fval):
                            result[inst] = fval
                    except (TypeError, ValueError):
                        continue
            else:
                for inst, row in df.iterrows():
                    val = row.iloc[0]
                    try:
                        fval = float(val)
                        if not np.isnan(fval):
                            result[inst] = fval
                    except (TypeError, ValueError):
                        continue
            return result
        except Exception as e:
            logger.error("Failed to get close prices: %s", e)
            return {}

    def is_limit_up(self, change: float) -> bool:
        return change is not None and change >= self.limit_threshold

    def is_limit_down(self, change: float) -> bool:
        return change is not None and change <= -self.limit_threshold

    def is_suspended(self, close_price, change) -> bool:
        return close_price is None or np.isnan(close_price)

    def calculate_commission(self, amount: float, direction: str) -> float:
        rate = self.open_cost if direction == "BUY" else self.close_cost
        commission = amount * rate
        return max(commission, self.min_cost)

    def round_shares(self, shares: float) -> int:
        """Round down to nearest trade_unit (100 shares)."""
        return int(shares // self.trade_unit) * self.trade_unit

    def execute_orders(self, date_str: str, order_list: list[dict]) -> list[dict]:
        """Execute a list of orders against market data.

        Each order: {instrument, direction, target_shares, ...}
        Returns list of executed order dicts with fill info.
        """
        if not order_list:
            return []

        instruments = list(set(o["instrument"] for o in order_list))
        market_data = self._fetch_market_data_dict(date_str, instruments)
        results = []

        for order in order_list:
            inst = order["instrument"]
            direction = order["direction"]
            target_shares = order.get("target_shares", 0)

            mkt = market_data.get(inst, {})
            close_price = mkt.get("close")
            change = mkt.get("change")

            result = {
                "order_id": str(uuid.uuid4())[:12],
                "date": date_str,
                "instrument": inst,
                "direction": direction,
                "target_shares": target_shares,
                "filled_shares": 0,
                "price": None,
                "amount": 0,
                "commission": 0,
                "status": "REJECTED",
                "reject_reason": None,
            }

            if close_price is None or np.isnan(close_price):
                result["reject_reason"] = "SUSPENDED"
                results.append(result)
                continue

            if direction == "BUY" and self.is_limit_up(change):
                result["reject_reason"] = "LIMIT_UP"
                results.append(result)
                continue

            if direction == "SELL" and self.is_limit_down(change):
                result["reject_reason"] = "LIMIT_DOWN"
                results.append(result)
                continue

            filled = self.round_shares(target_shares) if direction == "BUY" else int(target_shares)
            if filled <= 0:
                result["reject_reason"] = "ZERO_SHARES"
                results.append(result)
                continue

            amount = filled * close_price
            commission = self.calculate_commission(amount, direction)

            result.update({
                "filled_shares": filled,
                "price": close_price,
                "amount": amount,
                "commission": commission,
                "status": "FILLED" if filled == self.round_shares(target_shares) or direction == "SELL" else "PARTIAL",
            })
            results.append(result)

        return results

    def _fetch_market_data_dict(self, date_str: str, instruments: list[str]) -> dict:
        """Returns {instrument: {close, change}}."""
        try:
            df = D.features(instruments, ["$close", "$change"],
                            start_time=date_str, end_time=date_str)
            if df.empty:
                return {}

            def _safe_float(val):
                try:
                    fval = float(val)
                    return fval if not np.isnan(fval) else None
                except (TypeError, ValueError):
                    return None

            result = {}
            # D.features returns MultiIndex (instrument, datetime)
            if isinstance(df.index, pd.MultiIndex):
                for idx, row in df.iterrows():
                    inst = idx[0]  # first level is instrument
                    result[inst] = {
                        "close": _safe_float(row.iloc[0]),
                        "change": _safe_float(row.iloc[1]),
                    }
            else:
                for inst, row in df.iterrows():
                    result[inst] = {
                        "close": _safe_float(row.iloc[0]),
                        "change": _safe_float(row.iloc[1]),
                    }
            return result
        except Exception as e:
            logger.error("Failed to fetch market data dict: %s", e)
            return {}
