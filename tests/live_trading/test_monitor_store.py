"""MonitorStore：建表、upsert 幂等、告警去重。"""
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.monitor_store import MonitorStore


@pytest.fixture
def store(tmp_path):
    return MonitorStore(str(tmp_path / "live.db"))


def _snapshot_row(date="2026-07-13", total=500000.0):
    return {
        "date": date,
        "cash": 500000.0,
        "market_value": total - 500000.0,
        "total_value": total,
        "daily_return": None,
        "cumulative_return": 0.0,
        "benchmark_close": 4000.0,
        "benchmark_daily_return": None,
        "benchmark_cumulative_return": 0.0,
        "excess_return": None,
        "position_count": 0,
        "turnover": None,
    }


def test_reopen_existing_db(tmp_path):
    db = str(tmp_path / "live.db")
    MonitorStore(db)
    MonitorStore(db)  # 建表幂等


def test_existing_snapshot_table_migrates_account_value_adjustment(tmp_path):
    db = str(tmp_path / "legacy.db")
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE daily_snapshot (
                date TEXT PRIMARY KEY,
                cash REAL NOT NULL,
                market_value REAL NOT NULL,
                receivables REAL NOT NULL DEFAULT 0,
                pending_market_value REAL NOT NULL DEFAULT 0,
                tax_provision REAL NOT NULL DEFAULT 0,
                total_value REAL NOT NULL,
                daily_return REAL,
                cumulative_return REAL,
                benchmark_close REAL,
                benchmark_daily_return REAL,
                benchmark_cumulative_return REAL,
                excess_return REAL,
                position_count INTEGER NOT NULL,
                turnover REAL,
                fees REAL NOT NULL DEFAULT 0,
                external_flow REAL NOT NULL DEFAULT 0
            )
        """)

    migrated = MonitorStore(db)
    row = _snapshot_row(total=9_268_587.08)
    row["cash"] = 9_949_714.06
    row["market_value"] = 0.0
    row["account_value_adjustment"] = -681_126.98
    migrated.upsert_daily_snapshot(row)

    snapshot = migrated.get_latest_snapshot()
    assert snapshot["account_value_adjustment"] == pytest.approx(-681_126.98)


def test_upsert_daily_snapshot_overwrites(store):
    store.upsert_daily_snapshot(_snapshot_row(total=500000.0))
    store.upsert_daily_snapshot(_snapshot_row(total=510000.0))
    snaps = store.get_snapshots()
    assert len(snaps) == 1
    assert snaps[0]["total_value"] == 510000.0


def test_snapshots_ordered_and_filtered(store):
    for d in ["2026-07-15", "2026-07-13", "2026-07-14"]:
        store.upsert_daily_snapshot(_snapshot_row(date=d))
    snaps = store.get_snapshots()
    assert [s["date"] for s in snaps] == ["2026-07-13", "2026-07-14", "2026-07-15"]
    assert store.get_latest_snapshot()["date"] == "2026-07-15"
    assert store.get_first_snapshot()["date"] == "2026-07-13"
    ranged = store.get_snapshots(start="2026-07-14", end="2026-07-14")
    assert [s["date"] for s in ranged] == ["2026-07-14"]


def test_position_snapshot_rerun_no_dup(store):
    rows = [
        {"stock_code": "600000.SH", "shares": 800, "avg_cost": 10.5,
         "close_price": 11.0, "market_value": 8800.0, "profit": 400.0,
         "weight": 0.5},
        {"stock_code": "000001.SZ", "shares": 500, "avg_cost": 12.0,
         "close_price": None, "market_value": 6000.0, "profit": 0.0,
         "weight": 0.4},
    ]
    store.upsert_position_snapshots("2026-07-13", rows)
    store.upsert_position_snapshots("2026-07-13", rows)  # 重跑覆盖
    got = store.get_position_snapshots("2026-07-13")
    assert len(got) == 2
    assert got[0]["stock_code"] == "600000.SH"  # market_value 降序
    assert got[1]["close_price"] is None


def test_pipeline_events(store):
    store.record_pipeline_event("2026-07-13", "report", "OK", "snapshot built")
    store.record_pipeline_event("2026-07-13", "evening", "FAIL", "no batch")
    events = store.get_pipeline_events(trade_date="2026-07-13")
    assert [e["stage"] for e in events] == ["report", "evening"]
    assert store.get_pipeline_events(days=5)[0]["trade_date"] == "2026-07-13"


def test_alert_dedup_same_day_same_rule(store):
    assert store.try_record_alert("2026-07-13", "CRIT", "PUBLISH_MISSING", "x")
    assert not store.try_record_alert("2026-07-13", "CRIT", "PUBLISH_MISSING", "y")
    assert store.try_record_alert("2026-07-14", "CRIT", "PUBLISH_MISSING", "z")
    assert store.try_record_alert("2026-07-13", "WARN", "DAILY_LOSS", "w")
    assert len(store.get_alerts()) == 3


def test_mark_alert_sent(store):
    store.try_record_alert("2026-07-13", "CRIT", "FILLS_MISSING", "m")
    store.mark_alert_sent("2026-07-13", "FILLS_MISSING", "serverchan", True)
    alert = store.get_alerts()[0]
    assert alert["channel"] == "serverchan"
    assert alert["sent_ok"] == 1
