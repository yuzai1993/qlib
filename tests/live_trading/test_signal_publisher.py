"""SignalPublisher：原子写 jsonl + done 标记。"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.signal_publisher import SignalPublisher, PublishError
from live_trading.modules.signal_schema import (
    BatchHeader,
    SignalOrder,
    compute_checksum,
)

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
