"""夜间实盘发布必须解析下一个真实开市日。"""

from pathlib import Path
import sys

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


class FakePro:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def trade_cal(self, **kwargs):
        self.calls.append(kwargs)
        return pd.DataFrame(self.rows)


def test_next_open_date_skips_weekend_and_sorts_results():
    from live_trading.scripts.next_trade_date import next_open_date

    pro = FakePro([
        {"cal_date": "20260720", "is_open": 1},
        {"cal_date": "20260718", "is_open": 0},
        {"cal_date": "20260719", "is_open": 0},
    ])
    assert next_open_date("2026-07-17", pro=pro) == "2026-07-20"
    assert pro.calls == [{
        "exchange": "",
        "start_date": "20260718",
        "end_date": "20260731",
        "is_open": "1",
    }]


def test_next_open_date_fails_closed_when_calendar_empty():
    from live_trading.scripts.next_trade_date import next_open_date

    with pytest.raises(RuntimeError, match="no open trading day"):
        next_open_date("2026-07-17", pro=FakePro([]))
