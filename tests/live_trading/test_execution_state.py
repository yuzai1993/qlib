"""Durable, per-strategy execution state contracts."""

import sys

import pytest

from live_trading.modules.execution_state import (
    ExecutionStateError,
    validate_identifier,
)
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


def test_active_execution_state_also_requires_a_reason(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))

    with pytest.raises(ExecutionStateError, match="reason"):
        recorder.set_execution_state(
            "main", "ACTIVE", " \n ", "2026-08-10T20:00:00+08:00",
        )


@pytest.mark.parametrize("state", ["ACTIVE", "PAUSED"])
def test_execution_state_cli_rejects_blank_transition_reason(
    monkeypatch, capsys, state,
):
    from live_trading.scripts import set_execution_state

    monkeypatch.setattr(
        sys, "argv", [
            "set_execution_state.py", "--config", "main", "--state", state,
            "--reason", " \n ",
        ],
    )

    with pytest.raises(SystemExit):
        set_execution_state.parse_args()
    assert "reason" in capsys.readouterr().err


def test_execution_state_cli_rejects_unsafe_config_before_loading_it(
    monkeypatch, capsys,
):
    from live_trading.scripts import set_execution_state

    monkeypatch.setattr(
        sys, "argv", [
            "set_execution_state.py", "--config", "../main", "--get",
        ],
    )

    with pytest.raises(SystemExit):
        set_execution_state.parse_args()
    assert "safe identifier" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["..", "../main", "main/name", "main\\name", "/main", "main id", "main\nnext"])
def test_identifier_validator_rejects_path_and_whitespace_values(value):
    with pytest.raises(ExecutionStateError, match="identifier"):
        validate_identifier(value, "strategy_id")


def test_execution_state_rejects_unknown_values(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))

    with pytest.raises(ExecutionStateError, match="ACTIVE or PAUSED"):
        recorder.set_execution_state("main", "RUNNING", "not supported", "2026-08-10T20:00:00+08:00")
