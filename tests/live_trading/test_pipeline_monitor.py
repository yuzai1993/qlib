"""pipeline_monitor：每条规则触发/不触发的边界。"""
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.pipeline_monitor import (
    DEFAULT_THRESHOLDS,
    check_account,
    check_broker_reconcile,
    check_evening,
    check_fill_ratio,
    check_netting_close,
    check_postmarket,
    check_probe_execution,
    check_report,
    weighted_fill_ratio,
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
          batch_id=BATCH["batch_id"], message="", netted_qty=0):
    return {"batch_id": batch_id, "mode": mode, "status": status, "side": side,
            "stock_code": code, "filled_qty": qty, "message": message,
            "netted_qty": netted_qty}


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


def test_a_fully_netted_ladder_day_is_not_an_all_skipped_incident():
    """三只票全额抵销：回执全 SKIPPED 且 filled_qty=0，但股数确实动了。"""
    fills = [
        _fill(status="SKIPPED", side="SELL", code="600000.SH", qty=0,
              netted_qty=300, message="netted against same-day buy"),
        _fill(status="SKIPPED", side="BUY", code="600000.SH", qty=0,
              netted_qty=300, message="netted against same-day due sell"),
    ]
    findings = check_postmarket(
        "2026-07-14",
        [{**BATCH, "mode": "LIVE", "planned_orders": 2}],
        {BATCH["batch_id"]: {"planned": 2, "terminal": 2, "missing": 0}},
        fills, prev_positions={"600000.SH": 300},
    )
    assert "ALL_ORDERS_SKIPPED" not in _rules(findings)


def test_all_skipped_with_nothing_netted_is_still_critical():
    """真的什么都没发生：既没成交也没抵销，仍须 CRIT。"""
    fills = [
        _fill(status="SKIPPED", code="600000.SH", qty=0,
              message="official close unavailable"),
        _fill(status="SKIPPED", code="000001.SZ", qty=0,
              message="official close unavailable"),
    ]
    findings = check_postmarket(
        "2026-07-14",
        [{**BATCH, "mode": "LIVE", "planned_orders": 2}],
        {BATCH["batch_id"]: {"planned": 2, "terminal": 2, "missing": 0}},
        fills, prev_positions={},
    )
    assert "ALL_ORDERS_SKIPPED" in _rules(findings)


def test_a_partially_netted_day_with_one_real_fill_is_not_an_incident():
    fills = [
        _fill(status="SKIPPED", side="SELL", code="600000.SH", qty=0,
              netted_qty=200, message="netted against same-day buy"),
        _fill(status="FILLED", side="BUY", code="600000.SH", qty=100),
    ]
    findings = check_postmarket(
        "2026-07-14",
        [{**BATCH, "mode": "LIVE", "planned_orders": 2}],
        {BATCH["batch_id"]: {"planned": 2, "terminal": 2, "missing": 0}},
        fills, prev_positions={"600000.SH": 200},
    )
    assert "ALL_ORDERS_SKIPPED" not in _rules(findings)


def _priced_fill(used, code="600000.SH"):
    fill = _fill(code=code)
    fill["netting_close"] = used
    return fill


def test_netting_close_matching_the_official_close_is_silent():
    assert check_netting_close(
        "2026-07-14", [_priced_fill(10.00)], {"600000.SH": 10.00},
    ) == []


def test_a_stale_netting_close_is_critical():
    """14:57 的冻结价与 15:00 定盘价不同，是静默错单，必须转成 CRIT。"""
    findings = check_netting_close(
        "2026-07-14", [_priced_fill(9.80)], {"600000.SH": 10.00},
    )
    assert "NETTING_CLOSE_MISMATCH" in _rules(findings)
    assert all(f.level == "CRIT" for f in findings)


def test_a_missing_official_close_is_critical_not_silent():
    """拿不到权威价就等于对不了账，不能当成对上了。"""
    findings = check_netting_close("2026-07-14", [_priced_fill(9.80)], {})
    assert "NETTING_CLOSE_UNVERIFIED" in _rules(findings)
    assert all(f.level == "CRIT" for f in findings)


def test_orders_not_priced_by_a_frozen_close_are_skipped():
    """netting_close == 0 表示本单不是按冻结价定量的（如 TopkDropout 批次）。"""
    assert check_netting_close("2026-07-14", [_priced_fill(0.0)], {}) == []


def test_float_noise_within_a_cent_does_not_alarm():
    assert check_netting_close(
        "2026-07-14", [_priced_fill(10.000004)], {"600000.SH": 10.0},
    ) == []


def test_a_high_priced_name_uses_the_relative_tolerance():
    """$close/$factor 的浮点误差随价格放大，绝对一分钱不够用。"""
    assert check_netting_close(
        "2026-07-14", [_priced_fill(800.03)], {"600000.SH": 800.0},
    ) == []


