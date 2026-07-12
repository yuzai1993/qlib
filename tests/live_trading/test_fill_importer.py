"""FillImporter：回执导入、SIMULATE 隔离、幂等、对账。"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.fill_importer import FillImporter, LiveRecorder

BATCH_ID = "20260714_csi300_topk10_001"


def _fill(client_order_id="20260714001S", mode="LIVE", status="FILLED",
          side="SELL", stock_code="000001.SZ", requested=800, filled=800,
          price=10.45):
    return {
        "type": "fill_event",
        "batch_id": BATCH_ID,
        "client_order_id": client_order_id,
        "mode": mode,
        "stock_code": stock_code,
        "side": side,
        "status": status,
        "requested_qty": requested,
        "filled_qty": filled,
        "avg_price": price,
        "qmt_order_id": "1001",
        "message": "",
        "ts": "2026-07-14T09:31:12+08:00",
    }


def _write_fills(bridge_root: Path, fills: list, batch_id=BATCH_ID, with_done=True):
    outbound = bridge_root / "outbound"
    outbound.mkdir(parents=True, exist_ok=True)
    jsonl = outbound / f"fills_{batch_id}.jsonl"
    jsonl.write_text(
        "\n".join(json.dumps(f, ensure_ascii=False) for f in fills) + "\n",
        encoding="utf-8",
    )
    if with_done:
        (outbound / f"fills_{batch_id}.done").write_text("ok\n", encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    db_path = tmp_path / "live.db"
    recorder = LiveRecorder(str(db_path))
    importer = FillImporter(tmp_path, recorder)
    return tmp_path, recorder, importer


def test_import_requires_done(env):
    bridge_root, recorder, importer = env
    _write_fills(bridge_root, [_fill()], with_done=False)
    assert importer.import_fills() == 0
    assert recorder.get_fills(BATCH_ID) == []


def test_live_filled_updates_positions(env):
    bridge_root, recorder, importer = env
    # 账簿统一用 QMT 格式 stock_code；预置持仓，卖出后减少
    recorder.upsert_position("000001.SZ", 800, 10.0)
    _write_fills(bridge_root, [
        _fill(),  # SELL 800 @10.45
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", requested=500, filled=500, price=10.10),
    ])
    n = importer.import_fills()
    assert n == 2

    positions = recorder.get_positions()
    assert "000001.SZ" not in positions  # 全部卖出后清仓
    assert positions["600000.SH"]["shares"] == 500


def test_simulate_fills_do_not_touch_positions(env):
    bridge_root, recorder, importer = env
    _write_fills(bridge_root, [
        _fill(mode="SIMULATE", client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", requested=500, filled=500),
    ])
    importer.import_fills()
    assert recorder.get_positions() == {}
    # 但 fills 表中有记录（用于链路验证）
    fills = recorder.get_fills(BATCH_ID)
    assert len(fills) == 1
    assert fills[0]["mode"] == "SIMULATE"


def test_reimport_is_idempotent(env):
    bridge_root, recorder, importer = env
    _write_fills(bridge_root, [
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", requested=500, filled=500),
    ])
    importer.import_fills()
    # 再写同一批次同内容（模拟重复投递），持仓不能翻倍
    _write_fills(bridge_root, [
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", requested=500, filled=500),
    ])
    importer.import_fills()
    assert recorder.get_positions()["600000.SH"]["shares"] == 500


def test_non_terminal_and_rejected_do_not_change_positions(env):
    bridge_root, recorder, importer = env
    recorder.upsert_position("000001.SZ", 800, 10.0)
    _write_fills(bridge_root, [
        _fill(status="ACCEPTED", filled=0),
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", status="REJECTED", requested=500, filled=0),
    ])
    importer.import_fills()
    positions = recorder.get_positions()
    assert positions["000001.SZ"]["shares"] == 800
    assert "600000.SH" not in positions


def test_partial_fill_updates_by_filled_qty(env):
    bridge_root, recorder, importer = env
    _write_fills(bridge_root, [
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", status="PARTIAL", requested=500, filled=200),
    ])
    importer.import_fills()
    assert recorder.get_positions()["600000.SH"]["shares"] == 200


def test_cash_updated_by_live_fills_only(env):
    bridge_root, recorder, importer = env
    recorder.set_cash(100000.0)
    recorder.upsert_position("000001.SZ", 800, 10.0)
    _write_fills(bridge_root, [
        _fill(),  # LIVE SELL 800 @10.45 -> +8360
        _fill(client_order_id="20260714002B", side="BUY", mode="SIMULATE",
              stock_code="600000.SH", requested=500, filled=500, price=10.10),
    ])
    importer.import_fills()
    assert recorder.get_cash() == pytest.approx(100000.0 + 800 * 10.45)
    # 重复导入现金不重复累计
    _write_fills(bridge_root, [_fill()])
    importer.import_fills()
    assert recorder.get_cash() == pytest.approx(100000.0 + 800 * 10.45)


def test_reconcile_counts(env):
    bridge_root, recorder, importer = env
    recorder.record_batch(BATCH_ID, "2026-07-14", "LIVE", planned_orders=3)
    _write_fills(bridge_root, [
        _fill(),
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", status="REJECTED", requested=500, filled=0),
    ])
    importer.import_fills()
    result = importer.reconcile(BATCH_ID)
    assert result["planned"] == 3
    assert result["terminal"] == 2
    assert result["missing"] == 1
