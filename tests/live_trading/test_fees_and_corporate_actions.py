"""fees 计算与 corporate_actions 分红入账（含幂等）。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.corporate_actions import apply_corporate_actions
from live_trading.modules.fees import (
    DEFAULT_FEES,
    fees_from_config,
    order_total_fee,
)
from live_trading.modules.fill_importer import LiveRecorder


# ---------- fees ----------

def test_buy_fee_no_stamp_duty():
    # 买入 10 万：佣金 25 + 过户费 1，无印花税
    fee = order_total_fee("BUY", 100000.0, DEFAULT_FEES)
    assert fee == pytest.approx(25.0 + 1.0)


def test_sell_fee_with_stamp_duty():
    # 卖出 10 万：佣金 25 + 过户费 1 + 印花税 50
    fee = order_total_fee("SELL", 100000.0, DEFAULT_FEES)
    assert fee == pytest.approx(25.0 + 1.0 + 50.0)


def test_min_commission():
    # 买入 1 万：佣金 2.5 < 5，按 5 收
    fee = order_total_fee("BUY", 10000.0, DEFAULT_FEES)
    assert fee == pytest.approx(5.0 + 0.1)


def test_zero_amount_no_fee():
    assert order_total_fee("BUY", 0.0, DEFAULT_FEES) == 0.0


def test_fees_from_config_merges_defaults():
    fees = fees_from_config({"fees": {"commission_rate": 0.0001}})
    assert fees["commission_rate"] == 0.0001
    assert fees["min_commission"] == DEFAULT_FEES["min_commission"]
    assert fees_from_config({}) == DEFAULT_FEES


# ---------- corporate actions ----------

@pytest.fixture
def recorder(tmp_path):
    return LiveRecorder(str(tmp_path / "live.db"))


def test_cash_dividend_with_tax(recorder):
    recorder.set_cash(10000.0)
    recorder.upsert_position("600036.SH", 1000, 30.0)
    events = [{"stock_code": "600036.SH", "cash_div_tax": 0.5, "stk_div": 0.0}]

    applied = apply_corporate_actions(recorder, "2026-07-14", events,
                                      dividend_tax_rate=0.20)
    assert len(applied) == 2  # DIVIDEND + DIVIDEND_TAX
    # 税前 500，预提 20% = 100，净入 400
    assert recorder.get_cash() == pytest.approx(10000.0 + 500.0 - 100.0)
    # 分红计入收益，不算外部出入金
    assert recorder.sum_external_flows("2026-07-14") == 0.0

    # 幂等：重复执行不重复入账
    assert apply_corporate_actions(recorder, "2026-07-14", events, 0.20) == []
    assert recorder.get_cash() == pytest.approx(10400.0)


def test_bonus_shares(recorder):
    recorder.set_cash(10000.0)
    recorder.upsert_position("600036.SH", 1000, 30.0)
    events = [{"stock_code": "600036.SH", "cash_div_tax": 0.0, "stk_div": 0.3}]

    applied = apply_corporate_actions(recorder, "2026-07-14", events, 0.20)
    assert applied == ["BONUS_SHARES 600036.SH +300股"]
    pos = recorder.get_positions()["600036.SH"]
    assert pos["shares"] == 1300
    assert pos["avg_cost"] == pytest.approx(30000.0 / 1300)
    assert recorder.get_cash() == pytest.approx(10000.0)  # 送股不动现金

    # 幂等
    assert apply_corporate_actions(recorder, "2026-07-14", events, 0.20) == []
    assert recorder.get_positions()["600036.SH"]["shares"] == 1300


def test_event_for_stock_not_held_is_ignored(recorder):
    recorder.set_cash(10000.0)
    events = [{"stock_code": "600036.SH", "cash_div_tax": 0.5, "stk_div": 0.0}]
    assert apply_corporate_actions(recorder, "2026-07-14", events, 0.20) == []
    assert recorder.get_cash() == pytest.approx(10000.0)


def test_dividend_and_bonus_combined(recorder):
    recorder.set_cash(0.0)
    recorder.upsert_position("000858.SZ", 500, 150.0)
    events = [{"stock_code": "000858.SZ", "cash_div_tax": 3.0, "stk_div": 0.5}]
    applied = apply_corporate_actions(recorder, "2026-07-14", events, 0.20)
    assert len(applied) == 3
    assert recorder.get_cash() == pytest.approx(1500.0 - 300.0)
    assert recorder.get_positions()["000858.SZ"]["shares"] == 750
