"""Immutable, operator-created prType=49 probe batches."""

import dataclasses
import json
import threading
from types import SimpleNamespace
import sys
from pathlib import Path

import pytest
from filelock import FileLock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.signal_schema import BatchHeader, FillEvent
from live_trading.modules import operator_probe
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
        "eligibility_confirmed": False,
    }
    data.update(changes)
    return OperatorProbeRequest(**data)


def _record_probe_buy_plan(recorder, tmp_path, monkeypatch, *,
                           trade_date="2026-08-07", stock_code=STOCK_CODE):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    _save_snapshot(
        recorder, trade_date=trade_date, shares=0, can_use_volume=0,
        stock_code=stock_code,
    )
    config = _config(tmp_path)
    request = _request(
        trade_date=trade_date, stock_code=stock_code, side="BUY",
        reason="operator_buy_probe", eligibility_confirmed=True,
    )
    publish_operator_probe(
        request, config, recorder,
        SignalPublisher(config["live"]["bridge_root"]), "8890116049",
    )
    return request


def _apply_probe_buy_fill(recorder, request, *, filled_qty=100,
                          status="FILLED", with_snapshot=True):
    batch_id = (
        f"{request.trade_date.replace('-', '')}_{STRATEGY_ID}_900"
    )
    recorder.apply_fill(FillEvent.from_dict({
        "type": "fill_event",
        "batch_id": batch_id,
        "client_order_id": (
            f"{request.trade_date.replace('-', '')}900001B"
        ),
        "mode": "LIVE",
        "stock_code": request.stock_code,
        "side": "BUY",
        "status": status,
        "requested_qty": 100,
        "filled_qty": filled_qty,
        "avg_price": 10.0 if filled_qty else 0.0,
        "qmt_order_id": "probe-buy-1",
        "message": "",
        "ts": f"{request.trade_date}T15:06:00+08:00",
    }))
    if with_snapshot:
        recorder.save_broker_snapshot(
            batch_id,
            {"account_id": "8890116049"},
            [] if filled_qty == 0 else [{
                "stock_code": request.stock_code,
                "shares": filled_qty,
                "can_use_volume": 0,
                "avg_cost": 10.0,
                "market_value": filled_qty * 10.0,
            }],
        )


def _prepare_probe_sell(recorder, tmp_path, monkeypatch):
    monkeypatch.setattr(
        operator_probe, "_qlib_trade_dates",
        lambda start, end: ["2026-08-07", "2026-08-10"],
    )
    buy = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    _apply_probe_buy_fill(recorder, buy)


def _remove_probe_inbox_pair(tmp_path, batch_id):
    inbox = tmp_path / "pr49_probe" / "inbox"
    (inbox / f"signal_{batch_id}.jsonl").unlink()
    (inbox / f"signal_{batch_id}.done").unlink()


def _record_real_snapshot_batch(recorder, batch_id, trade_date):
    recorder.record_publish_plan(BatchHeader(
        batch_id=batch_id,
        strategy_id="operator-probe-snapshot-test",
        trade_date=trade_date,
        signal_date=trade_date,
        account_id="8890116049",
        account_type="STOCK",
        account_environment="REAL",
        mode="LIVE",
        created_at=f"{trade_date}T00:00:00+08:00",
        order_count=0,
        checksum="",
    ), [])


def _save_snapshot(recorder, *, trade_date=TRADE_DATE, shares=100,
                   can_use_volume=100, stock_code=STOCK_CODE,
                   batch_sequence=0, with_account=True):
    batch_id = (
        f"{trade_date.replace('-', '')}_{batch_sequence:03d}_snapshot"
    )
    _record_real_snapshot_batch(recorder, batch_id, trade_date)
    recorder.save_broker_snapshot(
        batch_id,
        {"account_id": "8890116049"} if with_account else None,
        [] if shares == 0 else [{
            "stock_code": stock_code,
            "shares": shares,
            "can_use_volume": can_use_volume,
            "avg_cost": 10.0,
            "market_value": shares * 10.0,
        }],
    )


def test_probe_buy_requires_latest_matched_account_snapshot_before_publish(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    _save_snapshot(
        recorder, shares=0, can_use_volume=0, batch_sequence=0,
    )
    _save_snapshot(
        recorder, shares=0, can_use_volume=0, batch_sequence=1,
        with_account=False,
    )
    config = _config(tmp_path)
    request = _request(side="BUY", eligibility_confirmed=True)
    probe_batch = "20260810_csi1000_pr49_one_lot_probe_900"

    with pytest.raises(SchemaError, match="ACCOUNT evidence"):
        publish_operator_probe(
            request,
            config,
            recorder,
            SignalPublisher(config["live"]["bridge_root"]),
            "8890116049",
        )

    assert recorder.get_batch(probe_batch) is None
    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID) is None
    assert not list(
        (tmp_path / "pr49_probe" / "inbox").glob(f"*{probe_batch}*")
    )

    _save_snapshot(
        recorder, shares=0, can_use_volume=0, batch_sequence=2,
    )
    path = publish_operator_probe(
        request,
        config,
        recorder,
        SignalPublisher(config["live"]["bridge_root"]),
        "8890116049",
    )
    assert path.is_file()
    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "BUY_PLANNED"


def test_probe_sell_requires_latest_matched_account_snapshot_before_publish(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        operator_probe, "_qlib_trade_dates",
        lambda start, end: ["2026-08-07", "2026-08-10"],
    )
    _prepare_probe_sell(recorder, tmp_path, monkeypatch)
    _save_snapshot(
        recorder, trade_date="2026-08-10", shares=100,
        can_use_volume=100, batch_sequence=0,
    )
    _save_snapshot(
        recorder, trade_date="2026-08-10", shares=100,
        can_use_volume=100, batch_sequence=1, with_account=False,
    )
    config = _config(tmp_path)
    request = _request(trade_date="2026-08-10")
    probe_batch = "20260810_csi1000_pr49_one_lot_probe_900"

    with pytest.raises(SchemaError, match="ACCOUNT evidence"):
        publish_operator_probe(
            request,
            config,
            recorder,
            SignalPublisher(config["live"]["bridge_root"]),
            "8890116049",
        )

    assert recorder.get_batch(probe_batch) is None
    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "BUY_FILLED"
    assert not list(
        (tmp_path / "pr49_probe" / "inbox").glob(f"*{probe_batch}*")
    )

    _save_snapshot(
        recorder, trade_date="2026-08-10", shares=100,
        can_use_volume=100, batch_sequence=2,
    )
    path = publish_operator_probe(
        request,
        config,
        recorder,
        SignalPublisher(config["live"]["bridge_root"]),
        "8890116049",
    )
    assert path.is_file()
    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "SELL_PLANNED"


