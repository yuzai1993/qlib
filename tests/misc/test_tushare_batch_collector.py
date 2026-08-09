"""Tushare 单交易日全市场批量采集测试。"""

import sys
from pathlib import Path

import pandas as pd
import pytest


COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "scripts" / "data_collector" / "tushare"
sys.path.insert(0, str(COLLECTOR_DIR))

import collector as tushare_collector  # noqa: E402


def _collector(instruments=("000001.sz", "000002.sz")):
    obj = tushare_collector.TushareCollectorCN.__new__(tushare_collector.TushareCollectorCN)
    obj.instrument_list = list(instruments)
    obj.start_datetime = pd.Timestamp("2026-08-05")
    obj.end_datetime = pd.Timestamp("2026-08-05")
    obj._limit_nums_requested = False
    return obj


def _daily(*codes, trade_date="20260805"):
    return pd.DataFrame(
        {
            "ts_code": list(codes),
            "trade_date": [trade_date] * len(codes),
            "open": [10.0 + i for i in range(len(codes))],
            "high": [10.5 + i for i in range(len(codes))],
            "low": [9.5 + i for i in range(len(codes))],
            "close": [10.2 + i for i in range(len(codes))],
            "vol": [1000.0 + i for i in range(len(codes))],
            "amount": [1020.0 + i for i in range(len(codes))],
            "pct_chg": [2.0 + i for i in range(len(codes))],
        }
    )


def _adj(*codes, trade_date="20260805"):
    return pd.DataFrame(
        {
            "ts_code": list(codes),
            "trade_date": [trade_date] * len(codes),
            "adj_factor": [1.5 + i for i in range(len(codes))],
        }
    )


def test_prepare_trade_date_batch_merges_and_filters_universe():
    obj = _collector()

    result = obj._prepare_trade_date_batch(
        _daily("000001.SZ", "000002.SZ", "430001.BJ"),
        _adj("000001.SZ"),
        "20260805",
    )

    assert result["symbol"].tolist() == ["000001.sz", "000002.sz"]
    assert result["date"].dt.strftime("%Y%m%d").tolist() == ["20260805", "20260805"]
    assert result["volume"].tolist() == [1000.0, 1001.0]
    assert result["adj_factor"].tolist() == [1.5, 1.0]
    assert result.columns.tolist() == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adj_factor",
        "pct_chg",
        "symbol",
    ]


@pytest.mark.parametrize(
    ("daily", "adj", "match"),
    [
        (pd.DataFrame(), _adj("000001.SZ"), "daily batch is empty"),
        (_daily("000001.SZ", trade_date="20260804"), _adj("000001.SZ"), "unexpected trade_date"),
        (_daily("000001.SZ").drop(columns=["close"]), _adj("000001.SZ"), "missing columns"),
        (_daily("000001.SZ"), pd.DataFrame(), "adj_factor batch is empty"),
    ],
)
def test_prepare_trade_date_batch_rejects_malformed_responses(daily, adj, match):
    with pytest.raises(ValueError, match=match):
        _collector(("000001.sz",))._prepare_trade_date_batch(daily, adj, "20260805")


def test_prepare_trade_date_batch_rejects_duplicate_stock_date():
    daily = pd.concat([_daily("000001.SZ"), _daily("000001.SZ")], ignore_index=True)

    with pytest.raises(ValueError, match="duplicate"):
        _collector(("000001.sz",))._prepare_trade_date_batch(daily, _adj("000001.SZ"), "20260805")


def test_prepare_trade_date_batch_rejects_tushare_row_limit():
    codes = [f"{i:06d}.SZ" for i in range(6000)]

    with pytest.raises(ValueError, match="row limit"):
        _collector(tuple(code.lower() for code in codes))._prepare_trade_date_batch(
            _daily(*codes), _adj(*codes), "20260805"
        )


def test_prepare_trade_date_batch_rejects_implausibly_low_universe_coverage():
    instruments = tuple(f"{i:06d}.sz" for i in range(100))
    returned = [f"{i:06d}.SZ" for i in range(80)]

    with pytest.raises(ValueError, match="coverage"):
        _collector(instruments)._prepare_trade_date_batch(_daily(*returned), _adj(*returned), "20260805")


class _FakePro:
    def __init__(self, daily, adj, daily_error=None):
        self.daily_frame = daily
        self.adj_frame = adj
        self.daily_error = daily_error
        self.calls = []

    def daily(self, **kwargs):
        self.calls.append(("daily", kwargs))
        if self.daily_error:
            raise self.daily_error
        return self.daily_frame.copy()

    def adj_factor(self, **kwargs):
        self.calls.append(("adj_factor", kwargs))
        return self.adj_frame.copy()


def test_one_day_collection_uses_two_market_calls_and_saves_active_stocks():
    obj = _collector(("000001.sz", "000002.sz", "000003.sz"))
    pro = _FakePro(_daily("000001.SZ", "000002.SZ"), _adj("000001.SZ", "000002.SZ"))
    saved = []
    indices = []
    obj._get_pro = lambda: pro
    obj.save_instrument = lambda symbol, frame: saved.append((symbol, frame.copy()))
    obj.download_index_data = lambda: indices.append(True)

    obj.collector_data()

    assert pro.calls == [
        ("daily", {"trade_date": "20260805"}),
        ("adj_factor", {"trade_date": "20260805"}),
    ]
    assert [symbol for symbol, _ in saved] == ["000001.sz", "000002.sz"]
    assert all(len(frame) == 1 for _, frame in saved)
    assert indices == [True]


def test_batch_failure_falls_back_to_per_symbol_collector(monkeypatch):
    obj = _collector(("000001.sz",))
    pro = _FakePro(pd.DataFrame(), pd.DataFrame(), daily_error=RuntimeError("temporary outage"))
    fallback = []
    indices = []
    obj._get_pro = lambda: pro
    obj.download_index_data = lambda: indices.append(True)
    monkeypatch.setattr(
        tushare_collector.BaseCollector,
        "collector_data",
        lambda self: fallback.append(tuple(self.instrument_list)),
    )

    obj.collector_data()

    assert fallback == [("000001.sz",)]
    assert indices == [True]


@pytest.mark.parametrize(
    ("start", "end", "limited"),
    [
        ("2026-08-04", "2026-08-05", False),
        ("2026-08-05", "2026-08-05", True),
    ],
)
def test_multi_day_and_limited_runs_keep_per_symbol_path(monkeypatch, start, end, limited):
    obj = _collector(("000001.sz",))
    obj.start_datetime = pd.Timestamp(start)
    obj.end_datetime = pd.Timestamp(end)
    obj._limit_nums_requested = limited
    fallback = []
    indices = []
    obj._get_pro = lambda: pytest.fail("batch API must not be called")
    obj.download_index_data = lambda: indices.append(True)
    monkeypatch.setattr(
        tushare_collector.BaseCollector,
        "collector_data",
        lambda self: fallback.append(tuple(self.instrument_list)),
    )

    obj.collector_data()

    assert fallback == [("000001.sz",)]
    assert indices == [True]
