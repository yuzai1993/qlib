"""fees 计算与 corporate_actions 分红入账（含幂等）。"""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.corporate_actions import fetch_dividend_events
from live_trading.modules.fees import (
    DEFAULT_FEES,
    fees_from_config,
    order_total_fee,
)
from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.monitor_store import MonitorStore


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


@pytest.mark.parametrize("side", ["HOLD", "", None])
def test_order_fee_rejects_unknown_side(side):
    with pytest.raises(ValueError, match="side"):
        order_total_fee(side, 1000.0, DEFAULT_FEES)


@pytest.mark.parametrize("amount", [-1.0, float("nan"), float("inf")])
def test_order_fee_rejects_invalid_amount(amount):
    with pytest.raises(ValueError, match="cum_amount"):
        order_total_fee("BUY", amount, DEFAULT_FEES)


def test_fees_from_config_rejects_negative_or_non_finite_rates():
    with pytest.raises(ValueError, match="commission_rate"):
        fees_from_config({"fees": {"commission_rate": -0.1}})
    with pytest.raises(ValueError, match="min_commission"):
        fees_from_config({"fees": {"min_commission": float("nan")}})


# ---------- corporate actions ----------

@pytest.fixture
def recorder(tmp_path):
    return LiveRecorder(str(tmp_path / "live.db"))


def _event(**overrides):
    event = {
        "event_key": "600036.SH_20251231_20260714_20260715",
        "stock_code": "600036.SH",
        "end_date": "2025-12-31",
        "record_date": "2026-07-14",
        "ex_date": "2026-07-15",
        "pay_date": "2026-07-16",
        "div_listdate": "2026-07-17",
        "cash_div_tax": 0.5,
        "stk_div": 0.1,
    }
    event.update(overrides)
    return event


class FakeDividendPro:
    def dividend(self, **kwargs):
        self.kwargs = kwargs
        return pd.DataFrame([{
            "ts_code": "600036.SH", "div_proc": "实施",
            "cash_div_tax": 0.5, "stk_div": 0.1,
            "record_date": "20260714", "ex_date": "20260715",
            "pay_date": "20260716", "div_listdate": "20260717",
            "end_date": "20251231",
        }])


def test_fetch_dividend_events_includes_record_pay_and_list_dates():
    pro = FakeDividendPro()
    events = fetch_dividend_events("2026-07-15", pro=pro)
    assert events == [_event()]
    assert pro.kwargs["ex_date"] == "20260715"


def test_missing_settlement_dates_do_not_fall_back_to_ex_date(recorder):
    pro = FakeDividendPro()
    original = pro.dividend

    def dividend(**kwargs):
        df = original(**kwargs)
        df.loc[0, "pay_date"] = None
        df.loc[0, "div_listdate"] = None
        return df

    pro.dividend = dividend
    event = fetch_dividend_events("2026-07-15", pro=pro)[0]
    assert event["pay_date"] == ""
    assert event["div_listdate"] == ""

    recorder.accrue_corporate_action(event, 1000, 0.20)
    assert recorder.settle_due_corporate_actions("2099-12-31") == []
    assert recorder.get_corporate_balances()["receivables"] == pytest.approx(500.0)
    assert recorder.get_corporate_balances()["pending_shares"] == {
        "600036.SH": 100,
    }


def test_dividend_accrual_creates_receivable_and_tax_provision_not_cash(recorder):
    recorder.set_cash(10000.0)
    assert recorder.accrue_corporate_action(_event(), 1000, 0.20)
    assert recorder.get_cash() == pytest.approx(10000.0)
    assert recorder.get_corporate_balances() == {
        "receivables": pytest.approx(500.0),
        "tax_provision": pytest.approx(100.0),
        "pending_shares": {"600036.SH": 100},
    }
    # Re-running the ex-date stage is idempotent.
    assert not recorder.accrue_corporate_action(_event(), 1000, 0.20)
    assert recorder.get_corporate_actions()[0]["event_key"] == _event()["event_key"]


def test_pay_and_bonus_list_dates_settle_separately_and_idempotently(recorder):
    recorder.set_cash(10000.0)
    recorder.upsert_position("600036.SH", 1000, 30.0)
    recorder.accrue_corporate_action(_event(), 1000, 0.20)

    assert recorder.settle_due_corporate_actions("2026-07-15") == []
    paid = recorder.settle_due_corporate_actions("2026-07-16")
    assert paid == ["DIVIDEND 600036.SH +500.00"]
    assert recorder.get_cash() == pytest.approx(10500.0)
    assert recorder.get_positions()["600036.SH"]["shares"] == 1000
    assert recorder.get_corporate_balances()["receivables"] == 0.0

    listed = recorder.settle_due_corporate_actions("2026-07-17")
    assert listed == ["BONUS_SHARES 600036.SH +100股"]
    pos = recorder.get_positions()["600036.SH"]
    assert pos["shares"] == 1100
    assert pos["avg_cost"] == pytest.approx(30000.0 / 1100)
    assert recorder.settle_due_corporate_actions("2026-07-17") == []
    assert recorder.get_corporate_balances()["tax_provision"] == pytest.approx(100.0)


def test_actual_dividend_tax_settles_provision_without_double_count(recorder):
    recorder.set_cash(10000.0)
    recorder.accrue_corporate_action(_event(), 1000, 0.20)
    recorder.settle_due_corporate_actions("2026-07-16")

    assert recorder.settle_dividend_tax(
        _event()["event_key"], "2026-08-01", actual_tax=50.0,
    )
    assert recorder.get_cash() == pytest.approx(10450.0)
    assert recorder.get_corporate_balances()["tax_provision"] == 0.0
    assert not recorder.settle_dividend_tax(
        _event()["event_key"], "2026-08-01", actual_tax=50.0,
    )
    tax_rows = [
        row for row in recorder.get_cash_flows()
        if row["flow_type"] == "DIVIDEND_TAX"
    ]
    assert len(tax_rows) == 1
    assert tax_rows[0]["amount"] == pytest.approx(-50.0)


