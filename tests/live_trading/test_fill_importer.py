"""FillImporter：回执导入、SIMULATE 隔离、幂等、对账。"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.fees import DEFAULT_FEES, order_total_fee
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


def test_record_and_get_orders(env):
    _, recorder, _ = env
    recorder.record_batch(BATCH_ID, "2026-07-14", "SIMULATE", 2)
    recorder.record_orders(BATCH_ID, [
        {
            "client_order_id": "20260714001S",
            "stock_code": "000001.SZ",
            "instrument_qlib": "SZ000001",
            "side": "SELL",
            "quantity": 800,
            "price_type": "FIX",
            "limit_price": 10.0,
            "priority": 10,
            "reason": "topk_dropout",
        },
        {
            "client_order_id": "20260714002B",
            "stock_code": "600000.SH",
            "instrument_qlib": "SH600000",
            "side": "BUY",
            "quantity": 500,
            "price_type": "FIX",
            "limit_price": 11.0,
            "priority": 20,
            "reason": "topk_dropout",
        },
    ])
    orders = recorder.get_orders(BATCH_ID)
    assert len(orders) == 2
    assert orders[0]["side"] == "SELL"  # priority 升序
    assert orders[1]["stock_code"] == "600000.SH"
    # 重跑覆盖不翻倍
    recorder.record_orders(BATCH_ID, orders[:1])
    assert len(recorder.get_orders(BATCH_ID)) == 1


def test_stock_names_roundtrip(env):
    _, recorder, _ = env
    recorder.save_stock_names([
        {"stock_code": "600000.SH", "instrument": "SH600000", "name": "浦发银行"},
    ])
    assert recorder.get_stock_names()["600000.SH"] == "浦发银行"



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
        _fill(),  # LIVE SELL 800 @10.45 -> +8360，另扣费用
        _fill(client_order_id="20260714002B", side="BUY", mode="SIMULATE",
              stock_code="600000.SH", requested=500, filled=500, price=10.10),
    ])
    importer.import_fills()
    # 卖出 8360：佣金 max(8360*0.00025, 5)=5 + 过户费 0.0836 + 印花税 4.18
    sell_fee = order_total_fee("SELL", 8360.0, DEFAULT_FEES)
    assert sell_fee == pytest.approx(5 + 0.0836 + 4.18)
    expected = 100000.0 + 800 * 10.45 - sell_fee
    assert recorder.get_cash() == pytest.approx(expected)
    # 重复导入现金/费用均不重复累计
    _write_fills(bridge_root, [_fill()])
    importer.import_fills()
    assert recorder.get_cash() == pytest.approx(expected)
    fill_row = recorder.get_fills(BATCH_ID)[0]
    assert fill_row["applied_fee"] == pytest.approx(sell_fee)


def test_partial_then_full_fee_incremental(env):
    """部分成交后终态补齐：最低佣金全订单只收一次，费用按增量补扣。"""
    bridge_root, recorder, importer = env
    recorder.set_cash(100000.0)
    _write_fills(bridge_root, [
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", status="PARTIAL",
              requested=500, filled=200, price=10.0),
    ])
    importer.import_fills()
    fee_200 = order_total_fee("BUY", 2000.0, DEFAULT_FEES)  # 佣金触发最低 5 元
    assert recorder.get_cash() == pytest.approx(100000.0 - 2000.0 - fee_200)

    _write_fills(bridge_root, [
        _fill(client_order_id="20260714002B", side="BUY",
              stock_code="600000.SH", status="FILLED",
              requested=500, filled=500, price=10.0),
    ])
    importer.import_fills()
    fee_500 = order_total_fee("BUY", 5000.0, DEFAULT_FEES)
    assert recorder.get_cash() == pytest.approx(100000.0 - 5000.0 - fee_500)
    fill_row = recorder.get_fills(BATCH_ID)[0]
    assert fill_row["applied_fee"] == pytest.approx(fee_500)


def test_cash_flow_record_and_dedup(env):
    _, recorder, _ = env
    recorder.set_cash(100000.0)
    ok = recorder.record_cash_flow("2026-07-14", "DEPOSIT", 50000.0,
                                   note="追加资金", dedup_key="DEP_1")
    assert ok
    assert recorder.get_cash() == pytest.approx(150000.0)
    # 同 dedup_key 不重复入账
    assert not recorder.record_cash_flow("2026-07-14", "DEPOSIT", 50000.0,
                                         dedup_key="DEP_1")
    assert recorder.get_cash() == pytest.approx(150000.0)

    recorder.record_cash_flow("2026-07-14", "WITHDRAW", -20000.0)
    recorder.record_cash_flow("2026-07-14", "DIVIDEND", 380.0,
                              stock_code="600036.SH")
    assert recorder.get_cash() == pytest.approx(130380.0)
    # 外部出入金净额不含分红
    assert recorder.sum_external_flows("2026-07-14") == pytest.approx(30000.0)
    assert len(recorder.get_cash_flows()) == 3


def test_apply_bonus_shares(env):
    _, recorder, _ = env
    recorder.upsert_position("600036.SH", 1000, 30.0)
    assert recorder.apply_bonus_shares("600036.SH", 300)
    pos = recorder.get_positions()["600036.SH"]
    assert pos["shares"] == 1300
    assert pos["avg_cost"] == pytest.approx(30.0 * 1000 / 1300)
    # 无持仓返回 False
    assert not recorder.apply_bonus_shares("000001.SZ", 100)


def test_sum_fees_by_date(env):
    bridge_root, recorder, importer = env
    recorder.record_batch(BATCH_ID, "2026-07-14", "LIVE", 1)
    recorder.set_cash(100000.0)
    recorder.upsert_position("000001.SZ", 800, 10.0)
    _write_fills(bridge_root, [_fill()])
    importer.import_fills()
    sell_fee = order_total_fee("SELL", 8360.0, DEFAULT_FEES)
    assert recorder.sum_fees_by_date("2026-07-14") == pytest.approx(sell_fee)
    assert recorder.sum_fees_by_date("2026-07-13") == 0.0


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
