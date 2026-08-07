"""SignalPublisher：原子写 jsonl + done 标记。"""
import dataclasses
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.signal_publisher import SignalPublisher, PublishError
from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.signal_schema import (
    BatchHeader,
    FillEvent,
    SchemaError,
    SignalOrder,
    compute_checksum,
)
from live_trading.scripts import run_publish_signals
from live_trading.scripts.run_publish_signals import publish_recorded_plan

BATCH_ID = "20260714_csi300_topk10_001"


def _orders():
    return [
        SignalOrder(
            batch_id=BATCH_ID, client_order_id="20260714001S",
            stock_code="000001.SZ", side="SELL", quantity=800,
            target_value=0.0, price_type="CLOSE_AUCTION_LIMIT", limit_price=0.0,
            priority=10,
            instrument_qlib="SZ000001", reason="topk_drop",
        ),
        SignalOrder(
            batch_id=BATCH_ID, client_order_id="20260714002B",
            stock_code="600000.SH", side="BUY", quantity=0,
            target_value=15_833.33, price_type="CLOSE_AUCTION_LIMIT",
            limit_price=0.0, priority=20,
            instrument_qlib="SH600000", reason="topk_add",
        ),
    ]


def _header(order_count=0, checksum=""):
    return BatchHeader(
        batch_id=BATCH_ID, strategy_id="csi300_topk10",
        trade_date="2026-07-14", signal_date="2026-07-11",
        account_id="123456", account_type="STOCK", mode="SIMULATE",
        account_environment="SIMULATION",
        created_at="2026-07-11T21:05:00+08:00",
        order_count=order_count, checksum=checksum,
    )


def test_publish_writes_jsonl_and_done(tmp_path):
    pub = SignalPublisher(tmp_path)
    pub.publish(_header(), _orders())

    jsonl = tmp_path / "inbox" / f"signal_{BATCH_ID}.jsonl"
    done = tmp_path / "inbox" / f"signal_{BATCH_ID}.done"
    assert jsonl.exists() and done.exists()

    lines = jsonl.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert header["type"] == "batch_header"
    assert header["order_count"] == 2  # publisher 自动填充
    order_lines = lines[1:]
    assert len(order_lines) == 2
    assert all(json.loads(l)["type"] == "order" for l in order_lines)

    # done 内容 == header checksum == 对 order 行重算的 checksum
    expected = compute_checksum(order_lines)
    assert header["checksum"] == expected
    assert done.read_text(encoding="utf-8").strip() == expected


def test_publish_exact_retry_is_idempotent(tmp_path):
    pub = SignalPublisher(tmp_path)
    first = pub.publish(_header(), _orders())
    first_bytes = first.read_bytes()
    second = pub.publish(_header(), _orders())
    assert second == first
    assert second.read_bytes() == first_bytes


def test_legacy_default_order_checksum_retries_against_durable_plan(tmp_path):
    legacy_line = (
        '{"batch_id":"20260714_csi300_topk10_001",'
        '"client_order_id":"20260714001S",'
        '"instrument_qlib":"SZ000001","limit_price":0.0,'
        '"price_type":"CLOSE_AUCTION_LIMIT","priority":10,'
        '"quantity":800,"reason":"topk_drop","side":"SELL",'
        '"stock_code":"000001.SZ","target_value":0.0,"type":"order"}'
    )
    # This was the exact schema v2.0 byte sequence before max_quantity existed.
    order = SignalOrder.from_dict(json.loads(legacy_line))
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    expected_checksum = compute_checksum([legacy_line])

    recorder.record_publish_plan(_header(), [order])
    recorder.record_publish_plan(_header(), [order])

    assert recorder.get_batch(BATCH_ID)["order_checksum"] == expected_checksum


def test_publish_conflicting_retry_is_rejected(tmp_path):
    pub = SignalPublisher(tmp_path)
    pub.publish(_header(), _orders())
    changed = dataclasses.replace(_orders()[1], target_value=16_000.0)
    with pytest.raises(PublishError, match="conflicts"):
        pub.publish(_header(), [_orders()[0], changed])


def test_publish_validates_orders(tmp_path):
    import dataclasses
    bad = [dataclasses.replace(_orders()[0], quantity=150)]
    with pytest.raises(Exception):
        SignalPublisher(tmp_path).publish(_header(), bad)
    # 校验失败不得留下任何文件
    inbox = tmp_path / "inbox"
    assert not list(inbox.glob("*")) if inbox.exists() else True


def test_no_tmp_files_left(tmp_path):
    pub = SignalPublisher(tmp_path)
    pub.publish(_header(), _orders())
    assert list((tmp_path / "inbox").glob("*.tmp")) == []


def test_empty_orders_publish_header_only_terminal_batch(tmp_path):
    path = SignalPublisher(tmp_path).publish(_header(), [])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    header = json.loads(lines[0])
    assert header["order_count"] == 0
    assert header["checksum"] == compute_checksum([])


def test_plan_is_durable_before_signal_becomes_visible(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))

    class InspectingPublisher:
        def ensure_publishable(self, header, orders):
            assert header.batch_id == BATCH_ID

        def publish(self, header, orders):
            assert recorder.get_batch(BATCH_ID)["planned_orders"] == 2
            assert len(recorder.get_orders(BATCH_ID)) == 2
            return tmp_path / "inbox" / f"signal_{BATCH_ID}.jsonl"

    path = publish_recorded_plan(
        recorder, InspectingPublisher(), _header(), _orders(),
    )
    assert path.name == f"signal_{BATCH_ID}.jsonl"


