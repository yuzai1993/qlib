"""QMT 内置桥接策略的纯逻辑测试（不依赖 QMT API）。

覆盖设计文档定稿的安全关键路径：
- 过期批次（trade_date != 当日）整批 SKIPPED
- 重复批次 SKIPPED duplicate
- checksum 不符整批拒绝
- 合法批次正确认领并按 priority 排序（先卖后买）
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.signal_schema import compute_checksum

BRIDGE_PATH = REPO_ROOT / "live_trading" / "qmt_strategy" / "qmt_signal_bridge.py"
BATCH_ID = "20260714_csi300_topk10_001"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("qmt_signal_bridge", BRIDGE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BRIDGE_ROOT = str(tmp_path)
    mod._ensure_dirs()
    mod._load_processed()
    return mod


def _order(coid="20260714001S", side="SELL", priority=10):
    return {
        "type": "order", "batch_id": BATCH_ID, "client_order_id": coid,
        "stock_code": "000001.SZ", "side": side, "quantity": 800,
        "price_type": "FIX", "limit_price": 10.41, "priority": priority,
        "instrument_qlib": "SZ000001", "reason": "test",
    }


def _write_batch(
    bridge, trade_date, orders, checksum=None, batch_id=BATCH_ID,
    mode="SIMULATE",
):
    order_lines = [json.dumps(o, sort_keys=True, separators=(",", ":")) for o in orders]
    if checksum is None:
        checksum = compute_checksum(order_lines)
    header = {
        "type": "batch_header", "schema_version": "1.0", "batch_id": batch_id,
        "strategy_id": "s", "trade_date": trade_date, "signal_date": trade_date,
        "account_id": "1", "account_type": "STOCK", "mode": mode,
        "created_at": "t", "order_count": len(orders), "checksum": checksum,
    }
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    jsonl = inbox / ("signal_%s.jsonl" % batch_id)
    jsonl.write_text(
        "\n".join([json.dumps(header, sort_keys=True)] + order_lines) + "\n")
    (inbox / ("signal_%s.done" % batch_id)).write_text(checksum + "\n")


def _read_fills(bridge, batch_id=BATCH_ID):
    p = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.jsonl" % batch_id)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_expired_batch_skipped(bridge):
    _write_batch(bridge, "2020-01-01", [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    fills = _read_fills(bridge)
    assert len(fills) == 1
    assert fills[0]["status"] == "SKIPPED"
    assert "expired" in fills[0]["message"]
    # done 已写出、batch 已登记 processed
    done = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.done" % BATCH_ID)
    assert done.exists()
    assert BATCH_ID in bridge.g.processed


def test_future_batch_left_in_inbox(bridge):
    """T-1 晚发布的次日信号不应被提前消费，留在 inbox 等到 trade_date 当天。"""
    _write_batch(bridge, "2099-12-31", [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    # 未认领：文件原地保留，无回执，未登记 processed
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    assert (inbox / ("signal_%s.jsonl" % BATCH_ID)).exists()
    assert (inbox / ("signal_%s.done" % BATCH_ID)).exists()
    assert _read_fills(bridge) == []
    assert BATCH_ID not in bridge.g.processed


def test_future_batch_claimed_on_trade_date(bridge):
    """到了 trade_date 当天，之前留在 inbox 的批次可正常认领。"""
    _write_batch(bridge, bridge._today(), [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is not None
    assert bridge.g.batch.batch_id() == BATCH_ID


def test_checksum_mismatch_rejected(bridge):
    _write_batch(bridge, bridge._today(), [_order()], checksum="sha256:deadbeef")
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    fills = _read_fills(bridge)
    assert fills and fills[0]["status"] == "SKIPPED"
    assert "checksum" in fills[0]["message"]


def test_duplicate_batch_skipped(bridge):
    bridge._mark_processed(BATCH_ID)
    _write_batch(bridge, bridge._today(), [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    fills = _read_fills(bridge)
    assert fills and "duplicate" in fills[0]["message"]


def test_valid_batch_claimed_sells_first(bridge):
    orders = [
        _order(coid="20260714002B", side="BUY", priority=20),
        _order(coid="20260714001S", side="SELL", priority=10),
    ]
    _write_batch(bridge, bridge._today(), orders)
    bridge._claim_new_batch()
    batch = bridge.g.batch
    assert batch is not None
    assert [o["side"] for o in batch.orders] == ["SELL", "BUY"]
    # 文件从 inbox 认领后保留在 processing，直到批次完成才归档
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    assert list(inbox.glob("*")) == []
    processing = Path(bridge.BRIDGE_ROOT) / "processing"
    assert len(list(processing.glob("signal_*"))) == 2
    archive = Path(bridge.BRIDGE_ROOT) / "archive"
    assert list(archive.glob("signal_*")) == []


def test_restart_recovers_active_processing_batch(bridge):
    _write_batch(bridge, bridge._today(), [_order()])
    bridge._claim_new_batch()
    batch = bridge.g.batch
    batch.phase = "BUY"
    batch.submitted[_order()["client_order_id"]] = True
    batch.remaining_cash = 1234.5
    bridge._save_active_state(batch)

    bridge.g.batch = None
    bridge._recover_processing_batch()

    recovered = bridge.g.batch
    assert recovered is not None
    assert recovered.phase == "BUY"
    assert recovered.remaining_cash == pytest.approx(1234.5)
    assert _order()["client_order_id"] in recovered.submitted


def test_restart_repairs_claim_interrupted_between_two_file_moves(bridge):
    _write_batch(bridge, bridge._today(), [_order()])
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    processing = Path(bridge.BRIDGE_ROOT) / "processing"
    jsonl_name = "signal_%s.jsonl" % BATCH_ID
    done_name = "signal_%s.done" % BATCH_ID
    (inbox / jsonl_name).rename(processing / jsonl_name)
    assert (inbox / done_name).exists()

    bridge._recover_processing_batch()

    assert bridge.g.batch is not None
    assert (processing / jsonl_name).exists()
    assert (processing / done_name).exists()
    assert not list(inbox.glob("signal_*"))


def test_max_affordable_quantity_includes_buy_fees(bridge):
    assert bridge._max_affordable_quantity(10000.0, 10.0, 1600) == 900
    assert bridge._max_affordable_quantity(1000.0, 10.0, 1600) == 0


def test_buy_phase_uses_one_cash_snapshot_and_reserves_between_orders(
    bridge, monkeypatch,
):
    first = _order(coid="20260714001001B", side="BUY", priority=20)
    second = _order(coid="20260714001002B", side="BUY", priority=20)
    first.update(quantity=800, limit_price=10.0)
    second.update(quantity=800, limit_price=10.0, stock_code="600000.SH")
    _write_batch(bridge, bridge._today(), [first, second], mode="LIVE")
    (Path(bridge.BRIDGE_ROOT) / "state" /
     ("LIVE_OK_" + bridge._today())).write_text("")
    bridge._claim_new_batch()
    bridge.TRADE_START = "00:00:00"
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:45:00")

    cash_reads = []
    monkeypatch.setattr(
        bridge, "_get_available_cash",
        lambda account_id: cash_reads.append(account_id) or 10000.0,
    )
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    submitted = []

    def fake_passorder(*args):
        submitted.append({"price": args[5], "quantity": args[6]})

    monkeypatch.setattr(bridge, "passorder", fake_passorder, raising=False)
    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert len(cash_reads) == 1
    assert [row["quantity"] for row in submitted] == [800, 100]
    assert sum(row["price"] * row["quantity"] for row in submitted) < 10000.0


def test_removing_live_switch_still_cancels_already_submitted_orders(
    bridge, monkeypatch,
):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1",
    }
    batch = bridge.Batch(header, [order])
    batch.submitted[order["client_order_id"]] = True
    batch.fills[order["client_order_id"]] = {
        "status": "ACCEPTED", "filled_qty": 0, "avg_price": 0.0,
    }

    class Detail:
        m_strOrderSysID = "qmt-order-1"
        m_nOrderStatus = -1
        m_nVolumeTraded = 0
        m_dTradedPrice = 0.0

    canceled = []
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:56:30")
    monkeypatch.setattr(
        bridge, "_get_orders_by_remark",
        lambda account_id: {order["client_order_id"]: Detail()},
    )
    monkeypatch.setattr(bridge, "can_cancel_order", lambda *args: True, raising=False)
    monkeypatch.setattr(
        bridge, "cancel", lambda *args: canceled.append(args), raising=False,
    )

    # There is intentionally no LIVE_OK file: it was removed after submission.
    bridge._force_finalize_if_near_close(object(), batch)

    assert len(canceled) == 1


def test_no_new_orders_are_submitted_after_cancel_cutoff(bridge, monkeypatch):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1",
    }
    batch = bridge.Batch(header, [order])
    batch.phase = "BUY"
    (Path(bridge.BRIDGE_ROOT) / "state" /
     ("LIVE_OK_" + bridge._today())).write_text("")
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:56:30")
    submitted = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: 100000.0)

    bridge._process_batch(_TickCtx(10.0), batch)

    assert submitted == []
    assert order["client_order_id"] not in batch.submitted


def test_no_done_no_claim(bridge):
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    (inbox / ("signal_%s.jsonl" % BATCH_ID)).write_text("{}\n")
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    # jsonl 仍留在 inbox（未消费）
    assert (inbox / ("signal_%s.jsonl" % BATCH_ID)).exists()


class _TickCtx:
    """Fake ContextInfo exposing get_full_tick."""
    def __init__(self, last_price):
        self._last = last_price

    def get_full_tick(self, codes):
        return {c: {"lastPrice": self._last} for c in codes}


def test_effective_price_buy_uses_realtime_within_limit(bridge):
    order = {"stock_code": "000001.SZ", "side": "BUY", "limit_price": 10.41}
    # last=10.00 -> 10.00*1.003=10.03, below mac limit 10.41
    assert bridge._effective_price(_TickCtx(10.00), order) == 10.03
    # last=10.50 -> 10.53 capped by mac limit 10.41
    assert bridge._effective_price(_TickCtx(10.50), order) == 10.41


def test_effective_price_sell_floor_and_fallback(bridge):
    order = {"stock_code": "000001.SZ", "side": "SELL", "limit_price": 9.90}
    # last=10.20 -> 10.20*0.997=10.17, above floor 9.90
    assert bridge._effective_price(_TickCtx(10.20), order) == 10.17
    # last=9.50 -> floored at mac limit 9.90 (won't chase a big drop)
    assert bridge._effective_price(_TickCtx(9.50), order) == 9.90
    # no quote -> fall back to mac limit
    assert bridge._effective_price(_TickCtx(0.0), order) == 9.90


def test_simulate_batch_processes_without_qmt_api(bridge, monkeypatch):
    """SIMULATE 模式下全流程不触碰 QMT API，直接产出 simulated 回执。"""
    class FakeCtx:
        def is_last_bar(self):
            return True

    bridge.TRADE_START = "00:00:00"  # 允许任何时间提交
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:45:00")
    _write_batch(bridge, bridge._today(), [
        _order(coid="20260714001S", side="SELL", priority=10),
        _order(coid="20260714002B", side="BUY", priority=20),
    ])
    bridge._claim_new_batch()
    batch = bridge.g.batch
    assert batch is not None
    bridge._process_batch(FakeCtx(), batch)
    # SIMULATE：两单都 SKIPPED simulated，批次终结
    fills = {f["client_order_id"]: f for f in _read_fills(bridge)}
    assert fills["20260714001S"]["status"] == "SKIPPED"
    assert fills["20260714001S"]["message"] == "simulated"
    assert fills["20260714002B"]["message"] == "simulated"
    assert bridge.g.batch is None  # finalized
    done = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.done" % BATCH_ID)
    assert done.exists()
    assert not list((Path(bridge.BRIDGE_ROOT) / "processing").glob("signal_*"))
    assert len(list((Path(bridge.BRIDGE_ROOT) / "archive").glob("signal_*"))) == 2
