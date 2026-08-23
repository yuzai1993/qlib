"""A 股按市场类型的涨跌停阈值（封板判定用 9.5%/19.5%，避开除权噪声）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

LIMIT_CAP_MAIN = 0.095
LIMIT_CAP_WIDE = 0.195
CHINEXT_WIDE_FROM = pd.Timestamp("2020-08-24")
MARKET_CN = "market_cn"


def limit_cap_array(instruments, dates) -> np.ndarray:
    """按代码前缀与日期返回封板阈值。

    主板 9.5%；科创板 SH68* 19.5%；创业板 SZ30* 自 2020-08-24 起 19.5%，此前 9.5%。
    """
    inst = pd.Index(instruments).astype(str).str.upper()
    dts = pd.DatetimeIndex(pd.to_datetime(dates))
    cap = np.full(len(inst), LIMIT_CAP_MAIN, dtype=float)
    cap[np.asarray(inst.str.startswith("SH68"))] = LIMIT_CAP_WIDE
    chinext = np.asarray(inst.str.startswith("SZ30")) & np.asarray(dts >= CHINEXT_WIDE_FROM)
    cap[chinext] = LIMIT_CAP_WIDE
    return cap


def apply_market_cn_limits(quote_df: pd.DataFrame) -> None:
    """按板块阈值就地写入 limit_buy / limit_sell（停牌日一律不可成交）。"""
    if quote_df.empty:
        quote_df["limit_buy"] = pd.Series(dtype=bool)
        quote_df["limit_sell"] = pd.Series(dtype=bool)
        return
    names = list(quote_df.index.names or [])
    if "instrument" in names:
        inst = quote_df.index.get_level_values("instrument")
    else:
        inst = quote_df.index.get_level_values(0)
    if "datetime" in names:
        dts = quote_df.index.get_level_values("datetime")
    else:
        dts = quote_df.index.get_level_values(1)
    cap = limit_cap_array(inst, dts)
    suspended = quote_df["$close"].isna()
    chg = quote_df["$change"].to_numpy()
    quote_df["limit_buy"] = np.asarray(chg >= cap) | suspended.to_numpy()
    quote_df["limit_sell"] = np.asarray(chg <= -cap) | suspended.to_numpy()
