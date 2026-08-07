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
    bridge_root = tmp_path / "pr49_probe"
    bridge_root.mkdir(exist_ok=True)
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
            "bridge_root": str(bridge_root),
        },
    }


def _main_config(tmp_path):
    bridge_root = tmp_path / "main_bridge"
    bridge_root.mkdir(exist_ok=True)
    return {
        "_config_id": "csi1000_b6m_b2s_postclose_real",
        "live": {
            "strategy_id": "csi1000_b6m_b2s_postclose_real",
            "account_type": "STOCK",
            "broker_environment": "REAL",
            "allow_real_money": True,
            "default_mode": "LIVE",
            "execution_session": "CLOSE_AUCTION",
            "bridge_root": str(bridge_root),
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
    value = LiveRecorder(str(tmp_path / "live.db"))
    value.save_stock_names([{
        "stock_code": STOCK_CODE,
        "instrument": "SH600000",
        "name": "浦发银行",
    }])
    return value


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


def test_probe_rejects_well_formed_stock_outside_approved_universe(
    recorder, tmp_path,
):
    _save_snapshot(recorder)

    with pytest.raises(SchemaError, match="approved"):
        build_operator_order(
            _request(stock_code="999999.SH"), _config(tmp_path), recorder,
            TRADE_DATE,
        )


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


def test_buy_rejects_a_stock_held_only_in_latest_broker_snapshot(
    recorder, tmp_path,
):
    _save_snapshot(recorder, shares=100, can_use_volume=100)

    with pytest.raises(SchemaError, match="broker"):
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


def test_operator_tool_accepts_main_close_auction_config(recorder, tmp_path):
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)
    request = _request(config_id="csi1000_b6m_b2s_postclose_real")

    order = build_operator_order(request, _main_config(tmp_path), recorder, TRADE_DATE)

    assert order.price_type == "CLOSE_AUCTION_LIMIT"


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


def test_operator_buy_carries_immutable_one_lot_maximum(recorder, tmp_path):
    _save_snapshot(recorder, shares=0, can_use_volume=0)

    order = build_operator_order(
        _request(side="BUY"), _config(tmp_path), recorder, TRADE_DATE,
    )

    assert order.quantity == 0
    assert order.max_quantity == 100


def test_publish_persists_and_serializes_operator_buy_maximum(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    _save_snapshot(recorder, shares=0, can_use_volume=0)
    config = _config(tmp_path)
    request = _request(side="BUY")

    path = publish_operator_probe(
        request, config, recorder,
        SignalPublisher(config["live"]["bridge_root"]), "8890116049",
    )

    assert recorder.get_orders(
        "20260810_csi1000_pr49_one_lot_probe_900"
    )[0]["max_quantity"] == 100
    assert json.loads(path.read_text().splitlines()[1])["max_quantity"] == 100


def test_publish_records_plan_before_exposing_batch(recorder, tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)
    request = _request()
    config = _config(tmp_path)

    class InspectingPublisher:
        bridge_root = tmp_path / "pr49_probe"

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


def test_publish_uses_record_plan_as_the_atomic_pre_publish_gate(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)

    class InterleavingPublisher:
        bridge_root = tmp_path / "pr49_probe"

        def ensure_publishable(self, header, orders):
            recorder.record_publish_plan(
                header, [dataclasses.replace(orders[0], reason="other")],
            )
            return False

        def publish(self, header, orders):
            pytest.fail("conflicting plan must not reach SMB publish")

    with pytest.raises(SchemaError, match="conflicts with durable plan"):
        publish_operator_probe(
            _request(), _config(tmp_path), recorder,
            InterleavingPublisher(), "8890116049",
        )


def test_publish_rejects_mismatched_publisher_root_before_db_mutation(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)
    other_root = tmp_path / "other_pr49_probe"
    other_root.mkdir()

    with pytest.raises(SchemaError, match="publisher root"):
        publish_operator_probe(
            _request(), _config(tmp_path), recorder,
            SignalPublisher(other_root), "8890116049",
        )

    assert recorder.get_batch("20260810_csi1000_pr49_one_lot_probe_900") is None


def test_publish_rejects_missing_or_symlinked_configured_root(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)
    missing = _config(tmp_path)
    missing["live"]["bridge_root"] = str(tmp_path / "missing" / "pr49_probe")

    with pytest.raises(SchemaError, match="bridge root"):
        publish_operator_probe(
            _request(), missing, recorder,
            SignalPublisher(tmp_path / "missing" / "pr49_probe"), "8890116049",
        )

    target = tmp_path / "target" / "pr49_probe"
    target.mkdir(parents=True)
    linked = tmp_path / "linked" / "pr49_probe"
    linked.parent.mkdir()
    linked.symlink_to(target, target_is_directory=True)
    linked_config = _config(tmp_path)
    linked_config["live"]["bridge_root"] = str(linked)

    with pytest.raises(SchemaError, match="symlink"):
        publish_operator_probe(
            _request(), linked_config, recorder,
            SignalPublisher(target), "8890116049",
        )


def test_publish_rejects_unwritable_root_before_durable_plan(
    recorder, tmp_path, monkeypatch,
):
    from live_trading.modules import operator_probe

    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    monkeypatch.setattr(operator_probe.os, "access", lambda *args: False)
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)
    config = _config(tmp_path)

    with pytest.raises(SchemaError, match="not writable"):
        publish_operator_probe(
            _request(), config, recorder,
            SignalPublisher(config["live"]["bridge_root"]), "8890116049",
        )

    assert recorder.get_batch("20260810_csi1000_pr49_one_lot_probe_900") is None


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
    writable.save_stock_names([{
        "stock_code": STOCK_CODE,
        "instrument": "SH600000",
        "name": "浦发银行",
    }])
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