def test_probe_publish_serializes_snapshot_import_across_authorization(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    _save_snapshot(
        recorder, shares=0, can_use_volume=0, batch_sequence=0,
    )
    config = _config(tmp_path)
    request = _request(side="BUY", eligibility_confirmed=True)

    class InterleavingPublisher(SignalPublisher):
        def __init__(self, bridge_root):
            super().__init__(bridge_root)
            self.import_started = threading.Event()
            self.import_finished = threading.Event()
            self.import_errors = []
            self.import_thread = None

        def ensure_publishable(self, header, orders):
            if self.import_thread is not None:
                return super().ensure_publishable(header, orders)

            def import_positions_only():
                self.import_started.set()
                try:
                    _save_snapshot(
                        recorder,
                        shares=0,
                        can_use_volume=0,
                        batch_sequence=1,
                        with_account=False,
                    )
                except BaseException as exc:  # surfaced in the test thread
                    self.import_errors.append(exc)
                finally:
                    self.import_finished.set()

            self.import_thread = threading.Thread(
                target=import_positions_only,
            )
            self.import_thread.start()
            assert self.import_started.wait(2)
            assert not self.import_finished.wait(0.2)
            return super().ensure_publishable(header, orders)

    publisher = InterleavingPublisher(config["live"]["bridge_root"])
    path = publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )
    publisher.import_thread.join(timeout=5)

    assert not publisher.import_thread.is_alive()
    assert publisher.import_errors == []
    assert path.is_file()
    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "BUY_PLANNED"


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
        build_operator_order(
            _request(side="BUY", eligibility_confirmed=True),
            _config(tmp_path), recorder, TRADE_DATE,
        )


def test_buy_rejects_a_stock_held_only_in_latest_broker_snapshot(
    recorder, tmp_path,
):
    _save_snapshot(recorder, shares=100, can_use_volume=100)

    with pytest.raises(SchemaError, match="broker"):
        build_operator_order(
            _request(side="BUY", eligibility_confirmed=True),
            _config(tmp_path), recorder, TRADE_DATE,
        )


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


def _prepare_main_sell_publish(recorder, tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    recorder.upsert_position(STOCK_CODE, 100, 10.0)
    _save_snapshot(recorder)
    config = _main_config(tmp_path)
    request = _request(
        config_id="csi1000_b6m_b2s_postclose_real",
        side="SELL",
    )
    return config, request, SignalPublisher(config["live"]["bridge_root"])


def _write_main_authorization_marker(tmp_path, relative_path):
    marker = tmp_path / "main_bridge" / relative_path
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")
    return marker


def test_main_sell_publish_requires_durable_paused_state(
    recorder, tmp_path, monkeypatch,
):
    config, request, publisher = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )

    with pytest.raises(SchemaError, match="PAUSED"):
        publish_operator_probe(
            request, config, recorder, publisher, "8890116049",
        )

    assert recorder.get_batch(
        "20260810_csi1000_b6m_b2s_postclose_real_900"
    ) is None
    assert not list((tmp_path / "main_bridge" / "inbox").glob("*"))


def test_main_sell_publish_rejects_another_active_same_day_live_batch(
    recorder, tmp_path, monkeypatch,
):
    config, request, publisher = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    recorder.set_execution_state(
        config["live"]["strategy_id"], "PAUSED",
        "exclusive operator sell", "2026-08-10T12:00:00+08:00",
    )
    recorder.record_publish_plan(BatchHeader(
        batch_id="20260810_csi1000_b6m_b2s_postclose_real_001",
        strategy_id=config["live"]["strategy_id"],
        trade_date=TRADE_DATE,
        signal_date="2026-08-07",
        account_id="8890116049",
        account_type="STOCK",
        account_environment="REAL",
        mode="LIVE",
        created_at="2026-08-09T21:30:00+08:00",
        order_count=0,
        checksum="",
    ), [])

    with pytest.raises(SchemaError, match="same-day LIVE batch"):
        publish_operator_probe(
            request, config, recorder, publisher, "8890116049",
        )

    assert recorder.get_batch(
        "20260810_csi1000_b6m_b2s_postclose_real_900"
    ) is None


@pytest.mark.parametrize(
    "relative_path",
    [
        "state/LIVE_OK_2026-08-10",
        "state/PR49_LIVE_OK_2026-08-10",
        "pr49_probe/state/PR49_LIVE_OK_2026-08-10",
    ],
)
def test_main_sell_publish_rejects_any_same_day_authorization_marker(
    recorder, tmp_path, monkeypatch, relative_path,
):
    config, request, publisher = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    recorder.set_execution_state(
        config["live"]["strategy_id"], "PAUSED",
        "exclusive operator sell", "2026-08-10T12:00:00+08:00",
    )
    marker = _write_main_authorization_marker(tmp_path, relative_path)

    with pytest.raises(SchemaError, match="authorization marker"):
        publish_operator_probe(
            request, config, recorder, publisher, "8890116049",
        )

    assert marker.is_file()
    assert recorder.get_batch(
        "20260810_csi1000_b6m_b2s_postclose_real_900"
    ) is None
    assert not list((tmp_path / "main_bridge" / "inbox").glob("*"))


