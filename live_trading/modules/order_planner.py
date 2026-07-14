"""把 TopkDropout 买卖意图转换为可执行的 SignalOrder 列表。

输入沿用 paper_trading OrderManager 的输出格式：
``[{"instrument": "SH600000", "direction": "BUY", "target_shares": 500}, ...]``

规则（设计文档 §4.2/§4.3 定稿）：
- 限价：SELL = prev_close*(1-sell_slippage)，BUY = prev_close*(1+buy_slippage)
- 非整手向下取整到 trade_unit，取整后为 0 则丢弃
- 同 code 同向合并；卖单 priority=10 先于买单 priority=20
- 超过 max_orders_per_day 抛错（不静默截断）
"""

import logging

from live_trading.modules.code_map import qlib_to_qmt
from live_trading.modules.signal_schema import (
    SignalOrder,
    make_client_order_id,
)

logger = logging.getLogger("live_trading.order_planner")

SELL_PRIORITY = 10
BUY_PRIORITY = 20


class PlanError(ValueError):
    """订单规划失败（如超出单日订单上限）。"""


class OrderPlanner:
    def __init__(self, config: dict):
        self.buy_slippage = float(config.get("buy_slippage", 0.01))
        self.sell_slippage = float(config.get("sell_slippage", 0.01))
        self.max_orders_per_day = int(config.get("max_orders_per_day", 20))
        self.trade_unit = int(config.get("trade_unit", 100))

    def plan(
        self,
        intents: list,
        prev_close: dict,
        batch_id: str,
        trade_date: str,
        batch_seq: int = 1,
        reason: str = "topk_dropout",
    ) -> list:
        """生成 SignalOrder 列表（卖单在前）。

        Args:
            intents: [{"instrument", "direction", "target_shares"}, ...]
            prev_close: {instrument(qlib): 昨收价（未复权）}
            batch_id: 批次 ID
            trade_date: 计划执行日 YYYY-MM-DD
        """
        merged = self._merge_intents(intents)

        sells = [i for i in merged if i["direction"] == "SELL"]
        buys = [i for i in merged if i["direction"] == "BUY"]

        orders = []
        seq = 1
        for intent_list, side, priority in (
            (sells, "SELL", SELL_PRIORITY),
            (buys, "BUY", BUY_PRIORITY),
        ):
            for intent in intent_list:
                inst = intent["instrument"]
                price = prev_close.get(inst)
                if price is None or price <= 0:
                    logger.warning("drop %s %s: no valid prev_close", side, inst)
                    continue

                quantity = int(intent["target_shares"] // self.trade_unit) * self.trade_unit
                if quantity <= 0:
                    logger.warning(
                        "drop %s %s: shares %s rounds to 0",
                        side, inst, intent["target_shares"],
                    )
                    continue

                if side == "SELL":
                    limit_price = round(price * (1 - self.sell_slippage), 2)
                else:
                    limit_price = round(price * (1 + self.buy_slippage), 2)

                orders.append(SignalOrder(
                    batch_id=batch_id,
                    client_order_id=make_client_order_id(
                        trade_date, batch_seq, seq, side,
                    ),
                    stock_code=qlib_to_qmt(inst),
                    side=side,
                    quantity=quantity,
                    price_type="FIX",
                    limit_price=limit_price,
                    priority=priority,
                    instrument_qlib=inst,
                    reason=reason,
                ))
                seq += 1

        if len(orders) > self.max_orders_per_day:
            raise PlanError(
                f"{len(orders)} orders exceed max_orders_per_day="
                f"{self.max_orders_per_day}; refuse to publish"
            )
        return orders

    @staticmethod
    def _merge_intents(intents: list) -> list:
        """同一 instrument 同向合并 target_shares，保持首次出现顺序。"""
        merged = {}
        for intent in intents:
            key = (intent["instrument"], intent["direction"])
            if key in merged:
                merged[key]["target_shares"] += intent["target_shares"]
            else:
                merged[key] = dict(intent)
        return list(merged.values())
