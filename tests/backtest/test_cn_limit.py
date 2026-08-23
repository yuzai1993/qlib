"""A 股板块涨跌停阈值：主板 9.5%、创业板/科创板 19.5%。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qlib.backtest.cn_limit import apply_market_cn_limits, limit_cap_array


def test_limit_cap_main_chinext_star_and_cutoff():
    inst = np.array(["SH600000", "SZ000001", "SZ300001", "SZ300001", "SH688001"])
    dts = pd.to_datetime(
        ["2020-08-21", "2020-08-21", "2020-08-21", "2020-08-24", "2020-01-02"]
    )
    cap = limit_cap_array(inst, dts)
    assert cap.tolist() == pytest.approx([0.095, 0.095, 0.095, 0.195, 0.195])


def test_apply_market_cn_limits_blocks_legal_chinext_move_under_old_scalar():
    """创业板涨 12% 在 0.095 下会被误拒；按板块阈值应可买。"""
    idx = pd.MultiIndex.from_tuples(
        [
            ("SZ300001", pd.Timestamp("2024-01-02")),
            ("SH600000", pd.Timestamp("2024-01-02")),
        ],
        names=["instrument", "datetime"],
    )
    quote = pd.DataFrame(
        {"$close": [10.0, 10.0], "$change": [0.12, 0.12]},
        index=idx,
    )
    apply_market_cn_limits(quote)
    assert bool(quote.loc[("SZ300001", pd.Timestamp("2024-01-02")), "limit_buy"]) is False
    assert bool(quote.loc[("SH600000", pd.Timestamp("2024-01-02")), "limit_buy"]) is True


def test_exchange_market_cn_limit_type_updates_quote():
    from qlib.backtest.exchange import Exchange

    idx = pd.MultiIndex.from_tuples(
        [("SZ300001", pd.Timestamp("2024-01-02"))],
        names=["instrument", "datetime"],
    )
    ex = Exchange.__new__(Exchange)
    ex.quote_df = pd.DataFrame({"$close": [10.0], "$change": [0.12]}, index=idx)
    assert Exchange._get_limit_type(ex, "market_cn") == Exchange.LT_MARKET_CN
    ex._update_limit("market_cn")
    assert bool(ex.quote_df["limit_buy"].iloc[0]) is False
