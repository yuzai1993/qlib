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


def test_real_account_resolution_uses_real_specific_environment(monkeypatch):
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    monkeypatch.setenv("QMT_SIM_ACCOUNT_ID", "sim-123")
    config = _config()
    config["live"]["broker_environment"] = "REAL"
    config["live"]["allow_real_money"] = True

    assert publish.resolve_account_id(config) == "8890116049"


def test_real_account_resolution_requires_real_specific_environment(monkeypatch):
    monkeypatch.delenv("QMT_REAL_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("QMT_SIM_ACCOUNT_ID", "sim-123")
    config = _config()
    config["live"].update(
        broker_environment="REAL", allow_real_money=True,
    )

    with pytest.raises(SystemExit, match="QMT_REAL_ACCOUNT_ID"):
        publish.resolve_account_id(config)


def test_live_protocol_mode_still_requires_explicit_process_confirmation(monkeypatch):
    args = SimpleNamespace(mode="LIVE")
    monkeypatch.delenv("LIVE_TRADING_CONFIRM", raising=False)

    with pytest.raises(SystemExit, match="LIVE_TRADING_CONFIRM"):
        publish.resolve_mode(args, _config())


def test_real_account_cannot_publish_simulate_mode():
    args = SimpleNamespace(mode="SIMULATE")
    config = _config()
    config["live"].update(
        broker_environment="REAL", allow_real_money=True,
    )

    with pytest.raises(SystemExit, match="REAL.*LIVE"):
        publish.resolve_mode(args, config)


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


def test_signal_date_allows_the_next_session_beyond_local_qlib_calendar():
    calendar = ["2026-07-31", "2026-08-03"]

    signal_date, dates = publish.resolve_signal_calendar(
        calendar, "2026-08-04",
    )

    assert signal_date == "2026-08-03"
    assert dates == ["2026-07-31", "2026-08-03"]


def test_signal_date_requires_target_to_be_next_tushare_open_day():
    calendar = ["2026-07-31", "2026-08-03"]

    with pytest.raises(SystemExit, match="next open trading day"):
        publish.resolve_signal_calendar(
            calendar,
            "2026-08-05",
            next_open_resolver=lambda signal_date: "2026-08-04",
        )


def test_account_value_uses_adjustment_without_reducing_spendable_cash():
    positions = {
        "SH600000": {"shares": 100, "cost_price": 10.0},
    }

    total = publish.calculate_account_value(
        cash=9_949_714.06,
        positions=positions,
        prices={"SH600000": 10.0},
        value_adjustment=-681_126.98,
    )

    assert total == pytest.approx(9_269_587.08)
