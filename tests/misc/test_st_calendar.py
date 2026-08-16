import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/data_collector/tushare"))

from st_calendar import (
    compress_intervals,
    expand_namechange,
    is_st_name,
    merge_daily,
    st_symbols_on,
    ts_code_to_qlib,
)

CAL = [
    "2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18",
    "2026-06-19", "2026-06-22", "2026-06-23",
]


def test_ts_code_to_qlib_covers_bj():
    assert ts_code_to_qlib("300029.SZ") == "SZ300029"
    assert ts_code_to_qlib("600000.SH") == "SH600000"
    assert ts_code_to_qlib("920305.BJ") == "BJ920305"


def test_is_st_name_covers_both_delisting_shapes():
    assert is_st_name("*ST天龙")
    assert is_st_name("ST天龙")
    assert is_st_name("天龙退")      # 深/北市后缀
    assert is_st_name("退市创兴")    # 沪市前缀
    assert not is_st_name("天龙光电")
    assert not is_st_name("平安银行")


def test_expand_namechange_uses_delist_date_when_end_is_null():
    raw = pd.DataFrame(
        {
            "ts_code": ["300029.SZ"],
            "name": ["天龙退"],
            "start_date": ["20260618"],
            "end_date": [None],
        }
    )
    out = expand_namechange(raw, CAL, {"300029.SZ": "20260619"})
    assert sorted(out["date"]) == ["2026-06-18", "2026-06-19"]
    assert set(out["symbol"]) == {"SZ300029"}
    assert set(out["source"]) == {"namechange"}


def test_expand_namechange_falls_back_to_calendar_end_without_delist_date():
    raw = pd.DataFrame(
        {
            "ts_code": ["300029.SZ"],
            "name": ["天龙退"],
            "start_date": ["20260622"],
            "end_date": [None],
        }
    )
    out = expand_namechange(raw, CAL, {})
    assert sorted(out["date"]) == ["2026-06-22", "2026-06-23"]


def test_expand_namechange_drops_non_st_segments_and_clips_to_calendar():
    raw = pd.DataFrame(
        {
            "ts_code": ["300029.SZ", "000001.SZ"],
            "name": ["*ST天龙", "平安银行"],
            "start_date": ["20090101", "20090101"],
            "end_date": ["20260616", "20260616"],
        }
    )
    out = expand_namechange(raw, CAL, {})
    assert set(out["symbol"]) == {"SZ300029"}
    assert out["date"].min() == "2026-06-15"
    assert out["date"].max() == "2026-06-16"


def test_merge_daily_prefers_stock_st_on_conflict():
    st = pd.DataFrame(
        {"symbol": ["SZ300029"], "date": ["2026-06-17"],
         "name": ["*ST天龙"], "source": ["stock_st"]}
    )
    nc = pd.DataFrame(
        {"symbol": ["SZ300029", "SZ300029"], "date": ["2026-06-17", "2026-06-18"],
         "name": ["*ST天龙", "天龙退"], "source": ["namechange", "namechange"]}
    )
    out = merge_daily(st, nc)
    assert len(out) == 2
    row = out[out["date"] == "2026-06-17"].iloc[0]
    assert row["source"] == "stock_st"


def test_st_symbols_on_is_exact_day_lookup():
    daily = pd.DataFrame(
        {
            "symbol": ["SZ300029", "SZ300029", "SH600000"],
            "date": ["2026-06-17", "2026-06-18", "2026-06-17"],
            "name": ["*ST天龙", "天龙退", "退市浦发"],
            "source": ["stock_st", "namechange", "namechange"],
        }
    )
    assert st_symbols_on(daily, "2026-06-17") == {"SZ300029", "SH600000"}
    assert st_symbols_on(daily, "2026-06-18") == {"SZ300029"}
    assert st_symbols_on(daily, "2026-06-19") == set()


def test_compress_intervals_splits_on_missing_trade_day():
    daily = pd.DataFrame(
        {
            "symbol": ["SZ300029", "SZ300029"],
            "date": ["2026-06-15", "2026-06-17"],
            "name": ["*ST天龙", "*ST天龙"],
            "source": ["stock_st", "stock_st"],
        }
    )
    out = compress_intervals(daily, CAL)
    assert [(r.start, r.end) for r in out.itertuples()] == [
        ("2026-06-15", "2026-06-15"),
        ("2026-06-17", "2026-06-17"),
    ]
