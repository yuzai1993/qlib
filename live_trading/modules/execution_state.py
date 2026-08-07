"""Durable per-strategy operating state for signal publication."""

from datetime import datetime


VALID_EXECUTION_STATES = {"ACTIVE", "PAUSED"}


class ExecutionStateError(ValueError):
    """An execution-state request is malformed or unsupported."""


class ExecutionPausedError(RuntimeError):
    """A LIVE publication was attempted for a deliberately paused strategy."""


def default_execution_state(strategy_id: str) -> dict:
    """Return the non-persisted default for strategies with no state row."""
    _require_strategy_id(strategy_id)
    return {
        "strategy_id": strategy_id,
        "state": "ACTIVE",
        "reason": "",
        "changed_at": None,
    }


def get_execution_state(conn, strategy_id: str) -> dict:
    """Read a state row without creating one for an ACTIVE default."""
    default = default_execution_state(strategy_id)
    try:
        row = conn.execute(
            "SELECT strategy_id, state, reason, changed_at "
            "FROM execution_state WHERE strategy_id=?",
            (strategy_id,),
        ).fetchone()
    except Exception as exc:
        # Read-only access to a pre-migration ledger must remain safe.  Avoid
        # hiding unrelated SQLite errors such as a locked or corrupt database.
        if "no such table: execution_state" not in str(exc):
            raise
        row = None
    return dict(row) if row is not None else default


def set_execution_state(
    conn,
    strategy_id: str,
    state: str,
    reason: str,
    changed_at: str | None = None,
) -> dict:
    """Validate and atomically upsert a strategy's durable operating state."""
    _require_strategy_id(strategy_id)
    if state not in VALID_EXECUTION_STATES:
        raise ExecutionStateError("state must be ACTIVE or PAUSED")
    if not isinstance(reason, str):
        raise ExecutionStateError("reason must be a string")
    reason = reason.strip()
    if state == "PAUSED" and not reason:
        raise ExecutionStateError("PAUSED execution state requires a reason")
    if changed_at is None:
        changed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    if not isinstance(changed_at, str) or not changed_at.strip():
        raise ExecutionStateError("changed_at must be a nonempty string")
    conn.execute(
        """INSERT INTO execution_state (strategy_id, state, reason, changed_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(strategy_id) DO UPDATE SET
             state=excluded.state,
             reason=excluded.reason,
             changed_at=excluded.changed_at""",
        (strategy_id, state, reason, changed_at),
    )
    return {
        "strategy_id": strategy_id,
        "state": state,
        "reason": reason,
        "changed_at": changed_at,
    }


def _require_strategy_id(strategy_id: str) -> None:
    if not isinstance(strategy_id, str) or not strategy_id.strip():
        raise ExecutionStateError("strategy_id must be a nonempty string")