@pytest.mark.parametrize(
    "relative_path",
    [
        "state/LIVE_OK_2026-08-10.intent.abcd.tmp",
        "pr49_probe/state/PR49_LIVE_OK_2026-08-10.intent.ef01.tmp",
    ],
)
def test_main_sell_publish_rejects_same_day_authorization_intent(
    recorder, tmp_path, monkeypatch, relative_path,
):
    config, request, publisher = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    recorder.set_execution_state(
        config["live"]["strategy_id"], "PAUSED",
        "exclusive operator sell", "2026-08-10T12:00:00+08:00",
    )
    intent = _write_main_authorization_marker(tmp_path, relative_path)

    with pytest.raises(SchemaError, match="authorization intent"):
        publish_operator_probe(
            request, config, recorder, publisher, "8890116049",
        )

    assert intent.is_file()
    assert not list((tmp_path / "main_bridge" / "inbox").glob("*"))


def test_main_sell_db_only_recovery_rejects_same_day_authorization_marker(
    recorder, tmp_path, monkeypatch,
):
    config, request, publisher = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    recorder.set_execution_state(
        config["live"]["strategy_id"], "PAUSED",
        "exclusive operator sell", "2026-08-10T12:00:00+08:00",
    )
    header, order = operator_probe.preview_operator_probe(
        request, config, recorder, "8890116049",
    )
    recorder.record_publish_plan(
        header, [order], required_execution_state="PAUSED",
        exclusive_same_day_live=True,
    )
    marker = _write_main_authorization_marker(
        tmp_path, "state/LIVE_OK_2026-08-10",
    )

    with pytest.raises(SchemaError, match="authorization marker"):
        publish_operator_probe(
            request, config, recorder, publisher, "8890116049",
        )

    assert marker.is_file()
    assert not list((tmp_path / "main_bridge" / "inbox").glob("*"))


def test_main_sell_visible_retry_with_marker_fails_without_rewriting_pair(
    recorder, tmp_path, monkeypatch,
):
    config, request, publisher = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    recorder.set_execution_state(
        config["live"]["strategy_id"], "PAUSED",
        "exclusive operator sell", "2026-08-10T12:00:00+08:00",
    )
    path = publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )
    done_path = path.with_suffix(".done")
    original_pair = (path.read_bytes(), done_path.read_bytes())
    _write_main_authorization_marker(
        tmp_path, "state/LIVE_OK_2026-08-10",
    )

    class NoRewritePublisher(SignalPublisher):
        def publish(self, header, orders):
            pytest.fail("an exact visible pair must never be rewritten")

    with pytest.raises(SchemaError, match="authorization marker"):
        publish_operator_probe(
            request, config, recorder,
            NoRewritePublisher(config["live"]["bridge_root"]),
            "8890116049",
        )

    assert (path.read_bytes(), done_path.read_bytes()) == original_pair


def test_main_sell_rechecks_marker_after_preflight_before_smb_exposure(
    recorder, tmp_path, monkeypatch,
):
    config, request, _ = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    recorder.set_execution_state(
        config["live"]["strategy_id"], "PAUSED",
        "exclusive operator sell", "2026-08-10T12:00:00+08:00",
    )
    marker_written = threading.Event()

    class ConcurrentMarkerPublisher(SignalPublisher):
        def ensure_publishable(self, header, orders):
            def authorize():
                _write_main_authorization_marker(
                    tmp_path, "state/LIVE_OK_2026-08-10",
                )
                marker_written.set()

            writer = threading.Thread(target=authorize)
            writer.start()
            writer.join(timeout=2)
            assert not writer.is_alive()
            assert marker_written.is_set()
            return super().ensure_publishable(header, orders)

    with pytest.raises(SchemaError, match="authorization marker"):
        publish_operator_probe(
            request, config, recorder,
            ConcurrentMarkerPublisher(config["live"]["bridge_root"]),
            "8890116049",
        )

    assert marker_written.is_set()
    assert not list((tmp_path / "main_bridge" / "inbox").glob("*"))


@pytest.mark.parametrize(
    "relative_path",
    [
        "state/LIVE_OK_2026-08-10",
        "pr49_probe/state/PR49_LIVE_OK_2026-08-10",
    ],
)
def test_main_sell_rechecks_marker_after_publish_internal_preflight(
    recorder, tmp_path, monkeypatch, relative_path,
):
    config, request, _ = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    recorder.set_execution_state(
        config["live"]["strategy_id"], "PAUSED",
        "exclusive operator sell", "2026-08-10T12:00:00+08:00",
    )

    class InternalPreflightMarkerPublisher(SignalPublisher):
        ensure_calls = 0

        def ensure_publishable(self, header, orders):
            result = super().ensure_publishable(header, orders)
            self.ensure_calls += 1
            if self.ensure_calls == 2:
                _write_main_authorization_marker(tmp_path, relative_path)
            return result

    with pytest.raises(SchemaError, match="authorization marker"):
        publish_operator_probe(
            request, config, recorder,
            InternalPreflightMarkerPublisher(config["live"]["bridge_root"]),
            "8890116049",
        )

    batch_id = "20260810_csi1000_b6m_b2s_postclose_real_900"
    assert not list((tmp_path / "main_bridge" / "inbox").glob(f"*{batch_id}*"))


