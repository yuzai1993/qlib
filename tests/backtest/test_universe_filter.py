from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from universe_filter import (  # noqa: E402
    DEFAULT_ST_DAILY_REL,
    build_keep_mask,
    default_universe_filter,
    parse_universe_filter,
)


def test_parse_universe_filter_resolves_st_daily(tmp_path):
    daily = tmp_path / "st_daily.csv"
    daily.write_text(
        "symbol,date,name,source\nSZ300029,2026-04-24,*ST天龙,stock_st\n",
        encoding="utf-8",
    )
    spec = parse_universe_filter({"st_daily": str(daily), "pool": "csi300"},
                                 project_root=tmp_path)
    assert spec.st_daily == daily
    assert spec.pool == "csi300"


def test_build_keep_mask_is_date_aware(tmp_path):
    daily = tmp_path / "st_daily.csv"
    daily.write_text(
        "symbol,date,name,source\n"
        "SZ300029,2026-04-24,*ST天龙,stock_st\n"
        "SZ300029,2026-06-18,天龙退,namechange\n",
        encoding="utf-8",
    )
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2026-04-24", "2026-06-18"]), ["SZ300029", "SZ000001"]],
        names=["datetime", "instrument"],
    )
    spec = parse_universe_filter({"st_daily": str(daily), "pool": "csi300"},
                                 project_root=tmp_path)
    spec.min_amount = 0          # 避开取数，只测 ST 维度
    spec.min_listing_days = 0
    keep = build_keep_mask(idx, spec)
    assert bool(keep.loc[pd.Timestamp("2026-04-24"), "SZ300029"]) is False
    assert bool(keep.loc[pd.Timestamp("2026-06-18"), "SZ300029"]) is False
    assert bool(keep.loc[pd.Timestamp("2026-04-24"), "SZ000001"]) is True


def test_build_keep_mask_rejects_dates_beyond_cache(tmp_path):
    daily = tmp_path / "st_daily.csv"
    daily.write_text(
        "symbol,date,name,source\nSZ300029,2026-04-24,*ST天龙,stock_st\n",
        encoding="utf-8",
    )
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-08-14"), "SZ000001")], names=["datetime", "instrument"]
    )
    spec = parse_universe_filter({"st_daily": str(daily), "pool": "csi300"},
                                 project_root=tmp_path)
    spec.min_amount = 0
    spec.min_listing_days = 0
    with pytest.raises(ValueError, match="st_daily"):
        build_keep_mask(idx, spec)


def test_default_universe_filter_injects_st_daily():
    spec = default_universe_filter(None)
    assert spec["st_daily"] == DEFAULT_ST_DAILY_REL


def test_default_universe_filter_keeps_explicit_st_daily():
    spec = default_universe_filter({"st_daily": "/tmp/custom.csv", "pool": "all"})
    assert spec["st_daily"] == "/tmp/custom.csv"
    assert spec["pool"] == "all"


def test_default_universe_filter_rejects_st_names():
    with pytest.raises(ValueError, match="st_names 已废弃"):
        default_universe_filter({"st_names": "st_names.csv"})
