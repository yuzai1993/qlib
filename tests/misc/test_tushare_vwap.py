"""TushareNormalize1d 的 vwap 计算测试。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "data_collector" / "tushare"))


def _make_normalizer():
    from collector import TushareNormalize1d

    obj = TushareNormalize1d.__new__(TushareNormalize1d)  # 跳过 __init__ 的日历拉取
    obj._date_field_name = "date"
    obj._symbol_field_name = "symbol"
    obj._calendar_list = []
    return obj


def test_vwap_is_amount_over_volume_times_factor():
    norm = _make_normalizer()
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "symbol": ["sh600000", "sh600000"],
            "open": [10.0, 10.5],
            "high": [11.0, 11.0],
            "low": [9.5, 10.0],
            "close": [10.5, 10.8],
            "volume": [1000.0, 2000.0],  # 手
            "amount": [1060.0, 2140.0],  # 千元
            "adj_factor": [2.0, 2.0],  # 无除权 → factor 恒 1
            "pct_chg": [1.0, 2.857],
        }
    )
    out = norm.normalize(df)
    # raw_vwap = amount*1000 / (volume*100)；factor=1 → vwap = raw_vwap
    expected = np.array([1060.0 * 1000 / (1000 * 100), 2140.0 * 1000 / (2000 * 100)])
    assert "vwap" in out.columns
    np.testing.assert_allclose(out["vwap"].values, expected, rtol=1e-9)


def test_vwap_respects_front_adjustment():
    norm = _make_normalizer()
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "symbol": ["sh600000", "sh600000"],
            "open": [10.0, 5.25],
            "high": [11.0, 5.5],
            "low": [9.5, 5.0],
            "close": [10.5, 5.4],
            "volume": [1000.0, 4000.0],
            "amount": [1060.0, 2140.0],
            "adj_factor": [1.0, 2.0],  # 除权日 → 前日 factor = 0.5
            "pct_chg": [1.0, 2.857],
        }
    )
    out = norm.normalize(df)
    raw_vwap_day1 = 1060.0 * 1000 / (1000 * 100)
    assert abs(out["vwap"].iloc[0] - raw_vwap_day1 * 0.5) < 1e-9


def test_missing_amount_yields_nan_vwap():
    """存量 source CSV 没有 amount 列时，vwap 应为 NaN 而不是报错。"""
    norm = _make_normalizer()
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05"]),
            "symbol": ["sh600000"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
            "volume": [1000.0],
            "adj_factor": [1.0],
            "pct_chg": [1.0],
        }
    )
    out = norm.normalize(df)
    assert "vwap" in out.columns
    assert np.isnan(out["vwap"].iloc[0])


def test_suspended_day_vwap_is_nan():
    """停牌日（volume<=0）vwap 应与价格字段一样置 NaN。"""
    norm = _make_normalizer()
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "symbol": ["sh600000", "sh600000"],
            "open": [10.0, 10.5],
            "high": [11.0, 11.0],
            "low": [9.5, 10.0],
            "close": [10.5, 10.8],
            "volume": [1000.0, 0.0],
            "amount": [1060.0, 0.0],
            "adj_factor": [1.0, 1.0],
            "pct_chg": [1.0, 0.0],
        }
    )
    out = norm.normalize(df)
    assert np.isnan(out["vwap"].iloc[1])