def test_shared_authorization_lock_serializes_marker_after_final_guard(
    recorder, tmp_path, monkeypatch,
):
    config, request, _ = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    recorder.set_execution_state(
        config["live"]["strategy_id"], "PAUSED",
        "exclusive operator sell", "2026-08-10T12:00:00+08:00",
    )
    lock_path = (
        tmp_path / "main_bridge" / "state" /
        "OPERATOR_AUTHORIZATION.lock"
    )
    marker_attempted = threading.Event()
    marker_acquired = threading.Event()
    pair_visible_at_marker_lock = []

    class RenameRacePublisher(SignalPublisher):
        marker_thread = None
        acquired_before_first_write = None

        def _atomic_write(self, path, content):
            if self.marker_thread is None:
                def create_marker_under_shared_lock():
                    marker_attempted.set()
                    with FileLock(str(lock_path), timeout=3):
                        done = self.inbox / (
                            "signal_20260810_"
                            "csi1000_b6m_b2s_postclose_real_900.done"
                        )
                        pair_visible_at_marker_lock.append(done.is_file())
                        _write_main_authorization_marker(
                            tmp_path, "state/LIVE_OK_2026-08-10",
                        )
                        marker_acquired.set()

                self.marker_thread = threading.Thread(
                    target=create_marker_under_shared_lock,
                )
                self.marker_thread.start()
                assert marker_attempted.wait(1)
                self.acquired_before_first_write = marker_acquired.wait(0.2)
            return super()._atomic_write(path, content)

    publisher = RenameRacePublisher(config["live"]["bridge_root"])
    path = publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )
    publisher.marker_thread.join(timeout=4)

    assert not publisher.marker_thread.is_alive()
    assert publisher.acquired_before_first_write is False
    assert pair_visible_at_marker_lock == [True]
    assert path.is_file() and path.with_suffix(".done").is_file()
    assert (tmp_path / "main_bridge/state/LIVE_OK_2026-08-10").is_file()


@pytest.mark.parametrize(
    ("directory", "name"),
    [
        (
            "inbox",
            "signal_20260810_csi1000_b6m_b2s_postclose_real_001.jsonl",
        ),
        (
            "processing",
            "signal_20260810_csi1000_b6m_b2s_postclose_real_001.done",
        ),
        (
            "state",
            "active_20260810_csi1000_b6m_b2s_postclose_real_001.json",
        ),
        (
            "archive",
            "signal_20260810_csi1000_b6m_b2s_postclose_real_900.jsonl",
        ),
    ],
)
def test_main_sell_publish_rejects_other_same_day_qmt_artifacts(
    recorder, tmp_path, monkeypatch, directory, name,
):
    config, request, publisher = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    recorder.set_execution_state(
        config["live"]["strategy_id"], "PAUSED",
        "exclusive operator sell", "2026-08-10T12:00:00+08:00",
    )
    path = tmp_path / "main_bridge" / directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("evidence", encoding="utf-8")

    with pytest.raises(SchemaError, match="same-day QMT artifact"):
        publish_operator_probe(
            request, config, recorder, publisher, "8890116049",
        )

    assert recorder.get_batch(
        "20260810_csi1000_b6m_b2s_postclose_real_900"
    ) is None


def test_main_sell_publish_succeeds_when_paused_and_exclusive(
    recorder, tmp_path, monkeypatch,
):
    config, request, publisher = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    recorder.set_execution_state(
        config["live"]["strategy_id"], "PAUSED",
        "exclusive operator sell", "2026-08-10T12:00:00+08:00",
    )

    path = publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )
    retry_path = publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )

    assert path.name == (
        "signal_20260810_csi1000_b6m_b2s_postclose_real_900.jsonl"
    )
    assert retry_path == path
    assert recorder.get_execution_state(config["live"]["strategy_id"])[
        "state"
    ] == "PAUSED"


def test_main_sell_rechecks_same_day_ledger_inside_record_transaction(
    recorder, tmp_path, monkeypatch,
):
    config, request, _ = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    strategy_id = config["live"]["strategy_id"]
    recorder.set_execution_state(
        strategy_id, "PAUSED", "exclusive operator sell",
        "2026-08-10T12:00:00+08:00",
    )

    class InterleavingPublisher(SignalPublisher):
        def ensure_publishable(self, header, orders):
            recorder.record_publish_plan(BatchHeader(
                batch_id="20260810_csi1000_b6m_b2s_postclose_real_001",
                strategy_id=strategy_id,
                trade_date=TRADE_DATE,
                signal_date="2026-08-07",
                account_id="8890116049",
                account_type="STOCK",
                account_environment="REAL",
                mode="LIVE",
                created_at="2026-08-09T21:30:00+08:00",
                order_count=0,
                checksum="",
            ), [])
            return super().ensure_publishable(header, orders)

    with pytest.raises(SchemaError, match="same-day LIVE batch"):
        publish_operator_probe(
            request, config, recorder,
            InterleavingPublisher(config["live"]["bridge_root"]),
            "8890116049",
        )

    assert recorder.get_batch(
        "20260810_csi1000_b6m_b2s_postclose_real_900"
    ) is None


def test_main_sell_retry_never_recreates_a_qmt_claimed_inbox_pair(
    recorder, tmp_path, monkeypatch,
):
    config, request, publisher = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    strategy_id = config["live"]["strategy_id"]
    recorder.set_execution_state(
        strategy_id, "PAUSED", "exclusive operator sell",
        "2026-08-10T12:00:00+08:00",
    )
    publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )

    class ClaimingPublisher(SignalPublisher):
        claimed = False

        def ensure_publishable(self, header, orders):
            if not self.claimed:
                processing = self.bridge_root / "processing"
                processing.mkdir(parents=True, exist_ok=True)
                for suffix in ("jsonl", "done"):
                    source = self.inbox / f"signal_{header.batch_id}.{suffix}"
                    source.rename(processing / source.name)
                self.claimed = True
            return super().ensure_publishable(header, orders)

    with pytest.raises(SchemaError, match="same-day QMT artifact"):
        publish_operator_probe(
            request, config, recorder,
            ClaimingPublisher(config["live"]["bridge_root"]),
            "8890116049",
        )

    batch_id = "20260810_csi1000_b6m_b2s_postclose_real_900"
    assert not list((tmp_path / "main_bridge" / "inbox").glob(f"*{batch_id}*"))
    assert len(list(
        (tmp_path / "main_bridge" / "processing").glob(f"*{batch_id}*")
    )) == 2


