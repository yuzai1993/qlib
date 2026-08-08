"""pipeline_monitor：每条规则触发/不触发的边界。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.pipeline_monitor import (
    check_account,
    check_broker_reconcile,
    check_evening,
    check_postmarket,
    check_probe_execution,
    check_report,
)
from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.execution_state import ExecutionStateError
from live_trading.modules.monitor_store import MonitorStore
from live_trading.scripts import run_monitor

BATCH = {"batch_id": "20260714_csi300_topk10_001", "trade_date": "2026-07-14"}
FILES_OK = ["signal_20260714_csi300_topk10_001.jsonl",
            "signal_20260714_csi300_topk10_001.done"]
CONFIG_ID = "csi1000_b6m_b2s_postclose"
PUBLISH_LOG = f"live_trading/logs/{CONFIG_ID}_publish_cron.log"


def _rules(findings):
    return [f.rule for f in findings]


PROBE_STRATEGY_ID = "csi1000_pr49_one_lot_probe"
PROBE_BATCH = {
    "batch_id": "20260810_csi1000_pr49_one_lot_probe_001",
    "trade_date": "2026-08-10",
    "strategy_id": PROBE_STRATEGY_ID,
    "mode": "LIVE",
    "planned_orders": 1,
}
PROBE_ORDER = {
    "client_order_id": "20260810001001B",
    "stock_code": "688001.SH",
    "side": "BUY",
}
PROBE_LOG = "/Volumes/qmt_bridge/pr49_probe/logs/qmt_events_2026-08-10.jsonl"


def _probe_findings(**overrides):
    values = {
        "main_authorized": False,
        "probe_authorized": False,
        "probe_batch": PROBE_BATCH,
        "probe_orders": [PROBE_ORDER],
        "probe_fills": [],
        "broker_account": {"batch_id": PROBE_BATCH["batch_id"]},
        "broker_positions": {},
        "lifecycle": {
            "strategy_id": PROBE_STRATEGY_ID,
            "stock_code": "688001.SH",
            "buy_batch_id": PROBE_BATCH["batch_id"],
            "buy_trade_date": "2026-08-10",
            "sell_batch_id": None,
            "sell_trade_date": None,
            "state": "BUY_PLANNED",
        },
        "qmt_events": [],
        "event_log_path": PROBE_LOG,
        "main_marker_path": "/Volumes/qmt_bridge/state/LIVE_OK_2026-08-10",
        "probe_marker_path": (
            "/Volumes/qmt_bridge/pr49_probe/state/PR49_LIVE_OK_2026-08-10"
        ),
        "main_execution_state": "PAUSED",
    }
    values.update(overrides)
    return check_probe_execution("2026-08-10", **values)


def _assert_complete_probe_evidence(finding, *, batch_id=None, stock=None):
    assert finding.level == "CRIT"
    for token in (
        "date=2026-08-10",
        f"strategy_id={PROBE_STRATEGY_ID}",
        f"batch_id={batch_id or PROBE_BATCH['batch_id']}",
        f"stock={stock or PROBE_ORDER['stock_code']}",
        "expected=",
        "observed=",
        f"log={PROBE_LOG}",
    ):
        assert token in finding.message


def test_probe_monitor_blocks_dual_authorization_with_complete_evidence():
    findings = _probe_findings(main_authorized=True, probe_authorized=True)

    finding = next(f for f in findings if f.rule == "DUAL_AUTHORIZATION")
    _assert_complete_probe_evidence(finding)
    assert "LIVE_OK_2026-08-10" in finding.message
    assert "PR49_LIVE_OK_2026-08-10" in finding.message


@pytest.mark.parametrize(
    "probe_state",
    [
        {
            "probe_authorized": True, "probe_batch": None,
            "probe_orders": [], "lifecycle": None,
        },
        {"probe_authorized": False},
    ],
)
def test_probe_monitor_flags_active_main_for_authorized_or_planned_probe(
    probe_state,
):
    findings = _probe_findings(
        main_execution_state="ACTIVE", **probe_state,
    )

    finding = next(f for f in findings if f.rule == "PROBE_MAIN_NOT_PAUSED")
    _assert_complete_probe_evidence(
        finding,
        batch_id=(
            "NONE" if probe_state.get("probe_batch", PROBE_BATCH) is None
            else PROBE_BATCH["batch_id"]
        ),
        stock=(
            "NONE" if probe_state.get("probe_batch", PROBE_BATCH) is None
            else PROBE_ORDER["stock_code"]
        ),
    )
    assert "main execution state=ACTIVE" in finding.message


def test_probe_monitor_flags_passorder_without_observed_qmt_order():
    findings = _probe_findings(qmt_events=[
        {
            "event": "PASSORDER_ATTEMPT",
            "batch_id": PROBE_BATCH["batch_id"],
            "client_order_id": PROBE_ORDER["client_order_id"],
        },
        {
            "event": "ORDER_FINALIZED",
            "batch_id": PROBE_BATCH["batch_id"],
            "client_order_id": PROBE_ORDER["client_order_id"],
            "fill_status": "ERROR",
            "reason": "QMT order not observed after passorder",
        },
    ])

    finding = next(
        f for f in findings if f.rule == "PROBE_ORDER_NOT_OBSERVED"
    )
    _assert_complete_probe_evidence(finding)
    assert "ORDER_OBSERVED absent" in finding.message
    assert "final=ERROR" in finding.message


@pytest.mark.parametrize(
    "qmt_order_ids",
    [None, [], [""], ["  "], [{}], [True], [1], {"id": "qmt-1"}, "qmt-1"],
)
def test_probe_monitor_does_not_trust_observed_event_without_real_order_id(
    qmt_order_ids,
):
    observed = {
        "event": "ORDER_OBSERVED",
        "batch_id": PROBE_BATCH["batch_id"],
        "client_order_id": PROBE_ORDER["client_order_id"],
    }
    if qmt_order_ids is not None:
        observed["qmt_order_ids"] = qmt_order_ids
    findings = _probe_findings(qmt_events=[
        {
            "event": "PASSORDER_ATTEMPT",
            "batch_id": PROBE_BATCH["batch_id"],
            "client_order_id": PROBE_ORDER["client_order_id"],
        },
        observed,
    ])

    finding = next(
        row for row in findings if row.rule == "PROBE_ORDER_NOT_OBSERVED"
    )
    _assert_complete_probe_evidence(finding)


def test_probe_monitor_accepts_observed_event_with_real_order_id():
    findings = _probe_findings(qmt_events=[
        {
            "event": "PASSORDER_ATTEMPT",
            "batch_id": PROBE_BATCH["batch_id"],
            "client_order_id": PROBE_ORDER["client_order_id"],
        },
        {
            "event": "ORDER_OBSERVED",
            "batch_id": PROBE_BATCH["batch_id"],
            "client_order_id": PROBE_ORDER["client_order_id"],
            "qmt_order_ids": ["qmt-real-101"],
        },
    ])

    assert "PROBE_ORDER_NOT_OBSERVED" not in _rules(findings)


def test_probe_monitor_requires_account_wide_broker_snapshot():
    findings = _probe_findings(broker_account=None)

    finding = next(f for f in findings if f.rule == "PROBE_SNAPSHOT_MISSING")
    _assert_complete_probe_evidence(finding)
    assert "ACCOUNT snapshot" in finding.message


@pytest.mark.parametrize(
    ("side", "filled_qty", "broker_shares", "lifecycle", "expected"),
    [
        ("BUY", 100, 0, "BUY_PLANNED", "broker_shares=100"),
        ("SELL", 100, 100, "SELL_PLANNED", "broker_shares=0"),
    ],
)
def test_probe_monitor_flags_position_drift_even_when_main_paused(
    side, filled_qty, broker_shares, lifecycle, expected,
):
    batch = dict(PROBE_BATCH)
    order = {**PROBE_ORDER, "side": side}
    state = {
        "strategy_id": PROBE_STRATEGY_ID,
        "stock_code": order["stock_code"],
        "buy_batch_id": batch["batch_id"],
        "buy_trade_date": "2026-08-09" if side == "SELL" else "2026-08-10",
        "sell_batch_id": batch["batch_id"] if side == "SELL" else None,
        "sell_trade_date": "2026-08-10" if side == "SELL" else None,
        "state": lifecycle,
    }
    fill = {
        **order,
        "batch_id": batch["batch_id"],
        "status": "FILLED",
        "filled_qty": filled_qty,
        "mode": "LIVE",
    }

    findings = _probe_findings(
        probe_batch=batch,
        probe_orders=[order],
        probe_fills=[fill],
        broker_positions={order["stock_code"]: broker_shares},
        lifecycle=state,
        main_execution_state="PAUSED",
    )

    finding = next(f for f in findings if f.rule == "PROBE_POSITION_DRIFT")
    _assert_complete_probe_evidence(finding)
    assert expected in finding.message


def _terminal_probe_case(side, status, filled_qty, broker_shares, state):
    order = {**PROBE_ORDER, "side": side}
    lifecycle = {
        "strategy_id": PROBE_STRATEGY_ID,
        "stock_code": order["stock_code"],
        "buy_batch_id": (
            PROBE_BATCH["batch_id"]
            if side == "BUY" else "20260809_csi1000_pr49_one_lot_probe_001"
        ),
        "buy_trade_date": "2026-08-10" if side == "BUY" else "2026-08-09",
        "sell_batch_id": PROBE_BATCH["batch_id"] if side == "SELL" else None,
        "sell_trade_date": "2026-08-10" if side == "SELL" else None,
        "state": state,
    }
    fill = {
        **order,
        "batch_id": PROBE_BATCH["batch_id"],
        "status": status,
        "filled_qty": filled_qty,
        "mode": "LIVE",
    }
    return _probe_findings(
        probe_orders=[order],
        probe_fills=[fill],
        broker_positions={order["stock_code"]: broker_shares},
        lifecycle=lifecycle,
    )


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("status", ["FILLED", "PARTIAL"])
def test_probe_monitor_flags_zero_quantity_traded_terminal_once(side, status):
    broker_shares = 0 if side == "BUY" else 100

    findings = _terminal_probe_case(
        side, status, filled_qty=0, broker_shares=broker_shares, state="FAILED",
    )

    assert _rules(findings) == ["PROBE_POSITION_DRIFT"]
    finding = findings[0]
    _assert_complete_probe_evidence(finding)
    assert "expected=filled_qty=100" in finding.message
    assert f"observed=filled_qty=0 broker_shares={broker_shares}" \
        in finding.message


@pytest.mark.parametrize(
    ("side", "broker_shares", "state"),
    [("BUY", 100, "BUY_FILLED"), ("SELL", 0, "CLOSED")],
)
def test_probe_monitor_accepts_normal_one_lot_terminal(
    side, broker_shares, state,
):
    assert _terminal_probe_case(
        side, "FILLED", filled_qty=100,
        broker_shares=broker_shares, state=state,
    ) == []


@pytest.mark.parametrize(
    ("side", "broker_shares"),
    [("BUY", 40), ("SELL", 60)],
)
def test_probe_monitor_flags_nonzero_partial_fill_once(side, broker_shares):
    findings = _terminal_probe_case(
        side, "PARTIAL", filled_qty=40,
        broker_shares=broker_shares, state="FAILED",
    )

    assert _rules(findings) == ["PROBE_POSITION_DRIFT"]
    assert "observed=filled_qty=40" in findings[0].message


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_probe_monitor_does_not_call_untraded_failure_position_drift(side):
    broker_shares = 0 if side == "BUY" else 100

    findings = _terminal_probe_case(
        side, "ERROR", filled_qty=0,
        broker_shares=broker_shares, state="FAILED",
    )

    assert findings == []


def test_probe_monitor_rejects_lifecycle_bound_to_another_batch():
    state = {
        "strategy_id": PROBE_STRATEGY_ID,
        "stock_code": "688001.SH",
        "buy_batch_id": "20260809_csi1000_pr49_one_lot_probe_999",
        "buy_trade_date": "2026-08-09",
        "sell_batch_id": None,
        "sell_trade_date": None,
        "state": "BUY_PLANNED",
    }

    findings = _probe_findings(lifecycle=state)

    finding = next(f for f in findings if f.rule == "PROBE_LIFECYCLE_INVALID")
    _assert_complete_probe_evidence(finding)
    assert "lifecycle batch binding" in finding.message


def test_probe_monitor_rejects_buy_lifecycle_trade_date_mismatch():
    state = {
        "strategy_id": PROBE_STRATEGY_ID,
        "stock_code": "688001.SH",
        "buy_batch_id": PROBE_BATCH["batch_id"],
        "buy_trade_date": "2026-08-09",
        "sell_batch_id": None,
        "sell_trade_date": None,
        "state": "BUY_PLANNED",
    }

    findings = _probe_findings(lifecycle=state)

    finding = next(f for f in findings if f.rule == "PROBE_LIFECYCLE_INVALID")
    _assert_complete_probe_evidence(finding)
    assert "buy_trade_date=2026-08-10" in finding.message


@pytest.mark.parametrize(
    ("buy_trade_date", "sell_trade_date"),
    [
        ("2026-08-09", "2026-08-09"),
        ("2026-08-11", "2026-08-10"),
        ("2026-08-09", "2026-08-11"),
    ],
)
def test_probe_monitor_rejects_sell_lifecycle_date_or_chronology_error(
    buy_trade_date, sell_trade_date,
):
    sell_order = {**PROBE_ORDER, "side": "SELL"}
    state = {
        "strategy_id": PROBE_STRATEGY_ID,
        "stock_code": "688001.SH",
        "buy_batch_id": "20260809_csi1000_pr49_one_lot_probe_001",
        "buy_trade_date": buy_trade_date,
        "sell_batch_id": PROBE_BATCH["batch_id"],
        "sell_trade_date": sell_trade_date,
        "state": "SELL_PLANNED",
    }

    findings = _probe_findings(probe_orders=[sell_order], lifecycle=state)

    finding = next(f for f in findings if f.rule == "PROBE_LIFECYCLE_INVALID")
    _assert_complete_probe_evidence(finding)
    assert "sell lifecycle dates" in finding.message


def test_run_probe_checks_reads_authorizations_from_authoritative_state_dirs(
    tmp_path,
):
    recorder = LiveRecorder(str(tmp_path / "shared.db"))
    main_root = tmp_path / "bridge"
    probe_root = main_root / "pr49_probe"
    (main_root / "state").mkdir(parents=True)
    (probe_root / "state").mkdir(parents=True)
    main_marker = main_root / "state" / "LIVE_OK_2026-08-10"
    probe_marker = probe_root / "state" / "PR49_LIVE_OK_2026-08-10"
    main_marker.write_text("", encoding="utf-8")
    probe_marker.write_text("", encoding="utf-8")

    findings = run_monitor._run_probe_checks(
        "2026-08-10",
        recorder,
        {"live": {
            "bridge_root": str(main_root),
            "strategy_id": CONFIG_ID,
            "broker_environment": "REAL",
        }},
    )

    finding = next(row for row in findings if row.rule == "DUAL_AUTHORIZATION")
    assert str(main_marker) in finding.message
    assert str(probe_marker) in finding.message


def test_run_probe_checks_rejects_unresolved_authorization_intent(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "shared.db"))
    main_root = tmp_path / "bridge"
    probe_root = main_root / "pr49_probe"
    (main_root / "state").mkdir(parents=True)
    (probe_root / "state").mkdir(parents=True)
    intent = (
        probe_root / "state" /
        "PR49_LIVE_OK_2026-08-10.intent.deadbeef.tmp"
    )
    intent.write_text("uncommitted", encoding="utf-8")

    findings = run_monitor._run_probe_checks(
        "2026-08-10",
        recorder,
        {"live": {
            "bridge_root": str(main_root),
            "strategy_id": CONFIG_ID,
            "broker_environment": "REAL",
        }},
    )

    finding = next(
        row for row in findings
        if row.rule == "AUTHORIZATION_INTENT_REMAINS"
    )
    assert finding.level == "CRIT"
    assert str(intent) in finding.message


def test_probe_config_monitor_uses_authoritative_main_strategy_id(
    tmp_path, monkeypatch,
):
    recorder = LiveRecorder(str(tmp_path / "shared.db"))
    recorder.set_execution_state(
        "csi1000_b6m_b2s_postclose_real", "ACTIVE", "actual main active",
        "2026-08-10T15:31:00+08:00",
    )
    recorder.set_execution_state(
        "paused_decoy", "PAUSED", "decoy paused",
        "2026-08-10T15:31:01+08:00",
    )
    captured = {}

    def capture_probe_state(_date, **kwargs):
        captured["main_execution_state"] = kwargs["main_execution_state"]
        return []

    monkeypatch.setattr(run_monitor, "check_probe_execution", capture_probe_state)
    probe_root = tmp_path / "bridge" / "pr49_probe"
    run_monitor._run_probe_checks(
        "2026-08-10", recorder,
        {"live": {
            "kind": "OPERATOR_PROBE",
            "bridge_root": str(probe_root),
            "strategy_id": PROBE_STRATEGY_ID,
            "main_strategy_id": "paused_decoy",
            "broker_environment": "REAL",
        }},
    )

    assert captured["main_execution_state"] == "ACTIVE"


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


def test_evening_paused_with_current_audit_preview_is_ok():
    assert check_evening(
        "2026-07-14", None, [], CONFIG_ID,
        execution_state={"state": "PAUSED"},
        audit_preview={"trade_date": "2026-07-14"},
    ) == []


def test_evening_paused_with_stale_audit_preview_warns():
    findings = check_evening(
        "2026-07-14", None, [], CONFIG_ID,
        execution_state={"state": "PAUSED"},
        audit_preview={"trade_date": "2026-07-13"},
    )

    assert _rules(findings) == ["PAUSED_PREVIEW_MISSING"]
    assert findings[0].level == "WARN"


def test_audit_preview_loader_rejects_unsafe_strategy_path():
    with pytest.raises(ExecutionStateError, match="safe identifier"):
        run_monitor._load_audit_preview("../other", "2026-07-14")


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
    _make_snapshot_protocol_directories(tmp_path)
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
    _make_snapshot_protocol_directories(tmp_path)

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
    _make_snapshot_protocol_directories(tmp_path)

    findings = run_monitor.run_postmarket(
        "2026-07-15", recorder, store,
        {"live": {
            "bridge_root": str(tmp_path),
            "strategy_id": CONFIG_ID,
        }},
    )

    assert findings == []


def test_run_postmarket_surfaces_persistent_snapshot_residue_error(
    monkeypatch, tmp_path,
):
    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    store = MonitorStore(str(db))
    status_path = tmp_path / "snapshot_requests" / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(
        '{"state":"ERROR","blocking":true,'
        '"classification":"INVALID_RESIDUE",'
        '"artifacts":["processing/request_snapshot_x.json"]}\n',
        encoding="utf-8",
    )

    findings = run_monitor.run_postmarket(
        "2026-08-08", recorder, store,
        {"live": {
            "bridge_root": str(tmp_path),
            "strategy_id": CONFIG_ID,
        }},
    )

    finding = next(
        row for row in findings if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
    )
    assert finding.level == "CRIT"
    assert "INVALID_RESIDUE" in finding.message
    assert "processing/request_snapshot_x.json" in finding.message


@pytest.mark.parametrize("status_payload", [
    None,
    '{"state":"CLEAR","blocking":false,"classification":"CLEAR",'
    '"artifacts":[]}\n',
])
def test_run_postmarket_detects_live_snapshot_residue_without_error_status(
    monkeypatch, tmp_path, status_payload,
):
    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    store = MonitorStore(str(db))
    request_root = tmp_path / "snapshot_requests"
    processing = request_root / "processing"
    processing.mkdir(parents=True)
    residue = (
        processing /
        "request_snapshot_20260808_0123456789abcdef0123456789abcdef.json"
    )
    residue.write_text("{}\n", encoding="utf-8")
    if status_payload is not None:
        (request_root / "status.json").write_text(
            status_payload, encoding="utf-8",
        )

    findings = run_monitor.run_postmarket(
        "2026-08-08", recorder, store,
        {"live": {
            "bridge_root": str(tmp_path),
            "strategy_id": CONFIG_ID,
        }},
    )

    finding = next(
        row for row in findings if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
    )
    assert finding.level == "CRIT"
    assert "processing/request_snapshot_" in finding.message


def test_run_postmarket_detects_shared_snapshot_advance_gate_without_status(
    tmp_path,
):
    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    store = MonitorStore(str(db))
    gate = tmp_path / "state" / "SNAPSHOT_ORDER_ADVANCE.lock"
    gate.parent.mkdir(parents=True)
    gate.write_text(
        '{"owner":"QMT_ORDER_ADVANCE","created_at":"stale"}\n',
        encoding="utf-8",
    )

    findings = run_monitor.run_postmarket(
        "2026-08-08", recorder, store,
        {"live": {
            "bridge_root": str(tmp_path),
            "strategy_id": CONFIG_ID,
        }},
    )

    finding = next(
        row for row in findings if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
    )
    assert finding.level == "CRIT"
    assert "state/SNAPSHOT_ORDER_ADVANCE.lock" in finding.message


def _make_snapshot_protocol_directories(bridge_root):
    for name in ("inbox", "processing", "archive", "responses"):
        (bridge_root / "snapshot_requests" / name).mkdir(
            parents=True, exist_ok=True,
        )


def _main_real_monitor_config(bridge_root):
    return {"live": {
        "bridge_root": str(bridge_root),
        "strategy_id": "csi1000_b6m_b2s_postclose_real",
        "broker_environment": "REAL",
    }}


@pytest.mark.parametrize(
    "failure",
    ["missing-root", "not-directory", "list-error", "residue"],
)
def test_main_real_postmarket_scans_nested_probe_snapshot_root(
    tmp_path, monkeypatch, failure,
):
    main_root = tmp_path / "bridge"
    probe_root = main_root / "pr49_probe"
    _make_snapshot_protocol_directories(main_root)
    target = probe_root
    if failure == "missing-root":
        pass
    elif failure == "not-directory":
        target.write_text("not a directory\n", encoding="utf-8")
    else:
        _make_snapshot_protocol_directories(probe_root)
        if failure == "residue":
            target = probe_root / "snapshot_requests" / "processing"
            (target / (
                "request_snapshot_20260808_"
                "0123456789abcdef0123456789abcdef.json"
            )).write_text("{}\n", encoding="utf-8")
        else:
            original_iterdir = Path.iterdir

            def fail_probe_root(path):
                if path == probe_root:
                    raise OSError("simulated nested SMB list failure")
                return original_iterdir(path)

            monkeypatch.setattr(Path, "iterdir", fail_probe_root)
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    store = MonitorStore(str(tmp_path / "live.db"))

    findings = run_monitor.run_postmarket(
        "2026-08-08", recorder, store,
        _main_real_monitor_config(main_root),
    )

    finding = next(
        row for row in findings if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
        and str(probe_root) in row.message
    )
    assert finding.level == "CRIT"
    if failure == "residue":
        assert "processing/request_snapshot_" in finding.message
    else:
        assert str(target) in finding.message


def test_run_postmarket_snapshot_protocol_empty_directories_are_clear(tmp_path):
    bridge_root = tmp_path / "bridge"
    _make_snapshot_protocol_directories(bridge_root)
    _make_snapshot_protocol_directories(bridge_root / "pr49_probe")
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    store = MonitorStore(str(tmp_path / "live.db"))

    findings = run_monitor.run_postmarket(
        "2026-08-08", recorder, store,
        {"live": {
            "bridge_root": str(bridge_root),
            "strategy_id": CONFIG_ID,
            "broker_environment": "REAL",
        }},
    )

    assert not [
        row for row in findings if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
    ]


def test_run_postmarket_missing_snapshot_bridge_root_is_critical(tmp_path):
    bridge_root = tmp_path / "missing-bridge"
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    store = MonitorStore(str(tmp_path / "live.db"))

    findings = run_monitor.run_postmarket(
        "2026-08-08", recorder, store,
        {"live": {
            "bridge_root": str(bridge_root),
            "strategy_id": CONFIG_ID,
            "broker_environment": "REAL",
        }},
    )

    finding = next(
        row for row in findings if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
    )
    assert finding.level == "CRIT"
    assert str(bridge_root) in finding.message
    assert "expected=directory" in finding.message
    assert "observed=missing" in finding.message


@pytest.mark.parametrize("missing", [
    "inbox", "processing", "archive", "responses",
])
def test_run_postmarket_missing_snapshot_directory_is_critical(
    tmp_path, missing,
):
    bridge_root = tmp_path / "bridge"
    _make_snapshot_protocol_directories(bridge_root)
    (bridge_root / "snapshot_requests" / missing).rmdir()
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    store = MonitorStore(str(tmp_path / "live.db"))

    findings = run_monitor.run_postmarket(
        "2026-08-08", recorder, store,
        {"live": {
            "bridge_root": str(bridge_root),
            "strategy_id": CONFIG_ID,
            "broker_environment": "REAL",
        }},
    )

    finding = next(
        row for row in findings if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
    )
    assert finding.level == "CRIT"
    assert f"snapshot_requests/{missing}" in finding.message
    assert "expected=directory" in finding.message
    assert "observed=missing" in finding.message


@pytest.mark.parametrize("failure_point", [
    "bridge", "inbox", "processing", "archive", "responses",
])
def test_run_postmarket_snapshot_path_must_be_directory(
    tmp_path, failure_point,
):
    bridge_root = tmp_path / "bridge"
    if failure_point == "bridge":
        bridge_root.write_text("not a directory\n", encoding="utf-8")
        target = bridge_root
    else:
        _make_snapshot_protocol_directories(bridge_root)
        target = bridge_root / "snapshot_requests" / failure_point
        target.rmdir()
        target.write_text("not a directory\n", encoding="utf-8")
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    store = MonitorStore(str(tmp_path / "live.db"))

    findings = run_monitor.run_postmarket(
        "2026-08-08", recorder, store,
        {"live": {
            "bridge_root": str(bridge_root),
            "strategy_id": CONFIG_ID,
            "broker_environment": "REAL",
        }},
    )

    finding = next(
        row for row in findings if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
    )
    assert finding.level == "CRIT"
    assert str(target) in finding.message
    assert "expected=directory" in finding.message
    assert "observed=not-directory" in finding.message


@pytest.mark.parametrize("failure_point", ["bridge", "responses"])
def test_run_postmarket_unreadable_snapshot_path_is_critical(
    monkeypatch, tmp_path, failure_point,
):
    bridge_root = tmp_path / "bridge"
    _make_snapshot_protocol_directories(bridge_root)
    target = (
        bridge_root if failure_point == "bridge" else
        bridge_root / "snapshot_requests" / "responses"
    )
    original_iterdir = Path.iterdir

    def fail_selected_path(path):
        if path == target:
            raise OSError("simulated SMB list failure")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_selected_path)
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    store = MonitorStore(str(tmp_path / "live.db"))

    findings = run_monitor.run_postmarket(
        "2026-08-08", recorder, store,
        {"live": {
            "bridge_root": str(bridge_root),
            "strategy_id": CONFIG_ID,
            "broker_environment": "REAL",
        }},
    )

    finding = next(
        row for row in findings if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
    )
    assert finding.level == "CRIT"
    assert str(target) in finding.message
    assert "expected=readable-directory" in finding.message
    assert "observed=list-error" in finding.message


def test_run_postmarket_passes_only_current_strategy_fills_to_rules(
    monkeypatch, tmp_path,
):
    recorder = LiveRecorder(str(tmp_path / "shared.db"))
    store = MonitorStore(str(tmp_path / "shared.db"))
    main_batch = "20260810_csi1000_b6m_b2s_postclose_real_001"
    probe_batch = PROBE_BATCH["batch_id"]
    recorder.record_batch(main_batch, "2026-08-10", "SIMULATE", 1)
    recorder.record_batch(probe_batch, "2026-08-10", "SIMULATE", 1)
    _set_batch_strategy(recorder, main_batch)
    _set_batch_strategy(recorder, probe_batch, PROBE_STRATEGY_ID)
    monkeypatch.setattr(
        run_monitor.FillImporter,
        "reconcile",
        lambda _self, _batch_id: {"planned": 1, "terminal": 1, "missing": 0},
    )
    monkeypatch.setattr(
        recorder,
        "get_fills",
        lambda batch_id: [{"batch_id": batch_id, "status": "FILLED"}],
    )
    captured = {}

    def capture(_date, batches, _reconciles, fills, _positions, **_kwargs):
        captured["batches"] = [row["batch_id"] for row in batches]
        captured["fills"] = [row["batch_id"] for row in fills]
        return []

    monkeypatch.setattr(run_monitor, "check_postmarket", capture)
    _make_snapshot_protocol_directories(tmp_path / "bridge")

    findings = run_monitor.run_postmarket(
        "2026-08-10",
        recorder,
        store,
        {"live": {
            "bridge_root": str(tmp_path / "bridge"),
            "strategy_id": CONFIG_ID,
            "broker_environment": "SIMULATION",
        }},
    )

    assert findings == []
    assert captured == {"batches": [main_batch], "fills": [main_batch]}


def test_dispatch_sends_serverchan_the_exact_probe_evidence(tmp_path):
    finding = next(
        row for row in _probe_findings(
            main_authorized=True,
            probe_authorized=True,
        )
        if row.rule == "DUAL_AUTHORIZATION"
    )
    sent = []

    class CapturingNotifier:
        channel = "serverchan"

        def send(self, title, content):
            sent.append((title, content))
            return True

    store = MonitorStore(str(tmp_path / "monitor.db"))

    run_monitor.dispatch_findings(
        [finding], "postmarket", "2026-08-10", store, CapturingNotifier(),
    )

    assert sent == [(
        "[实盘CRIT] DUAL_AUTHORIZATION 2026-08-10",
        finding.message,
    )]


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
