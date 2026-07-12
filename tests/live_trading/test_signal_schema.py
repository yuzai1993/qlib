"""信号文件协议：数据对象、校验、checksum、client_order_id。"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.signal_schema import (
    BatchHeader,
    SignalOrder,
    FillEvent,
    make_client_order_id,
    compute_checksum,
    validate_order,
    validate_batch,
    validate_fill,
    SchemaError,
)


def _order(**kwargs) -> SignalOrder:
    base = dict(
        batch_id="20260714_csi300_topk10_001",
        client_order_id="20260714001S",
        stock_code="600000.SH",
        side="SELL",
        quantity=800,
        price_type="FIX",
        limit_price=10.41,
        priority=10,
        instrument_qlib="SH600000",
        reason="topk_drop",
    )
    base.update(kwargs)
    return SignalOrder(**base)


def _header(**kwargs) -> BatchHeader:
    base = dict(
        batch_id="20260714_csi300_topk10_001",
        strategy_id="csi300_topk10",
        trade_date="2026-07-14",
        signal_date="2026-07-11",
        account_id="123456",
        account_type="STOCK",
        mode="SIMULATE",
        created_at="2026-07-11T21:05:00+08:00",
        order_count=1,
        checksum="sha256:abc",
    )
    base.update(kwargs)
    return BatchHeader(**base)


# ---------- client_order_id ----------

def test_make_client_order_id_format_and_length():
    coid = make_client_order_id("2026-07-14", 1, "SELL")
    assert coid == "20260714001S"
    assert len(coid) <= 24
    assert make_client_order_id("2026-07-14", 23, "BUY") == "20260714023B"


def test_make_client_order_id_rejects_bad_seq():
    with pytest.raises(ValueError):
        make_client_order_id("2026-07-14", 0, "BUY")
    with pytest.raises(ValueError):
        make_client_order_id("2026-07-14", 1000, "BUY")


# ---------- checksum ----------

def test_checksum_deterministic_and_order_sensitive():
    l1 = _order().to_json_line()
    l2 = _order(client_order_id="20260714002B", side="BUY").to_json_line()
    c_a = compute_checksum([l1, l2])
    c_b = compute_checksum([l1, l2])
    c_swapped = compute_checksum([l2, l1])
    assert c_a == c_b
    assert c_a != c_swapped
    assert c_a.startswith("sha256:")


# ---------- serialization ----------

def test_order_json_roundtrip():
    o = _order()
    d = json.loads(o.to_json_line())
    assert d["type"] == "order"
    assert SignalOrder.from_dict(d) == o


def test_header_json_roundtrip():
    h = _header()
    d = json.loads(h.to_json_line())
    assert d["type"] == "batch_header"
    assert BatchHeader.from_dict(d) == h


# ---------- validation ----------

def test_validate_order_accepts_good():
    validate_order(_order())


@pytest.mark.parametrize("bad_kwargs", [
    {"side": "HOLD"},
    {"quantity": 0},
    {"quantity": -100},
    {"quantity": 150},          # 非整手
    {"limit_price": 0},
    {"limit_price": -1.0},
    {"stock_code": "SH600000"},  # qlib 格式未转换
])
def test_validate_order_rejects_bad(bad_kwargs):
    with pytest.raises(SchemaError):
        validate_order(_order(**bad_kwargs))


def test_validate_batch_checks_order_count_and_ids():
    h = _header(order_count=2)
    orders = [_order(), _order(client_order_id="20260714002B", side="BUY")]
    validate_batch(h, orders)

    with pytest.raises(SchemaError):
        validate_batch(_header(order_count=3), orders)

    # 重复 client_order_id
    with pytest.raises(SchemaError):
        validate_batch(h, [_order(), _order()])

    # batch_id 不一致
    with pytest.raises(SchemaError):
        validate_batch(h, [_order(), _order(client_order_id="20260714002B", batch_id="other")])


def test_validate_batch_rejects_bad_mode_and_date():
    with pytest.raises(SchemaError):
        validate_batch(_header(mode="REAL"), [_order()])
    with pytest.raises(SchemaError):
        validate_batch(_header(trade_date="20260714"), [_order()])


# ---------- fill ----------

def test_validate_fill_requires_mode():
    f = FillEvent(
        batch_id="20260714_csi300_topk10_001",
        client_order_id="20260714001S",
        mode="LIVE",
        stock_code="600000.SH",
        side="SELL",
        status="FILLED",
        requested_qty=800,
        filled_qty=800,
        avg_price=10.45,
        qmt_order_id="1",
        message="",
        ts="2026-07-14T09:31:12+08:00",
    )
    validate_fill(f)

    import dataclasses
    with pytest.raises(SchemaError):
        validate_fill(dataclasses.replace(f, mode="PROD"))
    with pytest.raises(SchemaError):
        validate_fill(dataclasses.replace(f, status="DONE"))
    # EXPIRED 是合法终态
    validate_fill(dataclasses.replace(f, status="EXPIRED", filled_qty=0))