def test_concurrent_main_db_only_recoveries_serialize_through_qmt_claim(
    recorder, tmp_path, monkeypatch,
):
    config, request, _ = _prepare_main_sell_publish(
        recorder, tmp_path, monkeypatch,
    )
    strategy_id = config["live"]["strategy_id"]
    recorder.set_execution_state(
        strategy_id, "PAUSED", "exclusive operator sell",
        "2026-08-10T12:00:00+08:00",
    )
    header, order = operator_probe.preview_operator_probe(
        request, config, recorder, "8890116049",
    )
    recorder.record_publish_plan(
        header, [order], required_execution_state="PAUSED",
        exclusive_same_day_live=True,
    )

    delayed_ready = threading.Event()
    allow_delayed_publish = threading.Event()
    concurrent_claimed = threading.Event()
    results = []
    errors = []

    def _claim_pair(publisher, publish_header):
        processing = publisher.bridge_root / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        for suffix in ("jsonl", "done"):
            source = publisher.inbox / (
                f"signal_{publish_header.batch_id}.{suffix}"
            )
            source.rename(processing / source.name)

    class DelayedRecovery(SignalPublisher):
        def publish(self, publish_header, orders, **kwargs):
            delayed_ready.set()
            assert allow_delayed_publish.wait(3)
            path = super().publish(publish_header, orders, **kwargs)
            processing = self.bridge_root / "processing"
            if not list(processing.glob(f"*{publish_header.batch_id}*")):
                _claim_pair(self, publish_header)
            return path

    class ImmediateClaim(SignalPublisher):
        def publish(self, publish_header, orders, **kwargs):
            path = super().publish(publish_header, orders, **kwargs)
            _claim_pair(self, publish_header)
            concurrent_claimed.set()
            return path

    def recover(publisher):
        try:
            results.append(publish_operator_probe(
                request, config, recorder, publisher, "8890116049",
            ))
        except BaseException as exc:
            errors.append(exc)

    delayed = threading.Thread(target=recover, args=(
        DelayedRecovery(config["live"]["bridge_root"]),
    ))
    immediate = threading.Thread(target=recover, args=(
        ImmediateClaim(config["live"]["bridge_root"]),
    ))
    delayed.start()
    assert delayed_ready.wait(2)
    immediate.start()
    # Without the cross-process operator gate, the immediate recovery can
    # publish and QMT-claim while the first recovery is paused. With the gate,
    # it remains blocked until the delayed recovery finishes.
    concurrent_claimed.wait(0.5)
    allow_delayed_publish.set()
    delayed.join(timeout=5)
    immediate.join(timeout=5)

    assert not delayed.is_alive()
    assert not immediate.is_alive()
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], SchemaError)
    assert "same-day QMT artifact" in str(errors[0])
    batch_id = "20260810_csi1000_b6m_b2s_postclose_real_900"
    assert not list((tmp_path / "main_bridge" / "inbox").glob(f"*{batch_id}*"))
    assert len(list(
        (tmp_path / "main_bridge" / "processing").glob(f"*{batch_id}*")
    )) == 2


def test_builds_a_one_lot_sell_for_the_fixed_price_profile(
    recorder, tmp_path, monkeypatch,
):
    _prepare_probe_sell(recorder, tmp_path, monkeypatch)
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
        _request(side="BUY", eligibility_confirmed=True),
        _config(tmp_path), recorder, TRADE_DATE,
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
    request = _request(side="BUY", eligibility_confirmed=True)

    path = publish_operator_probe(
        request, config, recorder,
        SignalPublisher(config["live"]["bridge_root"]), "8890116049",
    )

    assert recorder.get_orders(
        "20260810_csi1000_pr49_one_lot_probe_900"
    )[0]["max_quantity"] == 100
    assert json.loads(path.read_text().splitlines()[1])["max_quantity"] == 100


def test_durable_buy_retry_still_requires_eligibility_confirmation(
    recorder, tmp_path, monkeypatch,
):
    request = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    config = _config(tmp_path)

    with pytest.raises(SchemaError, match="eligibility-confirmed"):
        publish_operator_probe(
            dataclasses.replace(request, eligibility_confirmed=False),
            config,
            recorder,
            SignalPublisher(config["live"]["bridge_root"]),
            "8890116049",
        )


def test_durable_buy_retry_requires_latest_matched_account_snapshot(
    recorder, tmp_path, monkeypatch,
):
    request = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    batch_id = "20260807_csi1000_pr49_one_lot_probe_900"
    _remove_probe_inbox_pair(tmp_path, batch_id)
    _save_snapshot(
        recorder,
        trade_date="2026-08-07",
        shares=0,
        can_use_volume=0,
        batch_sequence=1,
        with_account=False,
    )
    config = _config(tmp_path)

    with pytest.raises(SchemaError, match="ACCOUNT evidence"):
        publish_operator_probe(
            request,
            config,
            recorder,
            SignalPublisher(config["live"]["bridge_root"]),
            "8890116049",
        )

    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "BUY_PLANNED"
    assert not list(
        (tmp_path / "pr49_probe" / "inbox").glob(f"*{batch_id}*")
    )

    _save_snapshot(
        recorder,
        trade_date="2026-08-07",
        shares=0,
        can_use_volume=0,
        batch_sequence=2,
    )
    path = publish_operator_probe(
        request,
        config,
        recorder,
        SignalPublisher(config["live"]["bridge_root"]),
        "8890116049",
    )
    assert path.is_file()


def test_durable_sell_retry_requires_latest_matched_account_snapshot(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        operator_probe, "_qlib_trade_dates",
        lambda start, end: ["2026-08-07", "2026-08-10"],
    )
    _prepare_probe_sell(recorder, tmp_path, monkeypatch)
    _save_snapshot(
        recorder,
        trade_date="2026-08-10",
        shares=100,
        can_use_volume=100,
        batch_sequence=0,
    )
    config = _config(tmp_path)
    request = _request(trade_date="2026-08-10")
    publisher = SignalPublisher(config["live"]["bridge_root"])
    publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )
    batch_id = "20260810_csi1000_pr49_one_lot_probe_900"
    _remove_probe_inbox_pair(tmp_path, batch_id)
    _save_snapshot(
        recorder,
        trade_date="2026-08-10",
        shares=100,
        can_use_volume=100,
        batch_sequence=1,
        with_account=False,
    )

    with pytest.raises(SchemaError, match="ACCOUNT evidence"):
        publish_operator_probe(
            request, config, recorder, publisher, "8890116049",
        )

    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "SELL_PLANNED"
    assert not list(
        (tmp_path / "pr49_probe" / "inbox").glob(f"*{batch_id}*")
    )

    _save_snapshot(
        recorder,
        trade_date="2026-08-10",
        shares=100,
        can_use_volume=0,
        batch_sequence=2,
    )
    path = publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )
    assert path.is_file()


