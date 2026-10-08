"""The import CLI must settle calendar sessions, never the latest future plan."""
import sys
from datetime import datetime

import pytest

from live_trading.modules.cohort_store import CohortState
from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.signal_schema import BatchHeader, SchemaError, SignalOrder
from live_trading.scripts import run_import_fills as cli


def run_import(tmp_path, monkeypatch, day, sessions, *, state=None):
    db = tmp_path / "ledger.db"
    rec = LiveRecorder(str(db), opening_cash=100000)
    if state is not None:
        rec.save_cohort_state(state)
    config = {"live": {"strategy_id": "s", "bridge_root": str(tmp_path / "bridge")},
              "storage": {"db_path": str(db)}, "strategy": {"class": "CohortLadderStrategy", "horizon": 5}}
    monkeypatch.setattr(cli, "load_live_config", lambda *a: config)
    # Only the network calendar is stubbed; use real SQLite, importer and CLI.
    monkeypatch.setattr(cli, "next_open_date", lambda d: next(s for s in sessions if s > d), raising=False)
    monkeypatch.setenv("QLIB_LIVE_BUSINESS_DATE", day)
    monkeypatch.setattr(sys, "argv", ["run_import_fills.py", "--config", "s"])
    return rec


def record_empty_plan(rec, day):
    rec.record_publish_plan(BatchHeader(
        batch_id=day.replace("-", "") + "_s_001", strategy_id="s", trade_date=day,
        signal_date="2026-09-30", account_type="STOCK", created_at="2026-09-30T20:00:00+08:00",
        order_count=0, checksum="",
    ), [])


def test_holiday_import_does_not_advance_future_plan(tmp_path, monkeypatch):
    original = CohortState(layers=(("2026-09-30", {"SH688775": 500}),))
    rec = run_import(tmp_path, monkeypatch, "2026-10-01", ["2026-10-08"], state=original)
    record_empty_plan(rec, "2026-10-08")
    cli.main()
    assert rec.load_cohort_state() == original


def test_import_pinned_date_not_newest_plan_and_catches_up_only_open_days(tmp_path, monkeypatch):
    rec = run_import(tmp_path, monkeypatch, "2026-09-29",
                     ["2026-09-28", "2026-09-29", "2026-09-30"],
                     state=CohortState(layers=(("2026-09-24", {}),)))
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        record_empty_plan(rec, day)
    cli.main()
    assert rec.load_cohort_state().layers == (("2026-09-24", {}), ("2026-09-28", {}), ("2026-09-29", {}))
    cli.main()
    assert len(rec.load_cohort_state().layers) == 3


def test_no_batch_on_open_day_still_adds_empty_layer(tmp_path, monkeypatch):
    rec = run_import(tmp_path, monkeypatch, "2026-09-28", ["2026-09-28", "2026-09-29"],
                     state=CohortState(layers=(("2026-09-24", {}),)))
    cli.main()
    assert rec.load_cohort_state().layers[-1] == ("2026-09-28", {})


def test_import_cli_explicit_date_overrides_environment(tmp_path, monkeypatch):
    rec = run_import(tmp_path, monkeypatch, "2026-09-30", ["2026-09-28", "2026-09-29"],
                     state=CohortState(layers=(("2026-09-24", {}),)))
    monkeypatch.setattr(sys, "argv", ["run_import_fills.py", "--config", "s", "--date", "2026-09-28"])
    cli.main()
    assert rec.load_cohort_state().layers[-1] == ("2026-09-28", {})


def test_unfinished_earlier_day_blocks_later_cohort_dates(tmp_path, monkeypatch):
    before = CohortState(layers=(("2026-09-24", {}),))
    rec = run_import(tmp_path, monkeypatch, "2026-09-29", ["2026-09-28", "2026-09-29"], state=before)
    rec.record_publish_plan(BatchHeader(
        batch_id="20260928_s_001", strategy_id="s", trade_date="2026-09-28",
        signal_date="2026-09-24", account_type="STOCK", created_at="2026-09-24T20:00:00+08:00",
        order_count=1, checksum="",
    ), [SignalOrder("20260928_s_001", "20260928001001B", "600000.SH", "BUY", 0,
                    1000.0, "CLOSE_AUCTION_LIMIT", 0.0, 20, "SH600000", "cohort_layer")])
    record_empty_plan(rec, "2026-09-29")
    with pytest.raises(SchemaError, match="incomplete"):
        cli.main()
    assert rec.load_cohort_state() == before


def test_calendar_outage_does_not_insert_a_layer(tmp_path, monkeypatch):
    before = CohortState(layers=(("2026-09-24", {}),))
    rec = run_import(tmp_path, monkeypatch, "2026-09-28", [], state=before)
    def unavailable(day):
        raise RuntimeError("calendar unavailable")
    monkeypatch.setattr(cli, "next_open_date", unavailable)
    with pytest.raises(RuntimeError, match="calendar unavailable"):
        cli.main()
    assert rec.load_cohort_state() == before


def test_morning_manual_import_cannot_finalize_todays_empty_plan(tmp_path, monkeypatch):
    class Morning(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 8, 9, 0, tzinfo=tz)
    monkeypatch.setattr(cli, "datetime", Morning)
    before = CohortState(layers=(("2026-09-30", {}),))
    rec = run_import(tmp_path, monkeypatch, "2026-10-08", ["2026-10-08"], state=before)
    record_empty_plan(rec, "2026-10-08")
    cli.main()
    assert rec.load_cohort_state() == before
