"""实盘宇宙过滤：索引变换与转发契约（真实四项过滤在全A dry-run 里实测）。"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from live_trading.modules import universe_gate

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_filter_scores_nans_out_excluded_names(monkeypatch):
    captured = {}

    def fake_build_keep_mask(index, spec):
        captured["index"] = index
        captured["spec"] = spec
        inst = index.get_level_values("instrument")
        return pd.Series(inst != "SZ000001", index=index)

    monkeypatch.setattr(universe_gate, "build_keep_mask", fake_build_keep_mask)

    scores = pd.Series(
        {"SH600000": 1.0, "SZ000001": 2.0, "SH600519": 3.0}, dtype=float
    )
    out, stats = universe_gate.filter_scores(
        scores,
        signal_date="2026-08-20",
        raw_spec=_raw_spec(),
        project_root=PROJECT_ROOT,
    )

    assert not isinstance(out.index, pd.MultiIndex)
    assert list(out.index) == ["SH600000", "SZ000001", "SH600519"]
    assert np.isnan(out["SZ000001"])
    assert out["SH600000"] == 1.0 and out["SH600519"] == 3.0
    assert stats["n_raw"] == 3 and stats["n_keep"] == 2


def _raw_spec(**overrides):
    spec = {
        "st_daily": "scripts/data_collector/tushare/st_daily.csv",
        "min_amount": 10_000_000,
        "min_listing_days": 60,
        "min_recent_trading_days": 60,
        "pool": "all",
    }
    spec.update(overrides)
    return spec


def test_filter_scores_passes_single_day_index_and_parsed_spec(monkeypatch):
    captured = {}

    def fake_build_keep_mask(index, spec):
        captured["index"] = index
        captured["spec"] = spec
        return pd.Series(True, index=index)

    monkeypatch.setattr(universe_gate, "build_keep_mask", fake_build_keep_mask)

    universe_gate.filter_scores(
        pd.Series({"SH600000": 1.0}, dtype=float),
        signal_date="2026-08-20",
        raw_spec=_raw_spec(),
        project_root=PROJECT_ROOT,
    )

    index = captured["index"]
    assert index.names == ["datetime", "instrument"]
    assert index.get_level_values("datetime").unique().tolist() == [
        pd.Timestamp("2026-08-20")
    ]
    spec = captured["spec"]
    assert spec.min_amount == 10_000_000
    assert spec.min_listing_days == 60
    assert spec.min_recent_trading_days == 60
    assert spec.pool == "all"
    assert spec.st_daily.name == "st_daily.csv"


def test_filter_scores_reports_st_hits_from_mask_attrs(monkeypatch):
    def fake_build_keep_mask(index, spec):
        keep = pd.Series(True, index=index)
        keep.attrs["n_st_hits"] = 7
        return keep

    monkeypatch.setattr(universe_gate, "build_keep_mask", fake_build_keep_mask)

    _, stats = universe_gate.filter_scores(
        pd.Series({"SH600000": 1.0}, dtype=float),
        signal_date="2026-08-20",
        raw_spec=_raw_spec(),
        project_root=PROJECT_ROOT,
    )

    assert stats["n_st_hits"] == 7


def test_filter_scores_rejects_empty_input():
    with pytest.raises(ValueError, match="empty"):
        universe_gate.filter_scores(
            pd.Series(dtype=float),
            signal_date="2026-08-20",
            raw_spec=_raw_spec(),
            project_root=PROJECT_ROOT,
        )


def test_filter_scores_requires_all_four_filter_items():
    raw_spec = _raw_spec()
    del raw_spec["min_amount"]

    with pytest.raises(ValueError, match="min_amount"):
        universe_gate.filter_scores(
            pd.Series({"SH600000": 1.0}, dtype=float),
            signal_date="2026-08-20",
            raw_spec=raw_spec,
            project_root=PROJECT_ROOT,
        )