def test_post_fill_buy_retry_cannot_recreate_inbox_pair(
    recorder, tmp_path, monkeypatch,
):
    request = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    batch_id = "20260807_csi1000_pr49_one_lot_probe_900"
    _remove_probe_inbox_pair(tmp_path, batch_id)
    _apply_probe_buy_fill(recorder, request)
    config = _config(tmp_path)

    with pytest.raises(SchemaError, match="durable probe retry"):
        publish_operator_probe(
            request, config, recorder,
            SignalPublisher(config["live"]["bridge_root"]), "8890116049",
        )

    assert not list((tmp_path / "pr49_probe" / "inbox").glob(f"*{batch_id}*"))


def test_durable_retry_publish_is_serialized_before_terminal_import(
    recorder, tmp_path, monkeypatch,
):
    request = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    batch_id = "20260807_csi1000_pr49_one_lot_probe_900"
    _remove_probe_inbox_pair(tmp_path, batch_id)
    config = _config(tmp_path)
    write_attempted = threading.Event()
    original_apply_position_delta = LiveRecorder._apply_position_delta

    def observing_apply_position_delta(*args, **kwargs):
        write_attempted.set()
        return original_apply_position_delta(*args, **kwargs)

    monkeypatch.setattr(
        LiveRecorder,
        "_apply_position_delta",
        staticmethod(observing_apply_position_delta),
    )

    class InterleavingPublisher(SignalPublisher):
        def __init__(self, bridge_root):
            super().__init__(bridge_root)
            self.import_finished = threading.Event()
            self.import_errors = []
            self.import_thread = None

        def publish(self, header, orders):
            def import_terminal():
                try:
                    _apply_probe_buy_fill(
                        recorder, request, with_snapshot=False,
                    )
                except BaseException as exc:  # surfaced in the test thread
                    self.import_errors.append(exc)
                finally:
                    self.import_finished.set()

            self.import_thread = threading.Thread(target=import_terminal)
            self.import_thread.start()
            assert write_attempted.wait(2)
            assert not self.import_finished.wait(0.2)
            return super().publish(header, orders)

    publisher = InterleavingPublisher(config["live"]["bridge_root"])
    path = publish_operator_probe(
        request, config, recorder, publisher, "8890116049",
    )
    publisher.import_thread.join(timeout=5)

    assert not publisher.import_thread.is_alive()
    assert publisher.import_errors == []
    assert path.is_file()
    assert recorder.get_fills(batch_id)[0]["status"] == "FILLED"


def test_post_close_sell_retry_cannot_recreate_inbox_pair(
    recorder, tmp_path, monkeypatch,
):
    _prepare_probe_sell(recorder, tmp_path, monkeypatch)
    _save_snapshot(recorder, can_use_volume=100)
    config = _config(tmp_path)
    request = _request()
    publisher = SignalPublisher(config["live"]["bridge_root"])
    publish_operator_probe(request, config, recorder, publisher, "8890116049")
    batch_id = "20260810_csi1000_pr49_one_lot_probe_900"
    _remove_probe_inbox_pair(tmp_path, batch_id)
    recorder.apply_fill(FillEvent.from_dict({
        "type": "fill_event",
        "batch_id": batch_id,
        "client_order_id": "20260810900001S",
        "mode": "LIVE",
        "stock_code": STOCK_CODE,
        "side": "SELL",
        "status": "FILLED",
        "requested_qty": 100,
        "filled_qty": 100,
        "avg_price": 10.5,
        "qmt_order_id": "probe-sell-closed",
        "message": "",
        "ts": "2026-08-10T15:06:00+08:00",
    }))
    recorder.save_broker_snapshot(
        batch_id, {"account_id": "8890116049"}, [],
    )
    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "CLOSED"

    with pytest.raises(SchemaError, match="durable probe retry"):
        publish_operator_probe(
            request, config, recorder, publisher, "8890116049",
        )

    assert not list((tmp_path / "pr49_probe" / "inbox").glob(f"*{batch_id}*"))


def test_old_buy_retry_cannot_replace_newer_terminal_lifecycle(
    recorder, tmp_path, monkeypatch,
):
    old = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    old_batch = "20260807_csi1000_pr49_one_lot_probe_900"
    _remove_probe_inbox_pair(tmp_path, old_batch)
    _apply_probe_buy_fill(
        recorder, old, filled_qty=0, status="REJECTED",
    )
    newer = _record_probe_buy_plan(
        recorder, tmp_path, monkeypatch, trade_date="2026-08-10",
    )
    _apply_probe_buy_fill(recorder, newer)
    newer_batch = "20260810_csi1000_pr49_one_lot_probe_900"
    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["buy_batch_id"] \
        == newer_batch
    config = _config(tmp_path)

    with pytest.raises(SchemaError, match="durable probe retry"):
        publish_operator_probe(
            old, config, recorder,
            SignalPublisher(config["live"]["bridge_root"]), "8890116049",
        )

    lifecycle = recorder.get_operator_probe_lifecycle(STRATEGY_ID)
    assert lifecycle["buy_batch_id"] == newer_batch
    assert lifecycle["state"] == "BUY_FILLED"
    assert not list((tmp_path / "pr49_probe" / "inbox").glob(f"*{old_batch}*"))


