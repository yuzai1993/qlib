"""Durable, per-strategy execution state contracts."""

import pytest

from live_trading.modules.execution_state import ExecutionStateError
from live_trading.modules.fill_importer import LiveRecorder


def test_execution_state_defaults_to_active_without_creating_a_row(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))

    assert recorder.get_execution_state("main") == {
        "strategy_id": "main",
        "state": "ACTIVE",
        "reason": "",
        "changed_at": None,
    }
    with recorder._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM execution_state").fetchone()[0] == 0


def test_paused_execution_state_requires_a_reason(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))

    with pytest.raises(ExecutionStateError, match="reason"):
        recorder.set_execution_state("main", "PAUSED", "", "2026-08-10T20:00:00+08:00")

    recorder.set_execution_state(
        "main", "PAUSED", "operator verification pending", "2026-08-10T20:00:00+08:00",
    )
    assert recorder.get_execution_state("main") == {
        "strategy_id": "main",
        "state": "PAUSED",
        "reason": "operator verification pending",
        "changed_at": "2026-08-10T20:00:00+08:00",
    }


def test_execution_state_rejects_unknown_values(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))

    with pytest.raises(ExecutionStateError, match="ACTIVE or PAUSED"):
        recorder.set_execution_state("main", "RUNNING", "not supported", "2026-08-10T20:00:00+08:00")