def test_a_non_positive_official_close_counts_as_unverified():
    findings = check_netting_close(
        "2026-07-14", [_priced_fill(10.0)], {"600000.SH": 0.0},
    )
    assert "NETTING_CLOSE_UNVERIFIED" in _rules(findings)


def test_every_mismatched_name_is_named_in_one_finding():
    fills = [_priced_fill(9.8), _priced_fill(20.0, code="000001.SZ")]
    findings = check_netting_close(
        "2026-07-14", fills, {"600000.SH": 10.0, "000001.SZ": 21.0},
    )
    message = next(f.message for f in findings
                   if f.rule == "NETTING_CLOSE_MISMATCH")
    assert "600000.SH" in message
    assert "000001.SZ" in message


def _lfill(side="BUY", intended=300, applied=300, netted=0, code="600000.SH",
           status="FILLED", netting_close=0.0):
    return {"batch_id": BATCH["batch_id"], "mode": "LIVE", "status": status,
            "side": side, "stock_code": code, "filled_qty": applied,
            "applied_qty": applied, "netted_qty": netted,
            "intended_qty": intended, "message": "",
            "netting_close": netting_close}


def test_a_fully_filled_day_is_one_hundred_percent():
    assert weighted_fill_ratio([_lfill()], "BUY") == 1.0


def test_netted_shares_count_as_filled():
    """抵销掉的股数确实进了仓位，只是没走市场。"""
    assert weighted_fill_ratio([_lfill(applied=0, netted=300)], "BUY") == 1.0


def test_the_ratio_is_weighted_by_intended_shares_not_by_order_count():
    fills = [_lfill(intended=1000, applied=1000),
             _lfill(intended=100, applied=0, code="000001.SZ")]
    assert weighted_fill_ratio(fills, "BUY") == pytest.approx(1000 / 1100)


def test_a_skipped_order_drags_the_ratio_down():
    """买不成就是欠配，不是「不算」。"""
    assert weighted_fill_ratio(
        [_lfill(applied=0, status="SKIPPED")], "BUY") == 0.0


def test_no_intent_on_a_side_is_unknown_not_zero():
    assert weighted_fill_ratio([_lfill(side="SELL")], "BUY") is None


def test_intended_qty_falls_back_to_requested_for_legacy_receipts():
    fill = _lfill()
    del fill["intended_qty"]
    fill["requested_qty"] = 300
    assert weighted_fill_ratio([fill], "BUY") == 1.0


def test_simulation_fills_do_not_count_towards_the_live_ratio():
    fill = _lfill(applied=0, status="SKIPPED")
    fill["mode"] = "SIMULATE"
    assert weighted_fill_ratio([fill], "BUY") is None


def test_the_ratio_never_exceeds_one_hundred_percent():
    """市场腿全成 + 抵销腿，分子不得超过本意股数。"""
    fills = [_lfill(intended=500, applied=200, netted=300)]
    assert weighted_fill_ratio(fills, "BUY") == 1.0


def test_a_healthy_run_of_days_is_silent():
    ratios = {
        "2026-07-10": {"BUY": 0.95, "SELL": 1.0},
        "2026-07-13": {"BUY": 1.0, "SELL": 1.0},
        "2026-07-14": {"BUY": 0.9, "SELL": 1.0},
    }
    assert check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS) == []


def test_a_single_day_below_the_hard_floor_is_critical():
    ratios = {"2026-07-14": {"BUY": 0.4, "SELL": 1.0}}
    findings = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_CRITICAL" in _rules(findings)
    assert all(f.level == "CRIT" for f in findings)


@pytest.mark.parametrize("ratio,expected", [
    (0.49, "FILL_RATIO_CRITICAL"),
    (0.50, "FILL_RATIO_LOW"),     # 「低于 50%」不含 50% 本身
    (0.79, "FILL_RATIO_LOW"),
])
def test_the_floors_are_exclusive_boundaries(ratio, expected):
    findings = check_fill_ratio(
        "2026-07-14", {"2026-07-14": {"BUY": ratio}}, DEFAULT_THRESHOLDS,
    )
    assert expected in _rules(findings)


def test_exactly_at_the_soft_floor_is_silent():
    assert check_fill_ratio(
        "2026-07-14", {"2026-07-14": {"BUY": 0.80}}, DEFAULT_THRESHOLDS,
    ) == []


def test_three_consecutive_days_below_the_soft_floor_trip_the_rollback():
    ratios = {
        "2026-07-10": {"BUY": 0.75, "SELL": 1.0},
        "2026-07-13": {"BUY": 0.7, "SELL": 1.0},
        "2026-07-14": {"BUY": 0.79, "SELL": 1.0},
    }
    findings = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_STREAK" in _rules(findings)
    assert any(f.level == "CRIT" for f in findings)


