"""pipeline_monitor：每条规则触发/不触发的边界。"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.pipeline_monitor import (
    check_account,
    check_broker_reconcile,
    check_evening,
    check_postmarket,
    check_report,
)
from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.monitor_store import MonitorStore
from live_trading.scripts import run_monitor

BATCH = {"batch_id": "20260714_csi300_topk10_001", "trade_date": "2026-07-14"}
FILES_OK = ["signal_20260714_csi300_topk10_001.jsonl",
            "signal_20260714_csi300_topk10_001.done"]
CONFIG_ID = "csi1000_b6m_b2s_postclose"
PUBLISH_LOG = f"live_trading/logs/{CONFIG_ID}_publish_cron.log"


def _rules(findings):
    return [f.rule for f in findings]


def _set_batch_strategy(recorder, batch_id, strategy_id=CONFIG_ID):
    """Make direct batch fixtures match the durable publish-plan metadata."""
    with recorder._conn() as conn:
        conn.execute(
            "UPDATE batches SET strategy_id=? WHERE batch_id=?",
            (strategy_id, batch_id),
        )


def test_daily_report_discloses_nonzero_account_value_adjustment():
    snap = {
        "total_value": 9_268_587.08,
        "cash": 9_949_714.06,
        "account_value_adjustment": -681_126.98,
        "daily_return": None,
        "cumulative_return": 0.0,
        "excess_return": None,
        "position_count": 0,
        "turnover": None,
    }

    report = run_monitor._daily_report_md(
        "2026-08-03", snap, [], [], [],
    )

    assert "账户价值调整 -681,126.98" in report


# ---------- evening ----------

def test_evening_ok():
    assert check_evening("2026-07-14", BATCH, FILES_OK, CONFIG_ID) == []


def test_evening_no_batch():
    f = check_evening("2026-07-14", None, [], CONFIG_ID)
    assert _rules(f) == ["PUBLISH_MISSING"] and f[0].level == "CRIT"
    assert PUBLISH_LOG in f[0].message
    assert f"run_publish_catchup_cron.sh {CONFIG_ID}" in f[0].message


def test_evening_missing_done_file():
    f = check_evening("2026-07-14", BATCH, [FILES_OK[0]], CONFIG_ID)
    assert _rules(f) == ["PUBLISH_MISSING"]
    assert PUBLISH_LOG in f[0].message
    assert (
        f"run_publish_cron.sh {CONFIG_ID} 2026-07-14"
        in f[0].message
    )


def test_evening_inbox_unavailable():
    f = check_evening("2026-07-14", BATCH, None, CONFIG_ID)
    assert _rules(f) == ["PUBLISH_MISSING"]
    assert "不可访问" in f[0].message
    assert "先恢复 SMB 挂载" in f[0].message
    assert PUBLISH_LOG in f[0].message
    assert (
        f"run_publish_cron.sh {CONFIG_ID} 2026-07-14"
        in f[0].message
    )


# ---------- postmarket ----------

def _fill(status="FILLED", side="BUY", code="600000.SH", qty=100, mode="LIVE",
          batch_id=BATCH["batch_id"], message=""):
    return {"batch_id": batch_id, "mode": mode, "status": status, "side": side,
            "stock_code": code, "filled_qty": qty, "message": message}


def test_postmarket_ok():
    f = check_postmarket(
        "2026-07-14", [BATCH],
        {BATCH["batch_id"]: {"planned": 2, "terminal": 2, "missing": 0}},
        [_fill(), _fill(side="SELL", code="000001.SZ")],
        prev_positions={"000001.SZ": 100},
    )
    assert f == []


def test_postmarket_no_batches_silent():
    assert check_postmarket("2026-07-14", [], {}, [], {}) == []


def test_postmarket_missing_fills():
    f = check_postmarket(
        "2026-07-14", [BATCH],
        {BATCH["batch_id"]: {"planned": 3, "terminal": 1, "missing": 2}},
        [_fill()], {},
    )
    assert "FILLS_MISSING" in _rules(f)


def test_postmarket_batch_without_any_fill():
    f = check_postmarket(
        "2026-07-14", [BATCH],
        {BATCH["batch_id"]: {"planned": 3, "terminal": 0, "missing": 3}},
        [], {},
    )
    assert "FILLS_MISSING" in _rules(f)


def test_postmarket_zero_order_batch_is_terminal_without_fill_alert():
    findings = check_postmarket(
        "2026-07-14",
        [{**BATCH, "mode": "LIVE", "planned_orders": 0}],
        {BATCH["batch_id"]: {"planned": 0, "terminal": 0, "missing": 0}},
        [],
        prev_positions={},
    )

    assert "FILLS_MISSING" not in _rules(findings)
    assert "ALL_ORDERS_SKIPPED" not in _rules(findings)


def test_postmarket_reject_rate():
    fills = [_fill(status="REJECTED"), _fill(status="ERROR"),
             _fill(), _fill()]
    f = check_postmarket(
        "2026-07-14", [BATCH],
        {BATCH["batch_id"]: {"planned": 4, "terminal": 4, "missing": 0}},
        fills, {}, reject_rate=0.5,
    )
    assert "REJECT_RATE_HIGH" in _rules(f)
    # 1/4 < 0.5 不触发
    f = check_postmarket(
        "2026-07-14", [BATCH],
        {BATCH["batch_id"]: {"planned": 4, "terminal": 4, "missing": 0}},
        [_fill(status="REJECTED"), _fill(), _fill(), _fill()], {},
        reject_rate=0.5,
    )
    assert "REJECT_RATE_HIGH" not in _rules(f)


def test_postmarket_oversell_detected():
    fills = [_fill(side="SELL", code="600000.SH", qty=800)]
    f = check_postmarket(
        "2026-07-14", [BATCH],
        {BATCH["batch_id"]: {"planned": 1, "terminal": 1, "missing": 0}},
        fills, prev_positions={"600000.SH": 500},
    )
    assert "NEGATIVE_POSITION" in _rules(f)
    # SIMULATE 卖单不算
    fills = [_fill(side="SELL", code="600000.SH", qty=800, mode="SIMULATE")]
    f = check_postmarket(
        "2026-07-14", [BATCH],
        {BATCH["batch_id"]: {"planned": 1, "terminal": 1, "missing": 0}},
        fills, prev_positions={},
    )
    assert "NEGATIVE_POSITION" not in _rules(f)


def test_postmarket_oversell_skipped_without_baseline():
    fills = [_fill(side="SELL", code="600000.SH", qty=800)]
    f = check_postmarket(
        "2026-07-14", [BATCH],
        {BATCH["batch_id"]: {"planned": 1, "terminal": 1, "missing": 0}},
        fills, prev_positions=None,
    )
    assert "NEGATIVE_POSITION" not in _rules(f)


def test_postmarket_all_live_orders_skipped_is_critical():
    fills = [
        _fill(status="SKIPPED", qty=0, message="account unavailable"),
        _fill(status="SKIPPED", code="000001.SZ", qty=0,
              message="account unavailable"),
    ]
    findings = check_postmarket(
        "2026-07-15",
        [{**BATCH, "mode": "LIVE", "planned_orders": 2}],
        {BATCH["batch_id"]: {"planned": 2, "terminal": 2, "missing": 0}},
        fills, prev_positions={},
    )

    finding = next(f for f in findings if f.rule == "ALL_ORDERS_SKIPPED")
    assert finding.level == "CRIT"
    assert "account unavailable" in finding.message


def test_postmarket_live_fill_does_not_report_all_skipped():
    findings = check_postmarket(
        "2026-07-15",
        [{**BATCH, "mode": "LIVE", "planned_orders": 2}],
        {BATCH["batch_id"]: {"planned": 2, "terminal": 2, "missing": 0}},
        [_fill(), _fill(status="SKIPPED", code="000001.SZ", qty=0)],
        prev_positions={},
    )
    assert "ALL_ORDERS_SKIPPED" not in _rules(findings)


def test_run_postmarket_reconciles_only_active_batches(monkeypatch, tmp_path):
    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    store = MonitorStore(str(db))
    old = "20260715_csi300_topk10_001"
    active = "20260715_csi300_topk10_003"
    recorder.record_batch(old, "2026-07-15", "LIVE", 10)
    recorder.record_batch(active, "2026-07-15", "LIVE", 10)
    _set_batch_strategy(recorder, old)
    _set_batch_strategy(recorder, active)
    recorder.supersede_batch(old, active)
    reconciled = []

    def fake_reconcile(_importer, batch_id):
        reconciled.append(batch_id)
        return {"planned": 10, "terminal": 10, "missing": 0}

    monkeypatch.setattr(run_monitor.FillImporter, "reconcile", fake_reconcile)
    recorder.save_broker_snapshot(
        active, {"account_id": "1", "available_cash": 0.0}, [],
    )
    findings = run_monitor.run_postmarket(
        "2026-07-15", recorder, store,
        {"live": {
            "bridge_root": str(tmp_path),
            "strategy_id": CONFIG_ID,
        }},
    )

    assert findings == []
    assert reconciled == [active]


def test_run_postmarket_flags_missing_broker_snapshot(monkeypatch, tmp_path):
    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    store = MonitorStore(str(db))
    batch_id = "20260715_csi300_topk10_001"
    recorder.record_batch(batch_id, "2026-07-15", "LIVE", 1)
    _set_batch_strategy(recorder, batch_id)
    monkeypatch.setattr(
        run_monitor.FillImporter, "reconcile",
        lambda _self, _bid: {"planned": 1, "terminal": 1, "missing": 0},
    )

    findings = run_monitor.run_postmarket(
        "2026-07-15", recorder, store,
        {"live": {
            "bridge_root": str(tmp_path),
            "strategy_id": CONFIG_ID,
        }},
    )

    assert _rules(findings) == ["BROKER_SNAPSHOT_MISSING"]


def test_run_postmarket_skips_reconcile_for_simulate_batches(monkeypatch, tmp_path):
    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    store = MonitorStore(str(db))
    batch_id = "20260715_csi300_topk10_001"
    recorder.record_batch(batch_id, "2026-07-15", "SIMULATE", 1)
    _set_batch_strategy(recorder, batch_id)
    monkeypatch.setattr(
        run_monitor.FillImporter, "reconcile",
        lambda _self, _bid: {"planned": 1, "terminal": 1, "missing": 0},
    )

    findings = run_monitor.run_postmarket(
        "2026-07-15", recorder, store,
        {"live": {
            "bridge_root": str(tmp_path),
            "strategy_id": CONFIG_ID,
        }},
    )

    assert findings == []


# ---------- 二道对账 ----------

def _account(cash=1000.0, market_value=None):
    return {
        "account_id": "1",
        "available_cash": cash,
        "market_value": market_value,
    }


def test_broker_reconcile_ok():
    assert check_broker_reconcile(
        "2026-07-28", _account(1000.0), {"688223.SH": 244500},
        {"688223.SH": 244500}, 1000.0,
    ) == []


def test_broker_reconcile_detects_position_undercount():
    findings = check_broker_reconcile(
        "2026-07-28", _account(1000.0), {"688223.SH": 244500},
        {"688223.SH": 44500}, 1000.0,
    )
    finding = next(f for f in findings if f.rule == "BROKER_POSITION_MISMATCH")
    assert finding.level == "CRIT"
    assert "券商244500/账本44500" in finding.message


def test_broker_reconcile_detects_cash_gap_beyond_tolerance():
    findings = check_broker_reconcile(
        "2026-07-28", _account(1000.0), {}, {}, 1150.0, cash_tolerance=100.0,
    )
    finding = next(f for f in findings if f.rule == "BROKER_CASH_MISMATCH")
    assert finding.level == "CRIT"

    assert check_broker_reconcile(
        "2026-07-28", _account(1000.0), {}, {}, 1099.0, cash_tolerance=100.0,
    ) == []


def test_broker_reconcile_flags_negative_ledger_cash():
    findings = check_broker_reconcile(
        "2026-07-28", _account(-22560.37), {}, {}, -22560.37,
    )
    assert "CASH_NEGATIVE" in _rules(findings)
    assert next(f for f in findings if f.rule == "CASH_NEGATIVE").level == "CRIT"


def test_broker_reconcile_without_snapshot_warns_once():
    findings = check_broker_reconcile("2026-07-28", None, {}, {}, 1000.0)
    assert _rules(findings) == ["BROKER_SNAPSHOT_MISSING"]
    assert findings[0].level == "WARN"


def test_broker_reconcile_cash_check_disabled_only_checks_positions():
    """模拟盘现金口径不可信：check_cash=False 关闭现金类告警，持仓照查。"""
    findings = check_broker_reconcile(
        "2026-07-30", _account(302311.0), {"688223.SH": 244500},
        {"688223.SH": 44500}, -619730.0, check_cash=False,
    )
    assert _rules(findings) == ["BROKER_POSITION_MISMATCH"]

    assert check_broker_reconcile(
        "2026-07-30", _account(302311.0), {"688223.SH": 244500},
        {"688223.SH": 244500}, -619730.0, check_cash=False,
    ) == []


def test_broker_reconcile_tolerates_account_query_without_cash():
    """ACCOUNT 查询缺可用资金字段时只比持仓，不误报现金差额。"""
    findings = check_broker_reconcile(
        "2026-07-28", {"account_id": "1", "available_cash": None},
        {"600000.SH": 100}, {"600000.SH": 100}, 1000.0,
    )
    assert findings == []


def test_broker_reconcile_accepts_matching_negative_value_residual():
    findings = check_broker_reconcile(
        "2026-08-03",
        _account(9_949_714.06, market_value=-681_126.98),
        {},
        {},
        9_949_714.06,
        ledger_value_adjustment=-681_126.98,
        broker_position_market_values={},
        value_tolerance=100.0,
    )

    assert findings == []


def test_broker_reconcile_detects_value_adjustment_drift():
    findings = check_broker_reconcile(
        "2026-08-03",
        _account(9_949_714.06, market_value=-680_000.0),
        {},
        {},
        9_949_714.06,
        ledger_value_adjustment=-681_126.98,
        broker_position_market_values={},
        value_tolerance=100.0,
    )

    finding = next(
        f for f in findings if f.rule == "BROKER_VALUE_ADJUSTMENT_MISMATCH"
    )
    assert finding.level == "CRIT"
    assert "1126.98" in finding.message


def test_broker_reconcile_skips_value_check_when_position_value_missing():
    findings = check_broker_reconcile(
        "2026-08-03",
        _account(1_000.0, market_value=2_000.0),
        {"600000.SH": 100},
        {"600000.SH": 100},
        1_000.0,
        ledger_value_adjustment=0.0,
        broker_position_market_values={"600000.SH": None},
        value_tolerance=100.0,
    )

    assert findings == []


# ---------- report ----------

def test_report_data_stale():
    f = check_report("2026-07-14", "2026-07-13", [])
    assert _rules(f) == ["DATA_STALE"] and f[0].level == "CRIT"
    assert check_report("2026-07-14", "2026-07-14", []) == []


def test_report_price_missing():
    f = check_report("2026-07-14", "2026-07-14", ["600000.SH"])
    assert _rules(f) == ["PRICE_MISSING"] and f[0].level == "WARN"


# ---------- account ----------

def _snap(date, total, daily):
    return {"date": date, "total_value": total, "daily_return": daily}


def test_account_daily_loss_boundary():
    # 恰好等于阈值不触发（严格小于才触发，与 paper 一致）
    snaps = [_snap("2026-07-13", 100.0, None), _snap("2026-07-14", 97.0, -0.03)]
    assert check_account(snaps) == []
    snaps[-1]["daily_return"] = -0.031
    assert _rules(check_account(snaps)) == ["DAILY_LOSS"]


def test_account_does_not_alert_on_max_drawdown():
    """最大回撤只展示、不告警（短持有期策略回撤波动大，阈值告警噪音高）。"""
    snaps = [
        _snap("2026-07-10", 100.0, None),
        _snap("2026-07-11", 110.0, 0.10),
        _snap("2026-07-14", 98.0, -0.109),  # 峰值 110 回撤 -10.9%
    ]
    assert check_account(snaps, {"daily_loss": -0.5}) == []


def test_account_consecutive_loss():
    snaps = [_snap("2026-07-13", 100.0, None)]
    snaps += [_snap(f"2026-07-{14+i}", 99.0 - i, -0.001) for i in range(5)]
    f = check_account(snaps, {"daily_loss": -0.5})
    assert _rules(f) == ["CONSECUTIVE_LOSS"]
    # 窗口不足 N 天不触发
    f = check_account(snaps[:4], {"daily_loss": -0.5, "consecutive_loss_days": 5})
    assert f == []


def test_account_empty():
    assert check_account([]) == []
