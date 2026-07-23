"""pipeline_monitor：每条规则触发/不触发的边界。"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.pipeline_monitor import (
    check_account,
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


def _rules(findings):
    return [f.rule for f in findings]


# ---------- evening ----------

def test_evening_ok():
    assert check_evening("2026-07-14", BATCH, FILES_OK) == []


def test_evening_no_batch():
    f = check_evening("2026-07-14", None, [])
    assert _rules(f) == ["PUBLISH_MISSING"] and f[0].level == "CRIT"


def test_evening_missing_done_file():
    f = check_evening("2026-07-14", BATCH, [FILES_OK[0]])
    assert _rules(f) == ["PUBLISH_MISSING"]


def test_evening_inbox_unavailable():
    f = check_evening("2026-07-14", BATCH, None)
    assert _rules(f) == ["PUBLISH_MISSING"]
    assert "不可访问" in f[0].message


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
    recorder.supersede_batch(old, active)
    reconciled = []

    def fake_reconcile(_importer, batch_id):
        reconciled.append(batch_id)
        return {"planned": 10, "terminal": 10, "missing": 0}

    monkeypatch.setattr(run_monitor.FillImporter, "reconcile", fake_reconcile)
    findings = run_monitor.run_postmarket(
        "2026-07-15", recorder, store,
        {"live": {"bridge_root": str(tmp_path)}},
    )

    assert findings == []
    assert reconciled == [active]


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


def test_account_max_drawdown():
    snaps = [
        _snap("2026-07-10", 100.0, None),
        _snap("2026-07-11", 110.0, 0.10),
        _snap("2026-07-14", 98.0, -0.109),  # 峰值 110 回撤 -10.9%
    ]
    f = check_account(snaps, {"daily_loss": -0.5})
    assert _rules(f) == ["MAX_DRAWDOWN"] and f[0].level == "CRIT"


def test_account_consecutive_loss():
    snaps = [_snap("2026-07-13", 100.0, None)]
    snaps += [_snap(f"2026-07-{14+i}", 99.0 - i, -0.001) for i in range(5)]
    f = check_account(snaps, {"daily_loss": -0.5, "max_drawdown": -0.5})
    assert _rules(f) == ["CONSECUTIVE_LOSS"]
    # 窗口不足 N 天不触发
    f = check_account(snaps[:4], {"daily_loss": -0.5, "max_drawdown": -0.5,
                                  "consecutive_loss_days": 5})
    assert f == []


def test_account_empty():
    assert check_account([]) == []