def test_record_date_snapshot_entitles_ex_date_seller(tmp_path, monkeypatch):
    from live_trading.scripts import run_monitor

    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    recorder.set_cash(10000.0)
    store = MonitorStore(str(db))
    store.upsert_daily_snapshot({
        "date": "2026-07-14", "cash": 10000.0, "market_value": 30000.0,
        "total_value": 40000.0, "daily_return": None, "cumulative_return": 0.0,
        "benchmark_close": 4000.0, "benchmark_daily_return": None,
        "benchmark_cumulative_return": 0.0, "excess_return": None,
        "position_count": 1, "turnover": 0.0,
    })
    store.upsert_position_snapshots("2026-07-14", [{
        "stock_code": "600036.SH", "shares": 1000, "avg_cost": 30.0,
        "close_price": 30.0, "market_value": 30000.0, "profit": 0.0,
        "weight": 0.75,
    }])
    # Current recorder has no position: all original shares were sold on ex-date.
    monkeypatch.setattr(run_monitor, "fetch_dividend_events", lambda date: [_event()])
    applied, findings = run_monitor.run_corporate_actions(
        "2026-07-15", recorder, store,
        {"fees": {"dividend_tax_rate": 0.20}},
    )
    assert findings == []
    assert applied == [
        "DIVIDEND_RECEIVABLE 600036.SH +500.00; TAX_PROVISION -100.00; "
        "PENDING_BONUS +100股"
    ]
    assert recorder.get_corporate_balances()["receivables"] == pytest.approx(500.0)


def test_record_date_entitlement_is_not_hidden_by_newer_other_position(
        tmp_path, monkeypatch):
    from live_trading.scripts import run_monitor

    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    store = MonitorStore(str(db))
    for date in ("2026-07-14", "2026-07-15"):
        store.upsert_daily_snapshot({
            "date": date, "cash": 10000.0, "market_value": 30000.0,
            "total_value": 40000.0, "daily_return": None,
            "cumulative_return": 0.0, "benchmark_close": 4000.0,
            "benchmark_daily_return": None, "benchmark_cumulative_return": 0.0,
            "excess_return": None, "position_count": 1, "turnover": 0.0,
        })
    store.upsert_position_snapshots("2026-07-14", [{
        "stock_code": "600036.SH", "shares": 1000, "avg_cost": 30.0,
        "close_price": 30.0, "market_value": 30000.0, "profit": 0.0,
        "weight": 0.75,
    }])
    store.upsert_position_snapshots("2026-07-15", [{
        "stock_code": "000001.SZ", "shares": 1000, "avg_cost": 10.0,
        "close_price": 10.0, "market_value": 10000.0, "profit": 0.0,
        "weight": 0.25,
    }])

    monkeypatch.setattr(run_monitor, "fetch_dividend_events", lambda date: [_event()])
    applied, findings = run_monitor.run_corporate_actions(
        "2026-07-16", recorder, store,
        {"fees": {"dividend_tax_rate": 0.20}},
    )

    assert findings == []
    assert applied[0].startswith("DIVIDEND_RECEIVABLE 600036.SH +500.00")


def test_missing_record_date_snapshot_warns_instead_of_guessing(tmp_path, monkeypatch):
    from live_trading.scripts import run_monitor

    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    recorder.upsert_position("600036.SH", 1000, 30.0)
    store = MonitorStore(str(db))
    monkeypatch.setattr(run_monitor, "fetch_dividend_events", lambda date: [_event()])
    applied, findings = run_monitor.run_corporate_actions(
        "2026-07-15", recorder, store,
        {"fees": {"dividend_tax_rate": 0.20}},
    )
    assert applied == []
    assert [f.rule for f in findings] == ["CORP_ACTION_ENTITLEMENT_MISSING"]
    assert recorder.get_corporate_balances()["receivables"] == 0.0


def test_entitled_event_with_missing_settlement_dates_stays_pending(
        tmp_path, monkeypatch):
    from live_trading.scripts import run_monitor

    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    store = MonitorStore(str(db))
    store.upsert_daily_snapshot({
        "date": "2026-07-14", "cash": 10000.0, "market_value": 30000.0,
        "total_value": 40000.0, "daily_return": None, "cumulative_return": 0.0,
        "benchmark_close": 4000.0, "benchmark_daily_return": None,
        "benchmark_cumulative_return": 0.0, "excess_return": None,
        "position_count": 1, "turnover": 0.0,
    })
    store.upsert_position_snapshots("2026-07-14", [{
        "stock_code": "600036.SH", "shares": 1000, "avg_cost": 30.0,
        "close_price": 30.0, "market_value": 30000.0, "profit": 0.0,
        "weight": 0.75,
    }])
    monkeypatch.setattr(
        run_monitor, "fetch_dividend_events",
        lambda date: [_event(pay_date="", div_listdate="")],
    )

    applied, findings = run_monitor.run_corporate_actions(
        "2026-07-15", recorder, store,
        {"fees": {"dividend_tax_rate": 0.20}},
    )

    assert len(applied) == 1
    assert {f.rule for f in findings} == {
        "CORP_ACTION_SETTLEMENT_DATE_MISSING",
        "CORP_ACTION_LIST_DATE_MISSING",
    }
    assert recorder.settle_due_corporate_actions("2099-12-31") == []
