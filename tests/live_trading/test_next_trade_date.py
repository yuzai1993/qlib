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


def test_evening_monitor_does_not_hide_failed_friday_publish(monkeypatch, tmp_path):
    from live_trading.scripts import run_monitor

    class EmptyRecorder:
        @staticmethod
        def get_active_batches_by_date(trade_date):
            assert trade_date == "2026-07-20"
            return []

    monkeypatch.setattr(
        run_monitor, "next_open_date", lambda date: "2026-07-20",
        raising=False,
    )
    findings = run_monitor.run_evening(
        "2026-07-17", EmptyRecorder(),
        {"live": {"bridge_root": str(tmp_path)}},
    )

    assert len(findings) == 1
    assert findings[0].rule == "PUBLISH_MISSING"
    assert "2026-07-20" in findings[0].message


def test_evening_monitor_ignores_superseded_higher_sequence(monkeypatch, tmp_path):
    from live_trading.modules.fill_importer import LiveRecorder
    from live_trading.scripts import run_monitor

    recorder = LiveRecorder(str(tmp_path / "live.db"))
    active = "20260720_csi300_topk10_003"
    superseded = "20260720_csi300_topk10_004"
    recorder.record_batch(active, "2026-07-20", "LIVE", 10)
    recorder.record_batch(superseded, "2026-07-20", "LIVE", 10)
    recorder.supersede_batch(superseded, active)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / f"signal_{active}.jsonl").write_text("test\n", encoding="utf-8")
    (inbox / f"signal_{active}.done").write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr(
        run_monitor, "next_open_date", lambda date: "2026-07-20",
        raising=False,
    )

    findings = run_monitor.run_evening(
        "2026-07-17", recorder,
        {"live": {"bridge_root": str(tmp_path)}},
    )

    assert findings == []


def test_postmarket_with_active_batch_may_run_before_calendar_update():
    from live_trading.scripts import run_monitor

    assert run_monitor._may_run_with_stale_calendar("postmarket", [{"batch_id": "b"}])
    assert not run_monitor._may_run_with_stale_calendar("postmarket", [])
    assert not run_monitor._may_run_with_stale_calendar(
        "report", [{"batch_id": "b"}],
    )