def test_existing_exact_shared_batch_is_adopted_into_durable_db_plan(tmp_path):
    publisher = SignalPublisher(tmp_path)
    publisher.publish(_header(), _orders())
    recorder = LiveRecorder(str(tmp_path / "live.db"))

    publish_recorded_plan(recorder, publisher, _header(), _orders())

    assert recorder.get_batch(BATCH_ID)["planned_orders"] == 2
    assert len(recorder.get_orders(BATCH_ID)) == 2


def test_conflicting_publish_retry_preserves_original_plan(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    recorder.record_publish_plan(_header(), _orders())
    changed = [
        SignalOrder(**{
            **order.__dict__,
            "target_value": order.target_value + (1.0 if order.side == "BUY" else 0.0),
        })
        for order in _orders()
    ]

    with pytest.raises(SchemaError, match="conflicts with durable plan"):
        recorder.record_publish_plan(_header(), changed)

    assert [row["target_value"] for row in recorder.get_orders(BATCH_ID)] == [
        0.0, 15_833.33,
    ]


def test_publish_retry_cannot_change_account_or_signal_date(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    recorder.record_publish_plan(_header(), _orders())

    for changed in (
        dataclasses.replace(_header(), account_id="DIFFERENT"),
        dataclasses.replace(_header(), signal_date="2026-07-10"),
    ):
        with pytest.raises(SchemaError, match="conflicts with durable plan"):
            recorder.record_publish_plan(changed, _orders())
    with pytest.raises(SchemaError, match="conflicts with durable plan"):
        recorder.record_publish_plan(
            dataclasses.replace(
                _header(), account_environment="REAL", mode="LIVE",
            ), _orders(),
        )

    batch = recorder.get_batch(BATCH_ID)
    assert batch["account_id"] == "123456"
    assert batch["signal_date"] == "2026-07-11"
    with pytest.raises(SchemaError, match="immutable durable plan"):
        recorder.record_orders(BATCH_ID, _orders())
    with pytest.raises(SchemaError, match="immutable durable plan"):
        recorder.record_batch(BATCH_ID, "2026-07-15", "LIVE", 2)


def test_real_publish_plan_is_durable(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "real.db"))
    header = dataclasses.replace(
        _header(), account_environment="REAL", mode="LIVE",
        account_id="8890116049",
    )

    recorder.record_publish_plan(header, _orders())

    batch = recorder.get_batch(BATCH_ID)
    assert batch["account_environment"] == "REAL"
    assert batch["account_id"] == "8890116049"


def test_publish_guard_refuses_unreconciled_prior_live_batch(tmp_path):
    class FakeRecorder:
        @staticmethod
        def get_unreconciled_active_live_batches_before(
            trade_date, strategy_id=None,
        ):
            assert trade_date == "2026-07-17"
            assert strategy_id == "csi1000_pr49_one_lot_probe"
            return [{
                "batch_id": "20260716_csi300_topk10_001",
                "planned_orders": 2,
                "terminal_orders": 0,
            }]

    with pytest.raises(
        SystemExit, match=r"20260716_csi300_topk10_001.*2 missing",
    ):
        run_publish_signals.ensure_prior_live_batches_terminal(
            FakeRecorder(), "2026-07-17", "csi1000_pr49_one_lot_probe",
        )


def test_publish_guard_ignores_unreconciled_other_strategy(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    main_batch_id = "20260716_csi1000_b6m_b2s_postclose_real_001"
    probe_batch_id = "20260716_csi1000_pr49_one_lot_probe_001"
    recorder.record_batch(main_batch_id, "2026-07-16", "LIVE", 1)
    recorder.record_batch(probe_batch_id, "2026-07-16", "LIVE", 0)
    with recorder._conn() as conn:
        conn.executemany(
            "UPDATE batches SET strategy_id=? WHERE batch_id=?",
            [
                ("csi1000_b6m_b2s_postclose_real", main_batch_id),
                ("csi1000_pr49_one_lot_probe", probe_batch_id),
            ],
        )

    assert recorder.get_unreconciled_active_live_batches_before(
        "2026-07-17", strategy_id="csi1000_pr49_one_lot_probe",
    ) == []
    run_publish_signals.ensure_prior_live_batches_terminal(
        recorder, "2026-07-17", "csi1000_pr49_one_lot_probe",
    )


def test_publish_guard_refuses_prior_failed_sell(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    header = dataclasses.replace(
        _header(order_count=2), mode="LIVE",
        checksum=compute_checksum([o.to_json_line() for o in _orders()]),
    )
    recorder.record_publish_plan(header, _orders())
    recorder.apply_fill(FillEvent(
        batch_id=BATCH_ID, client_order_id="20260714001S", mode="LIVE",
        stock_code="000001.SZ", side="SELL", status="ERROR",
        requested_qty=800, filled_qty=0, avg_price=0.0,
        qmt_order_id="", message="not observed", ts="2026-07-14T15:00:30",
    ))

    with pytest.raises(SystemExit, match=r"failed prior SELL.*20260714001S"):
        run_publish_signals.ensure_no_failed_prior_sells(
            recorder, "2026-07-15",
        )
