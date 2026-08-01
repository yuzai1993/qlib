"""Operational wrappers must default to the controlled CSI1000 deployment."""

import sqlite3
from pathlib import Path

from live_trading.scripts.batch_status import find_active_batch_id


REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPERS = [
    "run_publish_cron.sh",
    "run_publish_catchup_cron.sh",
    "run_import_cron.sh",
    "run_monitor_cron.sh",
]


def test_wrappers_are_configurable_and_default_to_new_simulation_system():
    for name in WRAPPERS:
        text = (REPO_ROOT / "live_trading" / name).read_text(encoding="utf-8")
        assert "csi1000_b6m_b2s_postclose" in text
        assert "LIVE_CONFIG_ID" in text
        assert "QLIB_LIVE_CONFIG_ID" in text
        assert "LOCK_DIR" in text
        assert 'CONFIG_ID="csi300_topk10_live"' not in text


def test_wrappers_do_not_swallow_monitor_failures_or_embed_python_stdin():
    combined = "\n".join(
        (REPO_ROOT / "live_trading" / name).read_text(encoding="utf-8")
        for name in WRAPPERS
    )
    assert "|| true" not in combined
    assert "<<'PY'" not in combined
    assert "batch_status.py" in combined


def test_wrappers_log_the_real_exit_status_while_releasing_locks():
    for name in WRAPPERS:
        text = (REPO_ROOT / "live_trading" / name).read_text(encoding="utf-8")
        assert "finish_job()" in text
        assert "job_status=$?" in text
        assert "trap finish_job EXIT" in text
        assert 'exit "$job_status"' in text


def test_batch_status_finds_latest_active_batch(tmp_path):
    db = tmp_path / "live.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE batches (batch_id TEXT PRIMARY KEY, trade_date TEXT, "
            "superseded_by TEXT)"
        )
        conn.executemany(
            "INSERT INTO batches VALUES (?,?,?)",
            [
                ("20260803_s_001", "2026-08-03", "20260803_s_002"),
                ("20260803_s_002", "2026-08-03", None),
            ],
        )

    assert find_active_batch_id(db, "2026-08-03") == "20260803_s_002"
    assert find_active_batch_id(db, "2026-08-04") == ""
