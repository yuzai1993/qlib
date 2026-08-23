"""真阶梯（BT v4）的 Mac 侧下单意图生成。

只决定**名字与预算**，不决定股数：BUY 带 target_value、quantity=0，由 bridge 在
提交时刻用已确定的收盘价定量并与到期卖单抵销（见 spec 4.4）。这样 B 与回测的
round_amount_by_trade_unit(V / C, factor) 逐股相等，不引入动量倾斜。
"""

from __future__ import annotations

import logging
import math

import pandas as pd

from live_trading.modules.cohort_store import CohortState, state_to_ledger
from live_trading.modules.fees import fees_from_config, order_total_fee
from qlib.contrib.strategy.cohort_ladder import (
    cohort_budget,
    ledger_sell_amounts,
    select_ladder_buys,
)

logger = logging.getLogger("live_trading.cohort_orders")


class CohortOrderManager:
    """按 CohortLadderStrategy 语义生成买卖意图。"""

    def __init__(self, config: dict):
        strategy = config["strategy"]
        self.topk = int(strategy["topk"])
        self.horizon = int(strategy["horizon"])
        self.risk_degree = float(strategy["risk_degree"])
        self.trade_unit = int(config["exchange"].get("trade_unit", 100))
        self.fees = fees_from_config(config)

    def _sell_quantity(self, wanted: float, position: float) -> int:
        """不足一手只能整笔卖出；否则向下取整到一手。"""
        wanted = int(round(wanted))
        position = int(round(position))
        if wanted <= 0:
            return 0
        if wanted >= position:
            return position
        if wanted % self.trade_unit == 0:
            return wanted
        return (wanted // self.trade_unit) * self.trade_unit

    def _estimated_proceeds(self, sells: dict[str, int], close_prices: dict) -> float:
        """预估当日卖出所得（扣费）。回测预算天然含当日卖出所得，实盘必须补上。"""
        total = 0.0
        for code, quantity in sells.items():
            price = close_prices.get(code)
            if price is None or not math.isfinite(float(price)) or float(price) <= 0:
                continue
            gross = float(price) * int(quantity)
            total += gross - order_total_fee("SELL", gross, self.fees)
        return total

    def generate_orders(
        self,
        scores: pd.Series,
        cohort_state: CohortState,
        broker_positions: dict,
        cash: float,
        close_prices: dict,
        total_value: float,
    ) -> list[dict]:
        ledger = state_to_ledger(cohort_state, horizon=self.horizon)
        position_amounts = {
            code: float(amount) for code, amount in broker_positions.items()
        }

        due = ledger_sell_amounts(ledger.due(), position_amounts)
        sells: dict[str, int] = {}
        for code, wanted in due.items():
            quantity = self._sell_quantity(wanted, position_amounts.get(code, 0.0))
            if quantity > 0:
                sells[code] = quantity
            else:
                logger.warning(
                    "due %s dropped: wanted=%.0f position=%.0f below one lot",
                    code, wanted, position_amounts.get(code, 0.0),
                )

        orders = [
            {
                "side": "SELL",
                "stock_code": code,
                "quantity": quantity,
                "target_value": 0.0,
                "reason": "cohort_due",
            }
            for code, quantity in sells.items()
        ]

        # 发布期不做可买过滤：T 日 16:00 无从判断 T+1 的封板/停牌，且已决定不顺延。
        # 只剔掉没有收盘价的票——那样连预算都算不了。
        priced = (
            scores[[code in close_prices for code in scores.index]]
            if len(scores)
            else scores
        )
        buys = select_ladder_buys(priced, k=self.topk, is_buyable=None)

        budget = cohort_budget(
            total_value=float(total_value),
            cash=float(cash) + self._estimated_proceeds(sells, close_prices),
            risk_degree=self.risk_degree,
            horizon=self.horizon,
        )
        per_name = budget / self.topk if self.topk else 0.0
        for code in buys:
            if per_name <= 0:
                continue
            orders.append({
                "side": "BUY",
                "stock_code": code,
                "quantity": 0,
                "target_value": per_name,
                "reason": "cohort_layer",
            })

        logger.info(
            "cohort orders: %d sell / %d buy, budget=%.2f (per name %.2f)",
            len(sells), len(buys), budget, per_name,
        )
        return orders
