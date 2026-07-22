"""Refresh the Live Trading stock-name cache from Tushare."""

from __future__ import annotations

import logging

from live_trading.modules.code_map import qmt_to_qlib

logger = logging.getLogger("live_trading.stock_names")


def fetch_stock_names(pro) -> list[dict]:
    """Fetch listed, delisted and paused A-share names from a Tushare client."""
    mapped = {}
    for status in ("L", "D", "P"):
        frame = pro.stock_basic(
            exchange="", list_status=status, fields="ts_code,name",
        )
        if frame is None or frame.empty:
            continue
        for row in frame.to_dict("records"):
            stock_code = row.get("ts_code")
            name = row.get("name")
            if not stock_code or not name:
                continue
            try:
                instrument = qmt_to_qlib(stock_code)
            except ValueError:
                continue
            mapped[stock_code] = {
                "stock_code": stock_code,
                "instrument": instrument,
                "name": str(name),
            }
    rows = [mapped[key] for key in sorted(mapped)]
    logger.info("fetched %d stock names from Tushare", len(rows))
    return rows
