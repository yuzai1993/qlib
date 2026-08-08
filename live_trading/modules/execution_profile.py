"""Mac-side execution profiles shared by signal production and QMT execution."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionProfile:
    """Immutable protocol details for one broker execution session."""

    name: str
    signal_price_type: str
    qmt_price_type: int
    submit_after: str
    cancel_at: str
    finalize_at: str
    snapshot_after: str
    authorization_prefix: str


_EXECUTION_PROFILES = {
    "CLOSE_AUCTION": ExecutionProfile(
        name="CLOSE_AUCTION",
        signal_price_type="CLOSE_AUCTION_LIMIT",
        qmt_price_type=11,
        submit_after="14:57:05",
        cancel_at="15:00:05",
        finalize_at="15:00:30",
        snapshot_after="15:01:00",
        authorization_prefix="LIVE_OK_",
    ),
    "AFTER_HOURS_FIXED_PRICE": ExecutionProfile(
        name="AFTER_HOURS_FIXED_PRICE",
        signal_price_type="AFTER_HOURS_CLOSE",
        qmt_price_type=49,
        submit_after="15:05:00",
        cancel_at="15:28:00",
        finalize_at="15:30:00",
        snapshot_after="15:31:00",
        authorization_prefix="PR49_LIVE_OK_",
    ),
}


def get_execution_profile(name: str) -> ExecutionProfile:
    """Return the named execution profile, rejecting unknown broker sessions."""
    try:
        return _EXECUTION_PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown execution profile: {name!r}") from exc
