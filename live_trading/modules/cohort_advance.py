"""回执导入后推进分层账本一天。

没有这一步，``due()`` 永远返回同一层、``add`` 永远不记新层，阶梯彻底失效。

汇总用 ``fills.applied_qty`` 而非 ``filled_qty``：``applied_qty`` 是已真正计入持仓的
增量（``apply_fill`` 维护的幂等账），与券商持仓同源。取批次时**包含**被 supersede 的
批次——它们在被顶掉前可能已有部分成交落进持仓，那些股数必须同样进账本。
"""

from __future__ import annotations

import logging

from live_trading.modules.cohort_store import advanced_state
from live_trading.modules.signal_schema import TERMINAL_FILL_STATUS

logger = logging.getLogger("live_trading.cohort_advance")


def day_executions(
    fills: list, *, strategy_mode: str = "LIVE",
) -> tuple[dict[str, float], dict[str, float]]:
    """把当日回执汇总成 ``(sold, filled)``，按股票代码合并同侧多笔。"""
    sold: dict[str, float] = {}
    filled: dict[str, float] = {}
    for fill in fills:
        if fill.get("mode") != strategy_mode:
            continue
        if fill.get("status") not in TERMINAL_FILL_STATUS:
            continue
        quantity = float(fill.get("applied_qty") or 0)
        if quantity <= 0:
            continue
        bucket = filled if fill.get("side") == "BUY" else sold
        code = fill["stock_code"]
        bucket[code] = bucket.get(code, 0.0) + quantity
    return sold, filled


def advance_after_import(
    recorder, *, trade_date: str, horizon: int, strategy_id: str,
):
    """按当日实际成交推进账本一天并落库。

    已推进过同一天则返回 ``None``——回执导入一天可能跑多次，重复推进会让阶梯
    涨到 ``horizon + 1`` 层、后续所有到期日集体错位。当天没有批次也要记一个空层，
    否则阶梯账龄会提前一天。
    """
    state = recorder.load_cohort_state()
    if any(date == trade_date for date, _ in state.layers):
        logger.info("cohort layer for %s already recorded; skipping", trade_date)
        return None

    fills: list = []
    for batch in recorder.get_batches_by_date(trade_date, strategy_id=strategy_id):
        fills.extend(recorder.get_fills(batch["batch_id"]))
    sold, filled = day_executions(fills)

    advanced = advanced_state(
        state, horizon=horizon, trade_date=trade_date, sold=sold, filled=filled,
    )
    recorder.save_cohort_state(advanced)
    logger.info(
        "cohort ladder advanced to %s: sold=%s filled=%s pending=%s",
        trade_date, sold, filled, advanced.pending,
    )
    return advanced
