import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/data_collector/tushare"))

from st_calendar import (
    compress_intervals,
    expand_namechange,
    fetch_namechange,
    fetch_stock_st,
    is_st_name,
    merge_daily,
    st_symbols_on,
    ts_code_to_qlib,
    update,
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


class _FakePro:
    def __init__(self, st_by_date=None, nc=None, delist=None):
        self.st_by_date = st_by_date or {}
        self.nc = nc
        self.delist = delist
        self.st_calls = []
        self.nc_calls = []

    def stock_st(self, **kw):
        assert "start_date" not in kw and "end_date" not in kw, "range query is forbidden"
        self.st_calls.append(kw)
        return self.st_by_date.get(kw["trade_date"], pd.DataFrame()).copy()

    def namechange(self, **kw):
        self.nc_calls.append(kw)
        return self.nc.copy() if self.nc is not None else pd.DataFrame()

    def stock_basic(self, **kw):
        return self.delist.copy() if self.delist is not None else pd.DataFrame()


def test_fetch_stock_st_normalises_and_rejects_page_limit():
    pro = _FakePro({
        "20260617": pd.DataFrame(
            {"ts_code": ["300029.SZ"], "name": ["*ST天龙"],
             "trade_date": ["20260617"], "type": ["ST"]}
        )
    })
    out = fetch_stock_st(pro, "2026-06-17")
    assert list(out.columns) == ["symbol", "date", "name", "source"]
    assert out.iloc[0]["symbol"] == "SZ300029"
    assert out.iloc[0]["date"] == "2026-06-17"
    assert out.iloc[0]["source"] == "stock_st"
    assert pro.st_calls == [{"trade_date": "20260617"}]

    big = pd.DataFrame({
        "ts_code": [f"{i:06d}.SZ" for i in range(1000)],
        "name": ["ST假"] * 1000,
        "trade_date": ["20260430"] * 1000,
        "type": ["ST"] * 1000,
    })
    try:
        fetch_stock_st(_FakePro({"20260430": big}), "2026-04-30")
    except ValueError as exc:
        assert "1000" in str(exc)
    else:
        raise AssertionError("expected truncation error")


def test_fetch_namechange_slices_by_year_and_rejects_10000():
    seg = pd.DataFrame({
        "ts_code": ["300029.SZ"], "name": ["天龙退"],
        "start_date": ["20260618"], "end_date": [None],
        "ann_date": ["20260610"], "change_reason": ["退市整理期"],
    })
    pro = _FakePro(nc=seg)
    out = fetch_namechange(pro, range(2025, 2027))
    # 2 个年度分片 + 1 次无日期全量兜底（ann_date 为空的行）
    assert len(pro.nc_calls) == 3
    assert any("start_date" not in c for c in pro.nc_calls)
    assert len(out) == 1

    big = pd.DataFrame({
        "ts_code": [f"{i:06d}.SZ" for i in range(10000)],
        "name": ["ST假"] * 10000, "start_date": ["20200101"] * 10000,
        "end_date": [None] * 10000, "ann_date": ["20200101"] * 10000,
        "change_reason": ["ST"] * 10000,
    })
    try:
        fetch_namechange(_FakePro(nc=big), range(2025, 2026))
    except ValueError as exc:
        assert "10000" in str(exc)
    else:
        raise AssertionError("expected truncation error")


def test_update_backfill_writes_daily_with_both_sources(tmp_path):
    qlib_dir = tmp_path / "cn_data"
    (qlib_dir / "calendars").mkdir(parents=True)
    (qlib_dir / "calendars/day.txt").write_text(
        "2026-06-17\n2026-06-18\n2026-06-19\n", encoding="utf-8"
    )
    pro = _FakePro(
        st_by_date={
            "20260617": pd.DataFrame(
                {"ts_code": ["300029.SZ"], "name": ["*ST天龙"],
                 "trade_date": ["20260617"], "type": ["ST"]}
            )
        },
        nc=pd.DataFrame({
            "ts_code": ["300029.SZ"], "name": ["天龙退"],
            "start_date": ["20260618"], "end_date": [None],
            "ann_date": ["20260610"], "change_reason": ["退市整理期"],
        }),
        delist=pd.DataFrame(
            {"ts_code": ["300029.SZ"], "name": ["天龙退"], "delist_date": ["20260619"]}
        ),
    )
    daily_path = tmp_path / "st_daily.csv"
    stats = update(
        pro=pro, qlib_dir=qlib_dir, backfill=True,
        daily_path=daily_path,
        raw_path=tmp_path / "st_namechange_raw.csv",
        interval_path=tmp_path / "st_calendar.csv",
    )
    daily = pd.read_csv(daily_path, dtype=str)
    got = {(r.date, r.symbol, r.source) for r in daily.itertuples()}
    assert ("2026-06-17", "SZ300029", "stock_st") in got
    assert ("2026-06-18", "SZ300029", "namechange") in got
    assert ("2026-06-19", "SZ300029", "namechange") in got
    assert stats["n_rows"] == 3


def test_update_without_cache_and_without_backfill_fails(tmp_path):
    qlib_dir = tmp_path / "cn_data"
    (qlib_dir / "calendars").mkdir(parents=True)
    (qlib_dir / "calendars/day.txt").write_text("2026-06-17\n", encoding="utf-8")
    try:
        update(pro=_FakePro(), qlib_dir=qlib_dir,
               daily_path=tmp_path / "missing.csv",
               raw_path=tmp_path / "raw.csv",
               interval_path=tmp_path / "cal.csv")
    except SystemExit as exc:
        assert "backfill" in str(exc)
    else:
        raise AssertionError("cron must not silently build a partial index")
