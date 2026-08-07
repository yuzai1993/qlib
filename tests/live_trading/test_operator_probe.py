"""Immutable, operator-created prType=49 probe batches."""

import dataclasses
import json
from types import SimpleNamespace
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.operator_probe import (
    OperatorProbeRequest,
    build_operator_order,
    publish_operator_probe,
)
from live_trading.modules.signal_publisher import SignalPublisher
from live_trading.modules.signal_schema import SchemaError


TRADE_DATE = "2026-08-10"
STOCK_CODE = "600000.SH"
STRATEGY_ID = "csi1000_pr49_one_lot_probe"


def _config(tmp_path):
    return {
        "_config_id": "csi1000_pr49_one_lot_probe",
        "live": {
            "kind": "OPERATOR_PROBE",
            "strategy_id": STRATEGY_ID,
            "account_type": "STOCK",
            "broker_environment": "REAL",
            "allow_real_money": True,
            "default_mode": "LIVE",
            "execution_session": "AFTER_HOURS_FIXED_PRICE",
            "bridge_root": str(tmp_path / "pr49_probe"),
        },
    }


def _request(**changes):
    data = {
        "config_id": "csi1000_pr49_one_lot_probe",
        "trade_date": TRADE_DATE,
        "stock_code": STOCK_CODE,
        "side": "SELL",
        "quantity": 100,
        "reason": "operator_sell_probe",
    }
    data.update(changes)
    return OperatorProbeRequest(**data)


def _save_snapshot(recorder, *, trade_date=TRADE_DATE, shares=100,
                   can_use_volume=100, stock_code=STOCK_CODE):
    batch_id = f"{trade_date.replace('-', '')}_snapshot_001"
    recorder.record_batch(batch_id, trade_date, "LIVE", 0)
    recorder.save_broker_snapshot(
        batch_id,
        {"account_id": "8890116049"},
        [{
            "stock_code": stock_code,
            "shares": shares,
            "can_use_volume": can_use_volume,
            "avg_cost": 10.0,
            "market_value": shares * 10.0,
        }],
    )


@pytest.fixture
def recorder(tmp_path):
    return LiveRecorder(str(tmp_path / "live.db"))


def test_request_is_frozen():
    request = _request()

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.quantity = 200


@pytest.mark.parametrize("quantity", [0, 200, 150])
def test_probe_requires_exactly_one_lot(recorder, tmp_path, quantity):
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)

    with pytest.raises(SchemaError, match="exactly 100"):
        build_operator_order(_request(quantity=quantity), _config(tmp_path), recorder, TRADE_DATE)


def test_probe_rejects_unknown_stock_code(recorder, tmp_path):
    _save_snapshot(recorder)

    with pytest.raises(SchemaError, match="stock_code"):
        build_operator_order(_request(stock_code="not-a-stock"), _config(tmp_path), recorder, TRADE_DATE)


def test_sell_requires_the_live_ledger_holding(recorder, tmp_path):
    _save_snapshot(recorder)

    with pytest.raises(SchemaError, match="ledger"):
        build_operator_order(_request(), _config(tmp_path), recorder, TRADE_DATE)


def test_sell_requires_latest_broker_available_volume(recorder, tmp_path):
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder, can_use_volume=0)

    with pytest.raises(SchemaError, match="available"):
        build_operator_order(_request(), _config(tmp_path), recorder, TRADE_DATE)


def test_buy_rejects_a_held_ledger_stock(recorder, tmp_path):
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)

    with pytest.raises(SchemaError, match="already held"):
        build_operator_order(_request(side="BUY"), _config(tmp_path), recorder, TRADE_DATE)


def test_probe_requires_a_current_broker_snapshot(recorder, tmp_path):
    recorder.upsert_position(STOCK_CODE, 100, 10.0)

    with pytest.raises(SchemaError, match="snapshot"):
        build_operator_order(_request(), _config(tmp_path), recorder, TRADE_DATE)


def test_probe_rejects_a_stale_broker_snapshot(recorder, tmp_path):
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder, trade_date="2026-08-09")

    with pytest.raises(SchemaError, match="snapshot"):
        build_operator_order(_request(), _config(tmp_path), recorder, TRADE_DATE)


def test_probe_requires_hyphenated_iso_trade_date(recorder, tmp_path):
    with pytest.raises(SchemaError, match="YYYY-MM-DD"):
        build_operator_order(
            _request(trade_date="20260810"), _config(tmp_path), recorder,
            "20260810",
        )


def test_probe_rejects_a_non_probe_strategy_config(recorder, tmp_path):
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)
    config = _config(tmp_path)
    config["live"]["strategy_id"] = "another_real_strategy"

    with pytest.raises(SchemaError, match="strategy_id"):
        build_operator_order(_request(), config, recorder, TRADE_DATE)


def test_builds_a_one_lot_sell_for_the_fixed_price_profile(recorder, tmp_path):
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)

    order = build_operator_order(_request(), _config(tmp_path), recorder, TRADE_DATE)

    assert order.batch_id == "20260810_csi1000_pr49_one_lot_probe_900"
    assert order.client_order_id == "20260810900001S"
    assert order.side == "SELL"
    assert order.quantity == 100
    assert order.reason == "operator_sell_probe"
    assert order.price_type == "AFTER_HOURS_CLOSE"


