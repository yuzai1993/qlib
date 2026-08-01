"""Safety and metadata tests for the CSI1000 publish entry point."""

from types import SimpleNamespace

import pytest

from live_trading.scripts import run_publish_signals as publish


def _config():
    return {
        "live": {
            "broker_environment": "SIMULATION",
            "default_mode": "SIMULATE",
        }
    }


def test_account_resolution_uses_simulation_specific_environment(monkeypatch):
    monkeypatch.setenv("QMT_SIM_ACCOUNT_ID", "sim-123")
    monkeypatch.setenv("QMT_ACCOUNT_ID", "real-should-not-be-read")

    assert publish.resolve_account_id(_config()) == "sim-123"


def test_account_resolution_never_falls_back_to_real_account_variable(monkeypatch):
    monkeypatch.delenv("QMT_SIM_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("QMT_ACCOUNT_ID", "real-should-not-be-read")

    with pytest.raises(SystemExit, match="QMT_SIM_ACCOUNT_ID"):
        publish.resolve_account_id(_config())


def test_account_resolution_refuses_non_simulation_configuration(monkeypatch):
    monkeypatch.setenv("QMT_SIM_ACCOUNT_ID", "sim-123")
    config = _config()
    config["live"]["broker_environment"] = "REAL"

    with pytest.raises(SystemExit, match="SIMULATION"):
        publish.resolve_account_id(config)


def test_live_protocol_mode_still_requires_explicit_process_confirmation(monkeypatch):
    args = SimpleNamespace(mode="LIVE")
    monkeypatch.delenv("LIVE_TRADING_CONFIRM", raising=False)

    with pytest.raises(SystemExit, match="LIVE_TRADING_CONFIRM"):
        publish.resolve_mode(args, _config())


def test_strategy_positions_preserve_opening_trade_date():
    positions = publish.to_strategy_positions({
        "600000.SH": {
            "shares": 300,
            "avg_cost": 10.5,
            "opened_trade_date": "2026-07-01",
        }
    })

    assert positions == {
        "SH600000": {
            "shares": 300,
            "cost_price": 10.5,
            "opened_trade_date": "2026-07-01",
        }
    }


def test_signal_date_requires_the_requested_trade_date_to_be_open():
    calendar = ["2026-07-31", "2026-08-03"]

    signal_date, dates = publish.resolve_signal_calendar(
        calendar, "2026-08-03",
    )

    assert signal_date == "2026-07-31"
    assert dates == ["2026-07-31"]

    with pytest.raises(SystemExit, match="not a trading day"):
        publish.resolve_signal_calendar(calendar, "2026-08-02")