def test_publish_records_plan_before_exposing_batch(recorder, tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    _prepare_probe_sell(recorder, tmp_path, monkeypatch)
    _save_snapshot(recorder)
    request = _request()
    config = _config(tmp_path)

    class InspectingPublisher(SignalPublisher):
        def ensure_publishable(self, header, orders):
            assert recorder.get_batch(header.batch_id) is None
            return False

        def publish(self, header, orders):
            batch = recorder.get_batch(header.batch_id)
            assert batch["planned_orders"] == 1
            assert recorder.get_orders(header.batch_id)[0]["quantity"] == 100
            return tmp_path / "published.jsonl"

    assert publish_operator_probe(
        request, config, recorder,
        InspectingPublisher(tmp_path / "pr49_probe"), "8890116049",
    ) == tmp_path / "published.jsonl"


def test_publish_uses_record_plan_as_the_atomic_pre_publish_gate(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", "YES")
    monkeypatch.setenv("QMT_REAL_ACCOUNT_ID", "8890116049")
    _prepare_probe_sell(recorder, tmp_path, monkeypatch)
    _save_snapshot(recorder)

    class InterleavingPublisher(SignalPublisher):
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
            InterleavingPublisher(tmp_path / "pr49_probe"), "8890116049",
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
    _prepare_probe_sell(recorder, tmp_path, monkeypatch)
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
    _prepare_probe_sell(recorder, tmp_path, monkeypatch)
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
    _prepare_probe_sell(writable, tmp_path, monkeypatch)
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


def test_probe_trade_calendar_initializes_standalone_qlib(
    tmp_path, monkeypatch,
):
    data_root = tmp_path / "cn_data"
    calendars = data_root / "calendars"
    calendars.mkdir(parents=True)
    (calendars / "day.txt").write_text(
        "2026-08-07\n2026-08-10\n", encoding="utf-8",
    )
    monkeypatch.setenv("QLIB_CN_DATA_DIR", str(data_root))

    assert operator_probe._qlib_trade_dates(
        "2026-08-07", "2026-08-10",
    ) == ["2026-08-07", "2026-08-10"]


def test_probe_buy_requires_explicit_eligibility_confirmation(recorder, tmp_path):
    _save_snapshot(recorder, shares=0, can_use_volume=0)

    with pytest.raises(SchemaError, match="eligibility-confirmed"):
        operator_probe.validate_probe_transition(
            _request(side="BUY"), recorder,
        )


def test_probe_buy_plan_and_actual_fill_advance_lifecycle(
    recorder, tmp_path, monkeypatch,
):
    request = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)

    lifecycle = recorder.get_operator_probe_lifecycle(STRATEGY_ID)
    assert lifecycle["state"] == "BUY_PLANNED"
    assert lifecycle["stock_code"] == STOCK_CODE
    assert lifecycle["buy_batch_id"] == (
        "20260807_csi1000_pr49_one_lot_probe_900"
    )

    _apply_probe_buy_fill(recorder, request, with_snapshot=False)

    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "BUY_PLANNED"

    batch_id = "20260807_csi1000_pr49_one_lot_probe_900"
    recorder.save_broker_snapshot(
        batch_id,
        {"account_id": "8890116049"},
        [{
            "stock_code": STOCK_CODE,
            "shares": 100,
            "can_use_volume": 0,
            "avg_cost": 10.0,
            "market_value": 1000.0,
        }],
    )

    lifecycle = recorder.get_operator_probe_lifecycle(STRATEGY_ID)
    assert lifecycle["state"] == "BUY_FILLED"


def test_probe_buy_positions_only_snapshot_is_not_lifecycle_evidence(
    recorder, tmp_path, monkeypatch,
):
    request = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    batch_id = "20260807_csi1000_pr49_one_lot_probe_900"
    _apply_probe_buy_fill(recorder, request, with_snapshot=False)

    recorder.save_broker_snapshot(
        batch_id,
        None,
        [{
            "stock_code": STOCK_CODE,
            "shares": 100,
            "can_use_volume": 0,
            "avg_cost": 10.0,
            "market_value": 1000.0,
        }],
    )

    assert recorder.get_broker_positions("2026-08-07") == {
        STOCK_CODE: 100,
    }
    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "BUY_PLANNED"


def test_terminal_probe_failure_marks_lifecycle_failed(
    recorder, tmp_path, monkeypatch,
):
    request = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)

    _apply_probe_buy_fill(
        recorder, request, filled_qty=0, status="REJECTED",
    )

    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "FAILED"


def test_probe_lifecycle_accepts_snapshot_before_terminal_fill(
    recorder, tmp_path, monkeypatch,
):
    request = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    batch_id = "20260807_csi1000_pr49_one_lot_probe_900"
    recorder.save_broker_snapshot(
        batch_id,
        {"account_id": "8890116049"},
        [{
            "stock_code": STOCK_CODE,
            "shares": 100,
            "can_use_volume": 0,
            "avg_cost": 10.0,
            "market_value": 1000.0,
        }],
    )

    _apply_probe_buy_fill(recorder, request, with_snapshot=False)

    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "BUY_FILLED"


def test_probe_lifecycle_fails_on_contradictory_terminal_snapshot(
    recorder, tmp_path, monkeypatch,
):
    request = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    _apply_probe_buy_fill(recorder, request, with_snapshot=False)

    recorder.save_broker_snapshot(
        "20260807_csi1000_pr49_one_lot_probe_900",
        {"account_id": "8890116049"},
        [],
    )

    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "FAILED"


def test_probe_sell_requires_later_actual_available_same_symbol_buy(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        operator_probe, "_qlib_trade_dates",
        lambda start, end: ["2026-08-07", "2026-08-10"],
    )
    buy = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    _apply_probe_buy_fill(recorder, buy)
    _save_snapshot(
        recorder, trade_date="2026-08-10", shares=100,
        can_use_volume=100,
    )
    sell = _request(trade_date="2026-08-10")

    assert operator_probe.validate_probe_transition(sell, recorder) is None

    for invalid, message in (
        (_request(trade_date="2026-08-07"), "later Qlib trade date"),
        (_request(trade_date="2026-08-10", stock_code="000001.SZ"), "symbol"),
    ):
        with pytest.raises(SchemaError, match=message):
            operator_probe.validate_probe_transition(invalid, recorder)


