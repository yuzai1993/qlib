"""把 TopkDropout 买卖意图转换为可执行的 SignalOrder 列表。

输入沿用 Live OrderManager 的输出格式：SELL 使用 ``target_shares``，
BUY 使用 ``target_value``。

规则：
- 买卖均使用盘后定价协议；价格由 QMT 使用官方收盘价，不携带昨收限价
- BUY 的股数由 QMT 按成交前实际可用资金和目标金额计算
- SELL 非整手向下取整到 trade_unit，取整后为 0 则丢弃
- 同 code 同向合并；卖单 priority=10 先于买单 priority=20
- 超过 max_orders_per_day 抛错（不静默截断）
"""

import logging
import math

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
            prev_close: retained for caller compatibility; unused by v2 orders
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
                if side == "SELL":
                    quantity = (
                        int(intent["target_shares"] // self.trade_unit)
                        * self.trade_unit
                    )
                    if quantity <= 0:
                        logger.warning(
                            "drop SELL %s: shares %s rounds to 0",
                            inst,
                            intent["target_shares"],
                        )
                        continue
                    target_value = 0.0
                else:
                    quantity = 0
                    target_value = float(intent["target_value"])
                    if not math.isfinite(target_value) or target_value <= 0:
                        raise PlanError(
                            f"BUY target_value must be positive and finite: "
                            f"{target_value!r}"
                        )

                orders.append(SignalOrder(
                    batch_id=batch_id,
                    client_order_id=make_client_order_id(
                        trade_date, batch_seq, seq, side,
                    ),
                    stock_code=qlib_to_qmt(inst),
                    side=side,
                    quantity=quantity,
                    target_value=target_value,
                    price_type="AFTER_HOURS_CLOSE",
                    limit_price=0.0,
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
        """同一 instrument 同向合并目标，保持首次出现顺序。"""
        merged = {}
        for intent in intents:
            direction = intent.get("direction")
            if direction not in {"BUY", "SELL"}:
                raise PlanError(f"invalid intent direction: {direction!r}")
            key = (intent["instrument"], intent["direction"])
            value_key = "target_value" if direction == "BUY" else "target_shares"
            if value_key not in intent:
                raise PlanError(f"{direction} intent missing {value_key}")
            if key in merged:
                merged[key][value_key] += intent[value_key]
            else:
                merged[key] = dict(intent)
        return list(merged.values())
