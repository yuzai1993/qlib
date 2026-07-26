from __future__ import annotations

import pandas as pd

from scripts.data_collector.csindex_v2 import hybrid_backfill as backfill
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


def test_required_month_ends_follow_local_calendar():
    """Catches requesting non-trading calendar month ends from Tushare."""
    calendar = [
        "2010-01-04",
        "2010-01-29",
        "2010-02-01",
        "2010-02-26",
        "2010-03-01",
    ]

    result = backfill.required_month_end_dates(
        calendar, "2010-01", "2010-02"
    )

    assert result == ["20100129", "20100226"]


def test_merge_cache_is_idempotent_and_sorted():
    """Catches duplicate cache rows after a resumed backfill."""
    existing = pd.DataFrame(
        [{"trade_date": "20100129", "con_code": "000001.SZ"}]
    )
    incoming = pd.DataFrame(
        [
            {"trade_date": "20100129", "con_code": "000001.SZ"},
            {"trade_date": "20100226", "con_code": "600000.SH"},
        ]
    )

    merged = backfill.merge_cache(
        existing, incoming, ["trade_date", "con_code"]
    )

    assert merged.to_dict("records") == [
        {"trade_date": "20100129", "con_code": "000001.SZ"},
        {"trade_date": "20100226", "con_code": "600000.SH"},
    ]


class _FakeDailyBasicPro:
    def __init__(self):
        self.daily_calls: list[str] = []

    def daily_basic(self, **kwargs):
        trade_date = kwargs["trade_date"]
        self.daily_calls.append(trade_date)
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600000.SH"],
                "trade_date": [trade_date, trade_date],
                "total_mv": [10.0, 20.0],
            }
        )


def test_total_mv_backfill_skips_cached_dates(tmp_path):
    """Catches repeat API calls and lost rows when resuming a partial cache."""
    dest = tmp_path / "total_mv.parquet"
    pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20100129"],
            "total_mv": [10.0],
        }
    ).to_parquet(dest)
    pro = _FakeDailyBasicPro()
    calendar = ["2010-01-29", "2010-02-01", "2010-02-26"]

    backfill.backfill_total_mv(
        pro,
        dest,
        calendar,
        sleep_seconds=0,
        start_month="2010-01",
        end_month="2010-02",
    )

    cached = pd.read_parquet(dest)
    assert pro.daily_calls == ["20100226"]
    assert set(cached["trade_date"]) == {"20100129", "20100226"}
    assert len(cached) == 3


class _FakeIndexWeightPro:
    def __init__(self, response: pd.DataFrame | None = None):
        self.response = response
        self.index_calls: list[dict] = []

    def index_weight(self, **kwargs):
        self.index_calls.append(kwargs)
        if self.response is None:
            raise AssertionError("完整旧缓存不应触发 index_weight 请求")
        return self.response.copy()


def test_index_weight_backfill_migrates_complete_legacy_month(tmp_path):
    """Catches needless API requests when the legacy snapshot is complete."""
    legacy_dir = tmp_path / "legacy"
    snapshot_dir = tmp_path / "hybrid"
    legacy_dir.mkdir()
    legacy = pd.DataFrame(
        {
            "trade_date": ["20100129", "20100129"],
            "con_code": ["000001.SZ", "600000.SH"],
        }
    )
    legacy.to_parquet(legacy_dir / "csi500.parquet")
    pro = _FakeIndexWeightPro()

    paths = backfill.backfill_index_weights(
        pro,
        snapshot_dir,
        legacy_dir,
        sleep_seconds=0,
        specs={"csi500": ("000905.SH", "2010-01", "2010-01", 2)},
    )

    migrated = pd.read_parquet(paths["csi500"])
    assert pro.index_calls == []
    assert migrated.to_dict("records") == legacy.to_dict("records")


def test_index_weight_backfill_keeps_latest_snapshot_in_month(tmp_path):
    """Catches mixing multiple source dates into one monthly roster."""
    response = pd.DataFrame(
        {
            "trade_date": [
                "20100201",
                "20100201",
                "20100226",
                "20100226",
            ],
            "con_code": [
                "000001.SZ",
                "600000.SH",
                "000002.SZ",
                "600001.SH",
            ],
            "weight": [60.0, 40.0, 55.0, 45.0],
        }
    )
    pro = _FakeIndexWeightPro(response)

    paths = backfill.backfill_index_weights(
        pro,
        tmp_path / "hybrid",
        tmp_path / "legacy",
        sleep_seconds=0,
        specs={"csi500": ("000905.SH", "2010-02", "2010-02", 2)},
    )

    cached = pd.read_parquet(paths["csi500"])
    assert set(cached["trade_date"]) == {"20100226"}
    assert set(cached["con_code"]) == {"000002.SZ", "600001.SH"}


def test_get_tushare_pro_requires_environment_token(monkeypatch):
    """Catches silent anonymous access or an embedded credential fallback."""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    try:
        backfill.get_tushare_pro()
    except RuntimeError as error:
        assert str(error) == "TUSHARE_TOKEN 未配置"
    else:
        raise AssertionError("未配置 token 时必须失败")


class _FakeBackfillPro(_FakeDailyBasicPro):
    def index_weight(self, **kwargs):
        return pd.DataFrame(
            {
                "trade_date": ["20100129", "20100129"],
                "con_code": ["000001.SZ", "600000.SH"],
            }
        )


def test_backfill_all_returns_complete_cache_paths(tmp_path):
    """Catches orchestration that prepares only one of the required inputs."""
    calendar_path = tmp_path / "day.txt"
    calendar_path.write_text("2010-01-04\n2010-01-29\n")

    result = backfill.backfill_all(
        hybrid_root=tmp_path / "hybrid",
        legacy_dir=tmp_path / "legacy",
        calendar_path=calendar_path,
        pro=_FakeBackfillPro(),
        sleep_seconds=0,
        index_specs={"csi500": ("000905.SH", "2010-01", "2010-01", 2)},
        total_mv_start="2010-01",
        total_mv_end="2010-01",
    )

    assert set(result) == {"csi500", "total_mv_monthly"}
    assert all(path.exists() for path in result.values())