def test_a_streak_broken_by_a_good_day_does_not_trip():
    ratios = {
        "2026-07-10": {"BUY": 0.7, "SELL": 1.0},
        "2026-07-13": {"BUY": 0.95, "SELL": 1.0},
        "2026-07-14": {"BUY": 0.7, "SELL": 1.0},
    }
    findings = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_STREAK" not in _rules(findings)


def test_one_soft_breach_warns_without_tripping_the_rollback():
    ratios = {"2026-07-14": {"BUY": 0.7, "SELL": 1.0}}
    findings = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_LOW" in _rules(findings)
    assert "FILL_RATIO_STREAK" not in _rules(findings)


def test_a_short_history_cannot_trip_the_streak():
    """建仓期前两天历史不足，不能凑出连续三日。"""
    ratios = {"2026-07-13": {"BUY": 0.1, "SELL": 1.0},
              "2026-07-14": {"BUY": 0.1, "SELL": 1.0}}
    findings = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_STREAK" not in _rules(findings)


def test_an_unknown_buy_ratio_today_is_silent():
    ratios = {"2026-07-14": {"BUY": None, "SELL": 1.0}}
    assert check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS) == []


def test_a_gap_day_without_a_ratio_breaks_the_streak():
    """没有买入意图的那天不能当成「低于下限」凑进连续计数。"""
    ratios = {
        "2026-07-10": {"BUY": 0.7, "SELL": 1.0},
        "2026-07-13": {"BUY": None, "SELL": 1.0},
        "2026-07-14": {"BUY": 0.7, "SELL": 1.0},
    }
    findings = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_STREAK" not in _rules(findings)


def test_future_dates_do_not_leak_into_the_streak_window():
    ratios = {
        "2026-07-13": {"BUY": 0.7, "SELL": 1.0},
        "2026-07-14": {"BUY": 0.7, "SELL": 1.0},
        "2026-07-15": {"BUY": 0.7, "SELL": 1.0},
    }
    findings = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_STREAK" not in _rules(findings)


