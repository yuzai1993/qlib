"""Safety and metadata tests for the CSI1000 publish entry point."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from live_trading.scripts import run_publish_signals as publish
from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.signal_schema import compute_checksum
from live_trading.scripts.override_main_signal import (
    build_override,
    replace_unclaimed_batch,
)

DAILY_ST = pd.DataFrame(
    {
        "symbol": ["SZ300029", "SZ300029"],
        "date": ["2026-04-24", "2026-06-18"],
        "name": ["*ST天龙", "天龙退"],
        "source": ["stock_st", "namechange"],
    }
)


def _config():
    return {
        "live": {
            "broker_environment": "SIMULATION",
            "default_mode": "SIMULATE",
        }
    }


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


def test_load_saved_prediction_scores_returns_series(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    recorder.save_predictions("2026-09-02", {"SZ003816": 2.5, "SH601998": 1.1})

    scores = publish.load_saved_prediction_scores(recorder, "2026-09-02")

    assert list(scores.index) == ["SZ003816", "SH601998"] or set(scores.index) == {
        "SZ003816", "SH601998",
    }
    assert scores["SZ003816"] == pytest.approx(2.5)
    assert scores["SH601998"] == pytest.approx(1.1)


def test_load_saved_prediction_scores_fails_closed_when_missing(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))

    with pytest.raises(SystemExit, match="no saved predictions"):
        publish.load_saved_prediction_scores(recorder, "2026-09-02")


def test_reuse_predictions_skips_model_inference(monkeypatch, tmp_path):
    db_path = tmp_path / "live.db"
    recorder = LiveRecorder(str(db_path))
    recorder.save_predictions("2026-09-02", {"SZ003816": 2.59})
    config = {
        "storage": {"db_path": str(db_path)},
        "live": {
            "kind": "STRATEGY", "strategy_id": "strategy-main",
            "execution_session": "CLOSE_AUCTION", "bridge_root": str(tmp_path / "bridge"),
            "max_orders_per_day": 40,
        },
        "exchange": {"trade_unit": 100},
        "strategy": {"class": "TopkDropoutStrategy", "topk": 1},
        "account": {},
    }
    order = SimpleNamespace(
        side="BUY", stock_code="003816.SZ", quantity=0, target_value=1_000.0,
        client_order_id="order-1", batch_id="20260903_strategy-main_003",
        to_json_line=lambda: '{"side":"BUY"}',
    )
    monkeypatch.setattr(
        publish, "parse_args", lambda: SimpleNamespace(
            config="config-alias", trade_date="2026-09-03", seq=3,
            dry_run=True, audit_preview=None, reuse_predictions=True,
        ),
    )
    monkeypatch.setattr(publish, "load_live_config", lambda *_args: config)
    monkeypatch.setattr(publish, "validate_configured_backtest", lambda *_args: None)
    monkeypatch.setattr(
        publish, "get_signal_date_and_scores",
        lambda *_args: pytest.fail("must not re-predict"),
    )
    monkeypatch.setattr(
        publish, "resolve_publish_calendar",
        lambda *_args: ("2026-09-02", ["2026-09-02"]),
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
    saved = []
    monkeypatch.setattr(
        LiveRecorder, "save_predictions",
        lambda self, *args, **kwargs: saved.append(args) or 0,
    )

    publish.main()

    assert saved == []


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


def test_replace_unclaimed_batch_archives_buy_and_publishes_held_sell(tmp_path):
    from live_trading.modules.signal_publisher import SignalPublisher
    from live_trading.modules.signal_schema import BatchHeader, SignalOrder

    root = tmp_path / "bridge"
    root.mkdir()
    db_path = tmp_path / "live.db"
    recorder = LiveRecorder(str(db_path))
    recorder.upsert_position("601326.SH", 100, 3.39, "2026-08-07")
    source = BatchHeader(
        batch_id="20260811_main_001", strategy_id="main",
        trade_date="2026-08-11", signal_date="2026-08-10",
        account_id="QMT_MANAGED", account_type="STOCK",
        account_environment="REAL", mode="LIVE",
        created_at="2026-08-10T20:00:00+08:00", order_count=1, checksum="",
    )
    buy = SignalOrder(
        batch_id=source.batch_id, client_order_id="20260811001001B",
        stock_code="600033.SH", side="BUY", quantity=0,
        target_value=10000.0, price_type="CLOSE_AUCTION_LIMIT",
        limit_price=0.0, priority=20, instrument_qlib="SH600033",
        reason="topk_dropout",
    )
    recorder.record_publish_plan(source, [buy])
    SignalPublisher(root).publish(source, [buy])

    path = replace_unclaimed_batch(
        root, db_path, source.batch_id, "601326.SH", 100,
        "sell verification", "operator", 900,
    )

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["trade_date"] == "2026-08-11"
    assert rows[1]["side"] == "SELL"
    assert rows[1]["quantity"] == 100
    assert rows[1]["stock_code"] == "601326.SH"
    assert rows[1]["instrument_qlib"] == "SH601326"
    assert rows[1]["price_type"] == "CLOSE_AUCTION_LIMIT"
    assert not (root / "inbox" / f"signal_{source.batch_id}.done").exists()
    assert (root / "archive" / "superseded" / f"signal_{source.batch_id}.done").is_file()
    replacement_id = rows[0]["batch_id"]
    assert recorder.get_batch(source.batch_id)["superseded_by"] == replacement_id


def test_replace_unclaimed_batch_rejects_insufficient_holding(tmp_path):
    root = tmp_path / "bridge"
    root.mkdir()
    db_path = tmp_path / "live.db"
    LiveRecorder(str(db_path)).upsert_position("601326.SH", 99, 3.39)

    with pytest.raises(SystemExit, match="available holding"):
        replace_unclaimed_batch(
            root, db_path, "20260811_main_001", "601326.SH", 100,
            "sell verification", "operator", 900,
        )


def test_replace_unclaimed_batch_rejects_processing_artifact(tmp_path):
    root = tmp_path / "bridge"
    (root / "processing").mkdir(parents=True)
    (root / "processing" / "signal_20260811_main_001.done").write_text("x")
    db_path = tmp_path / "live.db"
    LiveRecorder(str(db_path)).upsert_position("601326.SH", 100, 3.39)

    with pytest.raises(SystemExit, match="processing artifact"):
        replace_unclaimed_batch(
            root, db_path, "20260811_main_001", "601326.SH", 100,
            "sell verification", "operator", 900,
        )


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
        to_json_line=lambda: '{"side":"BUY","instrument":"600000.SH"}',
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
    monkeypatch.setattr(
        publish, "get_signal_date_and_scores",
        lambda *_args: ("2026-08-10", [], ["2026-08-10"]),
    )
    st_daily = tmp_path / "st_daily.csv"
    st_daily.write_text(
        "symbol,date,name,source\nSZ000001,2026-08-10,平安银行,stock_st\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("QLIB_ST_DAILY", str(st_daily))
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


def test_publish_nans_st_symbols_on_signal_date():
    import numpy as np

    scores = pd.Series([1.0, 2.0, 3.0], index=["SZ000001", "SZ300029", "SH600000"])
    out = publish.apply_st_daily(scores, DAILY_ST, "2026-04-24")
    assert np.isnan(out["SZ300029"])
    assert out["SZ000001"] == 1.0
    assert np.isnan(publish.apply_st_daily(scores, DAILY_ST, "2026-06-18")["SZ300029"])


def test_publish_refuses_stale_cache():
    scores = pd.Series([1.0], index=["SZ000001"])
    with pytest.raises(SystemExit, match="st_daily"):
        publish.apply_st_daily(scores, DAILY_ST, "2026-08-14")


def test_ladder_branch_persists_reconciled_state_before_deciding(tmp_path):
    """reconcile 结果必须落库，否则次日 settle 弹错层。"""
    from live_trading.modules.cohort_store import CohortState

    recorder = LiveRecorder(str(tmp_path / "l.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(
        CohortState(
            layers=(
                ("2026-08-19", {"SH600000": 100}),
                ("2026-08-20", {"SH600000": 200}),
            ),
            pending={},
        )
    )

    # 券商只有 100 股：最新层整单落空，reconcile 应把它削掉并写回
    state = publish.reconcile_cohort_state(
        recorder, broker_positions={"SH600000": 100}, horizon=5,
    )

    assert state.layers == (
        ("2026-08-19", {"SH600000": 100}),
        ("2026-08-20", {}),
    )
    assert recorder.load_cohort_state() == state


def test_ladder_branch_reports_absorbed_broker_excess(tmp_path):
    from live_trading.modules.cohort_store import CohortState

    recorder = LiveRecorder(str(tmp_path / "l.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(
        CohortState(layers=(("2026-08-19", {"SH600000": 400}),), pending={})
    )

    state = publish.reconcile_cohort_state(
        recorder, broker_positions={"SH600000": 520}, horizon=5,
    )

    assert state.layers == (("2026-08-19", {"SH600000": 520}),)


def test_audit_preview_estimates_netting_for_overlapping_names():
    rows = publish.netting_preview(
        orders=[
            {"direction": "SELL", "instrument": "SH600000", "target_shares": 600,
             "target_value": 0.0, "reason": "cohort_due"},
            {"direction": "BUY", "instrument": "SH600000",
             "target_value": 60_000.0, "reason": "cohort_layer"},
            {"direction": "BUY", "instrument": "SZ000001",
             "target_value": 60_000.0, "reason": "cohort_layer"},
        ],
        close_prices={"SH600000": 100.0, "SZ000001": 20.0},
        trade_unit=100,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["stock_code"] == "SH600000"
    assert row["S"] == 600
    assert row["V"] == 60_000.0
    assert row["B_est"] == 600          # floor(60000 / 100 / 100) * 100
    assert row["net_est"] == 0
    assert row["estimate"] is True


def test_netting_preview_skips_buys_without_a_matching_sell():
    rows = publish.netting_preview(
        orders=[
            {"direction": "BUY", "instrument": "SZ000001",
             "target_value": 60_000.0, "reason": "cohort_layer"},
        ],
        close_prices={"SZ000001": 20.0},
        trade_unit=100,
    )

    assert rows == []