def test_probe_sell_uses_latest_imported_snapshot_not_largest_batch_id(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        operator_probe, "_qlib_trade_dates",
        lambda start, end: ["2026-08-07", "2026-08-10"],
    )
    buy = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    _apply_probe_buy_fill(recorder, buy)
    old_batch = "20260810_zzz_snapshot"
    _record_real_snapshot_batch(recorder, old_batch, "2026-08-10")
    recorder.save_broker_snapshot(
        old_batch, {"account_id": "8890116049"}, [{
            "stock_code": STOCK_CODE,
            "shares": 100,
            "can_use_volume": 100,
            "avg_cost": 10.0,
            "market_value": 1000.0,
        }],
    )
    new_batch = "20260810_aaa_snapshot"
    _record_real_snapshot_batch(recorder, new_batch, "2026-08-10")
    recorder.save_broker_snapshot(
        new_batch, {"account_id": "8890116049"}, [{
            "stock_code": STOCK_CODE,
            "shares": 100,
            "can_use_volume": 0,
            "avg_cost": 10.0,
            "market_value": 1000.0,
        }],
    )

    with pytest.raises(SchemaError, match="available"):
        operator_probe.validate_probe_transition(
            _request(trade_date="2026-08-10"), recorder,
        )


def test_probe_sell_rejects_plan_only_buy_quantity(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        operator_probe, "_qlib_trade_dates",
        lambda start, end: ["2026-08-07", "2026-08-10"],
    )
    _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    recorder.upsert_position(STOCK_CODE, 100, 10.0, "2026-08-07")
    _save_snapshot(
        recorder, trade_date="2026-08-10", shares=100,
        can_use_volume=100,
    )

    with pytest.raises(SchemaError, match="actual applied BUY"):
        operator_probe.validate_probe_transition(
            _request(trade_date="2026-08-10"), recorder,
        )


def test_probe_sell_rejects_unresolved_prior_probe_batch(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        operator_probe, "_qlib_trade_dates",
        lambda start, end: ["2026-08-07", "2026-08-10"],
    )
    buy = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    _apply_probe_buy_fill(recorder, buy)
    recorder.record_publish_plan(
        dataclasses.replace(
            operator_probe._header(
                _request(
                    trade_date="2026-08-08", side="BUY",
                    eligibility_confirmed=True,
                ),
                _config(tmp_path)["live"], "8890116049",
            ),
            batch_id="20260808_csi1000_pr49_one_lot_probe_901",
        ),
        [dataclasses.replace(
            operator_probe._make_operator_order(
                _request(
                    trade_date="2026-08-08", side="BUY",
                    eligibility_confirmed=True,
                ),
                _config(tmp_path)["live"],
            ),
            batch_id="20260808_csi1000_pr49_one_lot_probe_901",
            client_order_id="20260808901001B",
        )],
    )
    _save_snapshot(
        recorder, trade_date="2026-08-10", shares=100,
        can_use_volume=100,
    )

    with pytest.raises(SchemaError, match="unresolved prior probe batch"):
        operator_probe.validate_probe_transition(
            _request(trade_date="2026-08-10"), recorder,
        )


def test_probe_sell_terminal_fill_closes_lifecycle(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        operator_probe, "_qlib_trade_dates",
        lambda start, end: ["2026-08-07", "2026-08-10"],
    )
    buy = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    _apply_probe_buy_fill(recorder, buy)
    _save_snapshot(
        recorder, trade_date="2026-08-10", shares=100,
        can_use_volume=100,
    )
    config = _config(tmp_path)
    sell = _request(trade_date="2026-08-10")
    publish_operator_probe(
        sell, config, recorder,
        SignalPublisher(config["live"]["bridge_root"]), "8890116049",
    )
    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "SELL_PLANNED"

    recorder.apply_fill(FillEvent.from_dict({
        "type": "fill_event",
        "batch_id": "20260810_csi1000_pr49_one_lot_probe_900",
        "client_order_id": "20260810900001S",
        "mode": "LIVE",
        "stock_code": STOCK_CODE,
        "side": "SELL",
        "status": "FILLED",
        "requested_qty": 100,
        "filled_qty": 100,
        "avg_price": 10.5,
        "qmt_order_id": "probe-sell-1",
        "message": "",
        "ts": "2026-08-10T15:06:00+08:00",
    }))

    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "SELL_PLANNED"

    recorder.save_broker_snapshot(
        "20260810_csi1000_pr49_one_lot_probe_900",
        {"account_id": "8890116049"},
        [],
    )

    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "CLOSED"


def test_probe_sell_positions_only_snapshot_without_symbol_is_not_evidence(
    recorder, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        operator_probe, "_qlib_trade_dates",
        lambda start, end: ["2026-08-07", "2026-08-10"],
    )
    buy = _record_probe_buy_plan(recorder, tmp_path, monkeypatch)
    _apply_probe_buy_fill(recorder, buy)
    _save_snapshot(
        recorder, trade_date="2026-08-10", shares=100,
        can_use_volume=100,
    )
    config = _config(tmp_path)
    sell = _request(trade_date="2026-08-10")
    publish_operator_probe(
        sell, config, recorder,
        SignalPublisher(config["live"]["bridge_root"]), "8890116049",
    )
    batch_id = "20260810_csi1000_pr49_one_lot_probe_900"
    recorder.apply_fill(FillEvent.from_dict({
        "type": "fill_event",
        "batch_id": batch_id,
        "client_order_id": "20260810900001S",
        "mode": "LIVE",
        "stock_code": STOCK_CODE,
        "side": "SELL",
        "status": "FILLED",
        "requested_qty": 100,
        "filled_qty": 100,
        "avg_price": 10.5,
        "qmt_order_id": "probe-sell-positions-only",
        "message": "",
        "ts": "2026-08-10T15:06:00+08:00",
    }))

    recorder.save_broker_snapshot(batch_id, None, [])

    assert recorder.get_broker_positions("2026-08-10") == {}
    assert recorder.get_operator_probe_lifecycle(STRATEGY_ID)["state"] \
        == "SELL_PLANNED"