def test_publish_records_plan_before_exposing_batch(recorder, tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)
    request = _request()
    config = _config(tmp_path)

    class InspectingPublisher:
        def ensure_publishable(self, header, orders):
            assert recorder.get_batch(header.batch_id) is None
            return False

        def publish(self, header, orders):
            batch = recorder.get_batch(header.batch_id)
            assert batch["planned_orders"] == 1
            assert recorder.get_orders(header.batch_id)[0]["quantity"] == 100
            return tmp_path / "published.jsonl"

    assert publish_operator_probe(
        request, config, recorder, InspectingPublisher(), "8890116049",
    ) == tmp_path / "published.jsonl"


def test_identical_publish_retry_is_idempotent_but_conflict_is_rejected(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)
    request = _request()
    config = _config(tmp_path)
    publisher = SignalPublisher(tmp_path / "pr49_probe")

    path = publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )
    assert publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    ) == path

    with pytest.raises(SchemaError, match="conflicts"):
        publish_operator_probe(
            _request(reason="different operator reason"),
            config, recorder, publisher, "8890116049",
        )


def test_durable_retry_does_not_recheck_mutable_sell_eligibility(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder, can_use_volume=100)
    request = _request()
    config = _config(tmp_path)
    publisher = SignalPublisher(tmp_path / "pr49_probe")

    path = publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )
    _save_snapshot(recorder, can_use_volume=0)

    assert publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    ) == path


def test_publish_requires_explicit_real_confirmation(recorder, tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_TRADING_CONFIRM", raising=False)
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)

    with pytest.raises(SchemaError, match="LIVE_TRADING_CONFIRM"):
        publish_operator_probe(
            _request(), _config(tmp_path), recorder,
            SignalPublisher(tmp_path / "pr49_probe"), "8890116049",
        )

    assert recorder.get_batch(
        "20260810_csi1000_pr49_one_lot_probe_900"
    ) is None


def test_cli_without_publish_only_emits_a_preview(monkeypatch, capsys, tmp_path):
    from live_trading.scripts import run_operator_probe

    header = object()
    order = object()
    monkeypatch.setattr(
        run_operator_probe,
        "parse_args",
        lambda: SimpleNamespace(
            config="csi1000_pr49_one_lot_probe", trade_date=TRADE_DATE,
            stock_code=STOCK_CODE, side="SELL", quantity=100,
            reason="operator_sell_probe", publish=False,
        ),
    )
    monkeypatch.setattr(run_operator_probe, "load_live_config", lambda *args: _config(tmp_path))
    monkeypatch.setattr(run_operator_probe, "resolve_account_id", lambda config: "8890116049")
    monkeypatch.setattr(run_operator_probe, "_recorder", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        run_operator_probe, "preview_operator_probe", lambda *args: (
            SimpleNamespace(
                checksum="sha256:preview",
                to_json_line=lambda: '{"type":"batch_header"}',
            ),
            SimpleNamespace(to_json_line=lambda: '{"type":"order"}'),
        ),
    )
    monkeypatch.setattr(
        run_operator_probe, "publish_operator_probe",
        lambda *args: pytest.fail("--publish was not supplied"),
    )

    run_operator_probe.main()

    assert json.loads(capsys.readouterr().out)["dry_run"] is True


def test_cli_preview_does_not_create_an_inbox_or_mutate_the_ledger(
    monkeypatch, capsys, tmp_path,
):
    from live_trading.scripts import run_operator_probe

    db_path = tmp_path / "live.db"
    writable = LiveRecorder(str(db_path))
    writable.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(writable)
    before = db_path.read_bytes()
    config = _config(tmp_path)
    config["storage"] = {"db_path": str(db_path)}
    config["live"]["bridge_root"] = str(
        tmp_path / "unmounted_probe_root" / "pr49_probe"
    )
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    monkeypatch.setattr(
        run_operator_probe,
        "parse_args",
        lambda: SimpleNamespace(
            config="csi1000_pr49_one_lot_probe", trade_date=TRADE_DATE,
            stock_code=STOCK_CODE, side="SELL", quantity=100,
            reason="operator_sell_probe", publish=False,
        ),
    )
    monkeypatch.setattr(run_operator_probe, "load_live_config", lambda *args: config)

    run_operator_probe.main()

    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert db_path.read_bytes() == before
    assert not Path(config["live"]["bridge_root"]).exists()


def test_broker_detail_accessor_preserves_latest_snapshot_metadata(recorder):
    _save_snapshot(recorder, shares=300, can_use_volume=200)

    assert recorder.get_broker_position_details(TRADE_DATE) == {
        STOCK_CODE: {
            "shares": 300,
            "can_use_volume": 200,
            "avg_cost": 10.0,
            "market_value": 3000.0,
        }
    }


def test_broker_detail_accessor_refuses_missing_snapshot(recorder):
    with pytest.raises(SchemaError, match="snapshot"):
        recorder.get_broker_position_details(TRADE_DATE)
