from __future__ import annotations

import pandas as pd

from scripts.data_collector.csindex_v2 import hybrid_history as hybrid


def test_ts_code_conversion_rejects_unknown_exchanges():
    """Catches accepting malformed or non-A-share Tushare symbols."""
    assert hybrid.ts_code_to_symbol("600000.SH") == "SH600000"
    assert hybrid.ts_code_to_symbol("000001.SZ") == "SZ000001"
    assert hybrid.ts_code_to_symbol("920001.BJ") == "BJ920001"
    assert hybrid.ts_code_to_symbol("ABC.HK") is None


def test_proxy_selection_excludes_indices_and_breaks_ties_by_symbol():
    """Catches leakage from excluded large-cap pools and unstable tie handling."""
    frame = pd.DataFrame(
        {
            "ts_code": [
                "000003.SZ",
                "000002.SZ",
                "000001.SZ",
                "600000.SH",
            ],
            "total_mv": [20.0, 20.0, 100.0, 10.0],
        }
    )

    selected = hybrid.select_csi1000_proxy(
        frame, excluded={"SZ000001"}, limit=2
    )

    assert selected == {"SZ000002", "SZ000003"}


def test_rosters_become_non_overlapping_closed_intervals():
    """Catches off-by-one membership at the monthly handoff boundary."""
    calendar = [
        "2010-01-29",
        "2010-02-01",
        "2010-02-26",
        "2010-03-01",
    ]

    intervals = hybrid.rosters_to_closed_intervals(
        [
            ("2010-01-29", {"SH600000", "SZ000001"}),
            ("2010-02-26", {"SH600000", "SZ000002"}),
        ],
        calendar,
        final_end="2010-03-01",
        source="fixture",
    )

    assert set(
        map(tuple, intervals[["symbol", "start", "end"]].to_numpy())
    ) == {
        ("SH600000", "2010-01-29", "2010-03-01"),
        ("SZ000001", "2010-01-29", "2010-02-01"),
        ("SZ000002", "2010-02-26", "2010-03-01"),
    }
    assert hybrid.active_members(intervals, "2010-02-01") == {
        "SH600000",
        "SZ000001",
    }
    assert hybrid.active_members(intervals, "2010-02-26") == {
        "SH600000",
        "SZ000002",
    }
