"""Safety and metadata tests for the CSI1000 publish entry point."""

import json
from types import SimpleNamespace

import pytest

from live_trading.scripts import run_publish_signals as publish
from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.signal_schema import compute_checksum
from live_trading.scripts.override_main_signal import build_override


def _config():
    return {
        "live": {
            "broker_environment": "SIMULATION",
            "default_mode": "SIMULATE",
        }
    }


def test_account_resolution_is_qmt_managed(monkeypatch):
    monkeypatch.delenv("QMT_ACCOUNT_ID", raising=False)
    assert publish.resolve_account_id(_config()) == "QMT_MANAGED"


def test_account_resolution_accepts_operator_selected_account(monkeypatch):
    monkeypatch.setenv("QMT_ACCOUNT_ID", "operator-selected")
    assert publish.resolve_account_id(_config()) == "operator-selected"


def test_configured_account_id_is_preserved():
    config = _config()
    config["live"]["account_id"] = "8890116049"
    assert publish.resolve_account_id(config) == "8890116049"

def test_live_protocol_mode_needs_no_process_confirmation(monkeypatch):
    args = SimpleNamespace(mode="LIVE")
    monkeypatch.delenv("LIVE_TRADING_CONFIRM", raising=False)
    assert publish.resolve_mode(args, _config()) == "LIVE"


def test_qmt_account_selection_does_not_change_publisher_mode():
    args = SimpleNamespace(mode="SIMULATE")
    config = _config()
    config["live"].update(
        broker_environment="REAL", allow_real_money=True,
    )

    assert publish.resolve_mode(args, config) == "SIMULATE"


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


def test_generic_publisher_rejects_operator_probe_before_parity(monkeypatch):
    monkeypatch.setattr(
        publish, "parse_args",
        lambda: SimpleNamespace(config="csi1000_pr49_one_lot_probe"),
    )
    monkeypatch.setattr(
        publish, "load_live_config",
        lambda *args: {"live": {"kind": "OPERATOR_PROBE"}},
    )
    monkeypatch.setattr(
        publish, "validate_configured_backtest",
        lambda *args: pytest.fail("operator probe reached parity validation"),
    )

    with pytest.raises(SystemExit, match="STRATEGY"):
        publish.main()


def test_generic_publisher_binds_planner_to_selected_execution_profile():
    config = {
        "live": {
            "execution_session": "CLOSE_AUCTION",
            "max_orders_per_day": 40,
        },
        "exchange": {"trade_unit": 100},
    }

    planner = publish.build_order_planner(config)

    assert planner.signal_price_type == "CLOSE_AUCTION_LIMIT"