def _report_recorder(tmp_path, fills):
    """一个只够 run_report 跑通的账本：一个 LIVE 批次 + 直接落库的回执。

    这里不走 apply_fill：要验的是 run_report 的汇总与门禁接线，回执入库那条链路
    已由 test_fill_importer 覆盖。
    """
    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    store = MonitorStore(str(db))
    batch_id = "20260714_alla_v4_ladder_001"
    recorder.record_batch(batch_id, "2026-07-14", "LIVE", len(fills))
    with sqlite3.connect(db) as conn:
        for index, row in enumerate(fills):
            conn.execute(
                """INSERT INTO fills (batch_id, client_order_id, mode,
                       stock_code, side, status, requested_qty, filled_qty,
                       avg_price, qmt_order_id, message, ts, applied_qty,
                       applied_amount, applied_fee, netted_qty, netting_close,
                       intended_qty)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (batch_id, "coid%d" % index, row["mode"], row["stock_code"],
                 row["side"], row["status"], row["intended_qty"],
                 row["filled_qty"], 10.0, "", "", "t", row["applied_qty"],
                 0.0, 0.0, row["netted_qty"], row.get("netting_close", 0.0),
                 row["intended_qty"]),
            )
    return recorder, store


def _stub_report_boundaries(monkeypatch, closes):
    monkeypatch.setattr(run_monitor, "fetch_close_prices",
                        lambda codes, date: dict(closes))
    monkeypatch.setattr(run_monitor, "fetch_benchmark_close",
                        lambda benchmark, date: 4000.0)
    monkeypatch.setattr(run_monitor, "run_corporate_actions",
                        lambda date, recorder, store, config: ([], []))


class _Notifier:
    def __init__(self):
        self.bodies = []

    def send(self, title, body):
        self.bodies.append(body)
        return True


def test_run_report_publishes_the_weighted_fill_ratio(monkeypatch, tmp_path):
    """成交率是盘后固定价格通道的头号风险指标，必须真的出现在日报里。"""
    recorder, store = _report_recorder(tmp_path, [
        _lfill(side="BUY", intended=1000, applied=500),
        _lfill(side="SELL", intended=300, applied=300, code="000001.SZ"),
    ])
    _stub_report_boundaries(monkeypatch, {})
    notifier = _Notifier()

    findings = run_monitor.run_report(
        "2026-07-14", ["2026-07-14"], recorder, store,
        {"monitor": {"notify": {"daily_report": True}}}, notifier,
    )

    # 买入侧 500/1000 = 50%：正好在硬下限上，按「低于」的字面语义只 WARN
    assert "FILL_RATIO_LOW" in _rules(findings)
    assert notifier.bodies
    assert "**加权成交率** 买 50.0%　卖 100.0%" in notifier.bodies[0]


def test_run_report_flags_a_stale_sizing_close(monkeypatch, tmp_path):
    recorder, store = _report_recorder(tmp_path, [
        _lfill(side="BUY", intended=300, applied=300, netting_close=9.80),
    ])
    # 权威收盘价 10.00，bridge 用了 9.80：正是「读到 14:57 冻结价」的形状
    _stub_report_boundaries(monkeypatch, {"SH600000": 10.00})
    monkeypatch.setattr(run_monitor, "qmt_to_qlib", lambda code: "SH600000")

    findings = run_monitor.run_report(
        "2026-07-14", ["2026-07-14"], recorder, store,
        {"monitor": {"notify": {"daily_report": False}}}, _Notifier(),
    )

    assert "NETTING_CLOSE_MISMATCH" in _rules(findings)


def test_run_report_does_not_reconcile_orders_without_a_frozen_close(
    monkeypatch, tmp_path,
):
    """没按冻结价定量的批次不该触发对账，也不该去拉行情。"""
    recorder, store = _report_recorder(tmp_path, [
        _lfill(side="BUY", intended=300, applied=300),
    ])
    _stub_report_boundaries(monkeypatch, {})

    def explode(code):
        raise AssertionError("不该为无冻结价的批次做逐单对账")

    monkeypatch.setattr(run_monitor, "qmt_to_qlib", explode)

    findings = run_monitor.run_report(
        "2026-07-14", ["2026-07-14"], recorder, store,
        {"monitor": {"notify": {"daily_report": False}}}, _Notifier(),
    )

    assert "NETTING_CLOSE_MISMATCH" not in _rules(findings)
    assert "NETTING_CLOSE_UNVERIFIED" not in _rules(findings)


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
            "broker_environment": "SIMULATION",
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
            "broker_environment": "SIMULATION",
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
            "broker_environment": "SIMULATION",
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
    _make_snapshot_protocol_directories(tmp_path)
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
    ["list-error", "residue"],
)
def test_main_real_postmarket_scans_nested_probe_snapshot_root(
    tmp_path, monkeypatch, failure,
):
    main_root = tmp_path / "bridge"
    probe_root = main_root / "pr49_probe"
    _make_snapshot_protocol_directories(main_root)
    _make_snapshot_protocol_directories(probe_root)
    if failure == "residue":
        target = probe_root / "snapshot_requests" / "processing"
        (target / (
            "request_snapshot_20260808_"
            "0123456789abcdef0123456789abcdef.json"
        )).write_text("{}\n", encoding="utf-8")
    else:
        target = probe_root
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


def test_run_postmarket_skips_absent_snapshot_protocol(tmp_path):
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

    assert not [
        row for row in findings if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
    ]


def test_run_postmarket_skips_missing_nested_probe_snapshot_root(tmp_path):
    main_root = tmp_path / "bridge"
    _make_snapshot_protocol_directories(main_root)
    recorder = LiveRecorder(str(tmp_path / "live.db"))
    store = MonitorStore(str(tmp_path / "live.db"))

    findings = run_monitor.run_postmarket(
        "2026-08-08", recorder, store,
        _main_real_monitor_config(main_root),
    )

    assert not [
        row for row in findings
        if row.rule == "SNAPSHOT_RESIDUE_BLOCKED"
        and "pr49_probe" in row.message
    ]


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
    "inbox", "processing", "archive", "responses",
])
def test_run_postmarket_snapshot_path_must_be_directory(
    tmp_path, failure_point,
):
    bridge_root = tmp_path / "bridge"
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
    """费用未核准时 check_cash=False：现金和价值调整都不告警，持仓照查。"""
    findings = check_broker_reconcile(
        "2026-07-30", _account(302311.0), {"688223.SH": 244500},
        {"688223.SH": 44500}, -619730.0, check_cash=False,
    )
    assert _rules(findings) == ["BROKER_POSITION_MISMATCH"]

    assert check_broker_reconcile(
        "2026-07-30", _account(302311.0), {"688223.SH": 244500},
        {"688223.SH": 244500}, -619730.0, check_cash=False,
        ledger_value_adjustment=0.0,
        broker_position_market_values={"688223.SH": 1.0},
        value_tolerance=100.0,
    ) == []

    money_only = check_broker_reconcile(
        "2026-08-03",
        _account(9_949_714.06, market_value=-680_000.0),
        {},
        {},
        9_949_714.06,
        check_cash=False,
        ledger_value_adjustment=-681_126.98,
        broker_position_market_values={},
        value_tolerance=100.0,
    )
    assert money_only == []


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
