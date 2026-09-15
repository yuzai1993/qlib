"""Index outages and incomplete responses must not produce a successful update."""

import sys
from pathlib import Path

import pandas as pd
import pytest

COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "scripts/data_collector/tushare"
sys.path.insert(0, str(COLLECTOR_DIR))
import collector as tushare_collector


def _frame(code="000985.CSI", date="20260915", close=5723.6899):
    return pd.DataFrame([{
        "ts_code": code, "trade_date": date, "open": 5751.8899,
        "high": 5789.4821, "low": 5716.5009, "close": close,
        "pre_close": 5761.8847, "change": -38.1948, "pct_chg": -0.6629,
        "vol": 925759460.43, "amount": 1556380026.933,
    }])


class IndexAPI:
    def __init__(self, responses, holiday=False):
        self.responses = iter(responses)
        self.holiday = holiday

    def trade_cal(self, **kwargs):
        return pd.DataFrame({"cal_date": ["20260915"], "is_open": [0 if self.holiday else 1]})

    def index_daily(self, **kwargs):
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response.copy()


def _collector(tmp_path, api):
    obj = tushare_collector.TushareCollectorCN.__new__(tushare_collector.TushareCollectorCN)
    obj.start_datetime = pd.Timestamp("2026-09-15")
    obj.end_datetime = pd.Timestamp("2026-09-15")
    obj.save_dir = tmp_path
    obj.delay = 0
    obj.INDEX_LIST = {"csi_all": "000985"}
    obj._get_pro = lambda: api
    return obj


def test_index_transient_failure_retries_and_preserves_history(tmp_path):
    path = tmp_path / "sh000985.csv"
    pd.DataFrame([{"date": "2026-09-14", "close": 5761.8847, "symbol": "sh000985"}]).to_csv(path, index=False)
    obj = _collector(tmp_path, IndexAPI([RuntimeError("upstream date range error"), _frame()]))
    obj.download_index_data()
    rows = pd.read_csv(path)
    assert rows["date"].tolist() == ["2026-09-14", "2026-09-15"]
    assert rows["close"].tolist() == [5761.8847, 5723.6899]


@pytest.mark.parametrize("response", [
    RuntimeError("upstream date range error"), pd.DataFrame(),
    _frame(date="20260914"), _frame(close=float("nan")),
    _frame(close=0.0), _frame(code="000300.SH"),
])
def test_index_unavailable_or_invalid_fails_without_overwriting_source(tmp_path, response):
    path = tmp_path / "sh000985.csv"
    original = "date,close,symbol\n2026-09-14,5761.8847,sh000985\n"
    path.write_text(original)
    obj = _collector(tmp_path, IndexAPI([response] * 10))
    with pytest.raises(RuntimeError, match="csi_all"):
        obj.download_index_data()
    assert path.read_text() == original


def test_index_failure_does_not_skip_other_indices(tmp_path):
    class API(IndexAPI):
        def index_daily(self, **kwargs):
            if kwargs["ts_code"] == "000300.SH":
                raise RuntimeError("unavailable")
            return _frame()

    obj = _collector(tmp_path, API([]))
    obj.INDEX_LIST = {"csi300": "000300", "csi_all": "000985"}
    with pytest.raises(RuntimeError, match="csi300"):
        obj.download_index_data()
    assert pd.read_csv(tmp_path / "sh000985.csv")["close"].tolist() == [5723.6899]


def test_index_holiday_needs_no_quotes(tmp_path):
    obj = _collector(tmp_path, IndexAPI([], holiday=True))
    obj.download_index_data()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("is_open", [1.0, "1", "1.0"])
def test_index_numeric_calendar_flags_still_download(tmp_path, is_open):
    api = IndexAPI([_frame()])
    api.trade_cal = lambda **kwargs: pd.DataFrame({"cal_date": ["20260915"], "is_open": [is_open]})
    _collector(tmp_path, api).download_index_data()
    assert pd.read_csv(tmp_path / "sh000985.csv")["close"].tolist() == [5723.6899]


@pytest.mark.parametrize("is_open", [None, float("nan"), "unknown", 2])
def test_index_bad_calendar_flag_cannot_report_a_holiday(tmp_path, is_open):
    api = IndexAPI([])
    api.trade_cal = lambda **kwargs: pd.DataFrame({"cal_date": ["20260915"], "is_open": [is_open]})
    with pytest.raises(RuntimeError, match="calendar"):
        _collector(tmp_path, api).download_index_data()


def test_first_index_history_may_start_after_requested_range(tmp_path):
    api = IndexAPI([_frame()])
    api.trade_cal = lambda **kwargs: pd.DataFrame({"cal_date": ["20260914", "20260915"], "is_open": [1, 1]})
    obj = _collector(tmp_path, api)
    obj.start_datetime = pd.Timestamp("2026-09-14")
    obj.download_index_data()
    assert pd.read_csv(tmp_path / "sh000985.csv")["close"].tolist() == [5723.6899]


def test_existing_index_history_does_not_allow_truncated_prefix(tmp_path):
    (tmp_path / "sh000985.csv").write_text("date,close,symbol\n2026-09-11,5760,sh000985\n")
    api = IndexAPI([_frame()] * 10)
    api.trade_cal = lambda **kwargs: pd.DataFrame({"cal_date": ["20260914", "20260915"], "is_open": [1, 1]})
    obj = _collector(tmp_path, api)
    obj.start_datetime = pd.Timestamp("2026-09-14")
    with pytest.raises(RuntimeError, match="20260914"):
        obj.download_index_data()


def test_first_history_cannot_replace_a_trading_day_with_a_closed_day(tmp_path):
    api = IndexAPI([_frame()] * 10)
    api.trade_cal = lambda **kwargs: pd.DataFrame({"cal_date": ["20260914", "20260915"], "is_open": [1, 0]})
    obj = _collector(tmp_path, api)
    obj.start_datetime = pd.Timestamp("2026-09-14")
    with pytest.raises(RuntimeError, match="trade_date"):
        obj.download_index_data()


def test_backfill_before_local_history_still_requires_the_last_trading_day(tmp_path):
    (tmp_path / "sh000985.csv").write_text("date,close,symbol\n2026-09-16,5760,sh000985\n")
    api = IndexAPI([_frame(date="20260914")] * 10)
    api.trade_cal = lambda **kwargs: pd.DataFrame({"cal_date": ["20260914", "20260915"], "is_open": [1, 1]})
    obj = _collector(tmp_path, api)
    obj.start_datetime = pd.Timestamp("2026-09-14")
    with pytest.raises(RuntimeError, match="20260915"):
        obj.download_index_data()