def test_paused_strategy_does_not_block_direct_publication(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    recorder.set_execution_state(
        "main", "PAUSED", "operator verification pending", "2026-08-10T20:00:00+08:00",
    )

    publish.ensure_execution_is_active(recorder, "main", "LIVE")
    publish.ensure_execution_is_active(recorder, "main", "SIMULATE")


def test_unknown_persisted_state_does_not_block_direct_publication(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    with recorder._conn() as conn:
        conn.execute(
            "INSERT INTO execution_state VALUES (?,?,?,?)",
            ("main", "UNKNOWN", "manual corruption", "2026-08-10T20:00:00+08:00"),
        )

    publish.ensure_execution_is_active(recorder, "main", "LIVE")


def test_audit_preview_is_atomic_complete_and_does_not_need_a_batch(tmp_path):
    destination = tmp_path / "previews" / "signal_2026-08-11.json"
    order = SimpleNamespace(
        side="BUY", stock_code="600000.SH", quantity=0, max_quantity=100,
        target_value=1_000.0, limit_price=10.0, client_order_id="order-1",
        batch_id="20260811_main_001", to_json_line=lambda: '{"side":"BUY"}',
    )

    publish.write_audit_preview(
        destination, strategy_id="main", signal_date="2026-08-10",
        trade_date="2026-08-11", current_positions={"SH600000": {"shares": 100}},
        orders=[order], generated_at="2026-08-10T20:00:00+08:00",
    )

    assert not list(destination.parent.glob("*.tmp"))
    preview = json.loads(destination.read_text(encoding="utf-8"))
    assert preview["strategy_id"] == "main"
    assert preview["signal_date"] == "2026-08-10"
    assert preview["trade_date"] == "2026-08-11"
    assert preview["current_positions"] == {"SH600000": {"shares": 100}}
    assert preview["order_count"] == 1
    assert preview["buy_count"] == 1
    assert preview["sell_count"] == 0


def test_manual_sell_override_preserves_source_and_is_sell_only(tmp_path):
    from live_trading.modules.signal_publisher import SignalPublisher
    from live_trading.modules.signal_schema import BatchHeader, SignalOrder

    root = tmp_path / "bridge"
    root.mkdir()
    source = BatchHeader(
        batch_id="20260810_main_001", strategy_id="main",
        trade_date="2026-08-10", signal_date="2026-08-07",
        account_id="QMT_MANAGED", account_type="STOCK",
        account_environment="SIMULATION", mode="LIVE",
        created_at="2026-08-07T20:00:00+08:00", order_count=1, checksum="",
    )
    original = SignalOrder(
        batch_id=source.batch_id, client_order_id="20260810001001B",
        stock_code="600000.SH", side="BUY", quantity=0,
        target_value=10000.0, price_type="CLOSE_AUCTION_LIMIT", limit_price=0.0,
        priority=20, instrument_qlib="SH600000", reason="topk_dropout",
    )
    source = source.__class__(**{**source.__dict__, "checksum": compute_checksum([original.to_json_line()])})
    SignalPublisher(root).publish(source, [original])
    header, orders = build_override(
        root, source.batch_id, "600000.SH", 100, "sell test", "operator", 2,
    )
    assert orders[0].side == "SELL"
    assert orders[0].quantity == 100
    assert "source=20260810_main_001" in orders[0].reason
    assert header.batch_id != source.batch_id


def test_main_paused_audit_preview_does_not_create_batch_or_inbox(
    monkeypatch, tmp_path,
):
    db_path = tmp_path / "live.db"
    recorder = LiveRecorder(str(db_path))
    recorder.set_execution_state(
        "strategy-main", "PAUSED", "operator verification pending",
        "2026-08-10T20:00:00+08:00",
    )
    preview_path = tmp_path / "logs" / "strategy-main" / "previews" / "signal_2026-08-11.json"
    config = {
        "storage": {"db_path": str(db_path)},
        "live": {
            "kind": "STRATEGY", "strategy_id": "strategy-main",
            "execution_session": "CLOSE_AUCTION", "bridge_root": str(tmp_path / "bridge"),
            "max_orders_per_day": 40,
        },
        "exchange": {"trade_unit": 100},
        "strategy": {"topk": 2},
        "account": {},
    }
    order = SimpleNamespace(
        side="BUY", stock_code="600000.SH", quantity=0, target_value=1_000.0,
        client_order_id="order-1", batch_id="20260811_strategy-main_001",
        to_json_line=lambda: '{"side":"BUY","stock_code":"600000.SH"}',
    )
    monkeypatch.setattr(
        publish, "parse_args", lambda: SimpleNamespace(
            config="config-alias", trade_date="2026-08-11", mode="LIVE", seq=1,
            dry_run=True, audit_preview=preview_path,
        ),
    )
    monkeypatch.setattr(publish, "load_live_config", lambda *_args: config)
    monkeypatch.setattr(publish, "validate_configured_backtest", lambda *_args: None)
    monkeypatch.setattr(publish, "get_execution_profile", lambda *_args: object())
    monkeypatch.setattr(publish, "resolve_mode", lambda *_args: "LIVE")
    monkeypatch.setattr(publish, "resolve_account_id", lambda *_args: "account")
    monkeypatch.setattr(
        publish, "get_signal_date_and_scores",
        lambda *_args: ("2026-08-10", [], ["2026-08-10"]),
    )
    monkeypatch.setattr(publish, "get_price_instruments", lambda *_args: [])
    monkeypatch.setattr(publish, "get_prev_close", lambda *_args: {})
    monkeypatch.setattr(
        publish, "build_order_planner",
        lambda *_args: SimpleNamespace(plan=lambda *_args, **_kwargs: [order]),
    )
    from live_trading.modules import order_manager

    monkeypatch.setattr(
        order_manager, "OrderManager",
        lambda *_args: SimpleNamespace(generate_orders=lambda *_args, **_kwargs: [{}]),
    )

    publish.main()

    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    assert preview["trade_date"] == "2026-08-11"
    assert preview["sell_count"] == 0
    assert recorder.list_batches() == []
    assert not (tmp_path / "bridge" / "inbox").exists()
