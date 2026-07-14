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
    SchemaError,
    SignalOrder,
    compute_checksum,
)
from live_trading.scripts.run_publish_signals import publish_recorded_plan

BATCH_ID = "20260714_csi300_topk10_001"


def _orders():
    return [
        SignalOrder(
            batch_id=BATCH_ID, client_order_id="20260714001S",
            stock_code="000001.SZ", side="SELL", quantity=800,
            price_type="FIX", limit_price=19.80, priority=10,
            instrument_qlib="SZ000001", reason="topk_drop",
        ),
        SignalOrder(
            batch_id=BATCH_ID, client_order_id="20260714002B",
            stock_code="600000.SH", side="BUY", quantity=500,
            price_type="FIX", limit_price=10.10, priority=20,
            instrument_qlib="SH600000", reason="topk_add",
        ),
    ]


def _header(order_count=0, checksum=""):
    return BatchHeader(
        batch_id=BATCH_ID, strategy_id="csi300_topk10",
        trade_date="2026-07-14", signal_date="2026-07-11",
        account_id="123456", account_type="STOCK", mode="SIMULATE",
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


def test_publish_refuses_duplicate_batch(tmp_path):
    pub = SignalPublisher(tmp_path)
    pub.publish(_header(), _orders())
    with pytest.raises(PublishError):
        pub.publish(_header(), _orders())


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


def test_empty_orders_rejected(tmp_path):
    with pytest.raises(PublishError):
        SignalPublisher(tmp_path).publish(_header(), [])


def test_plan_is_durable_before_signal_becomes_visible(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))

    class InspectingPublisher:
        def ensure_available(self, batch_id):
            assert batch_id == BATCH_ID

        def publish(self, header, orders):
            assert recorder.get_batch(BATCH_ID)["planned_orders"] == 2
            assert len(recorder.get_orders(BATCH_ID)) == 2
            return tmp_path / "inbox" / f"signal_{BATCH_ID}.jsonl"

    path = publish_recorded_plan(
        recorder, InspectingPublisher(), _header(), _orders(),
    )
    assert path.name == f"signal_{BATCH_ID}.jsonl"


def test_existing_shared_batch_does_not_create_unverified_db_plan(tmp_path):
    publisher = SignalPublisher(tmp_path)
    publisher.publish(_header(), _orders())
    recorder = LiveRecorder(str(tmp_path / "live.db"))

    with pytest.raises(PublishError, match="already published"):
        publish_recorded_plan(recorder, publisher, _header(), _orders())

    assert recorder.get_batch(BATCH_ID) is None
    assert recorder.get_orders(BATCH_ID) == []


def test_conflicting_publish_retry_preserves_original_plan(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    recorder.record_publish_plan(_header(), _orders())
    changed = [
        SignalOrder(**{**order.__dict__, "limit_price": order.limit_price + 1.0})
        for order in _orders()
    ]

    with pytest.raises(SchemaError, match="conflicts with durable plan"):
        recorder.record_publish_plan(_header(), changed)

    assert [row["limit_price"] for row in recorder.get_orders(BATCH_ID)] == [
        19.80, 10.10,
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

    batch = recorder.get_batch(BATCH_ID)
    assert batch["account_id"] == "123456"
    assert batch["signal_date"] == "2026-07-11"
    with pytest.raises(SchemaError, match="immutable durable plan"):
        recorder.record_orders(BATCH_ID, _orders())
    with pytest.raises(SchemaError, match="immutable durable plan"):
        recorder.record_batch(BATCH_ID, "2026-07-15", "LIVE", 2)
