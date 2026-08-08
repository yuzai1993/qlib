#!/usr/bin/env python3
"""实盘监控：流程健康检查、每日快照、告警与日报推送。

用法（三个 stage 对应三个 cron 时点，见 live_trading/README.md）：
    python live_trading/scripts/run_monitor.py \
        --config csi1000_b6m_b2s_postclose \
        --stage {postmarket,report,evening} [--date YYYY-MM-DD]

退出码：0 全部 OK；1 有 WARN；2 有 CRIT/FAIL。
设计文档：docs/superpowers/specs/2026-07-13-live-monitor-platform-design.md
"""

import argparse
import json
import logging
import re
import sys
from datetime import date as _date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.code_map import qmt_to_qlib
from live_trading.modules.corporate_actions import (
    fetch_dividend_events,
)
from live_trading.modules.fees import fees_from_config
from live_trading.modules.fill_importer import FillImporter, LiveRecorder
from live_trading.modules.execution_state import validate_identifier
from live_trading.modules.live_config import load_live_config
from live_trading.modules.monitor_store import MonitorStore
from live_trading.modules.notifier import create_notifier
from live_trading.modules.operator_probe import (
    MAIN_STRATEGY_ID,
    PROBE_STRATEGY_ID,
    SNAPSHOT_ADVANCE_GATE_NAME,
    snapshot_artifact_checksum,
)
from live_trading.modules.pipeline_monitor import (
    DEFAULT_THRESHOLDS,
    Finding,
    check_account,
    check_broker_reconcile,
    check_evening,
    check_postmarket,
    check_probe_execution,
    check_report,
    check_snapshot_protocol_status,
)
from live_trading.modules.snapshot import build_snapshot, sum_live_fills_amount
from live_trading.scripts.next_trade_date import next_open_date

logger = logging.getLogger("live_trading.monitor")

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"

STAGES = ("postmarket", "report", "evening")


def parse_args():
    p = argparse.ArgumentParser(description="Live trading monitor")
    p.add_argument("--config", required=True, help="live config id (configs/*.yaml)")
    p.add_argument("--stage", required=True, choices=STAGES)
    p.add_argument("--date", default=None, help="trade date YYYY-MM-DD (default today)")
    return p.parse_args()


def init_qlib(config):
    import qlib
    qlib.init(
        provider_uri=str(Path(config["data"]["qlib_dir"]).expanduser()),
        region=config["data"]["region"],
        kernels=1,  # 只取少量数据；同时规避 stdin/多进程陷阱
    )


def get_calendar_dates():
    from qlib.data import D
    return [str(c)[:10] for c in D.calendar()]


def fetch_close_prices(qlib_codes: list, date: str) -> dict:
    """未复权收盘价 {qlib_code: price}；取不到的股票不出现在结果里。"""
    from qlib.data import D
    if not qlib_codes:
        return {}
    result = {}
    try:
        df = D.features(sorted(qlib_codes), ["$close/$factor"],
                        start_time=date, end_time=date)
        for (inst, _dt), row in df.iterrows():
            val = row.iloc[0]
            if val == val:  # 非 NaN
                result[inst] = float(val)
    except Exception as e:
        logger.error("fetch close prices failed: %s", e)
    return result


def fetch_benchmark_close(benchmark: str, date: str):
    from qlib.data import D
    try:
        df = D.features([benchmark], ["$close"], start_time=date, end_time=date)
        if not df.empty:
            return float(df.iloc[0, 0])
    except Exception as e:
        logger.error("fetch benchmark close failed: %s", e)
    return None


# ---------- stage 实现 ----------

def run_evening(date, recorder, config) -> list:
    """检查今晚是否已为 Tushare 解析出的下一开市日发布批次。"""
    next_day = next_open_date(date)
    config_id = validate_identifier(config["live"]["strategy_id"], "strategy_id")
    get_state = getattr(recorder, "get_execution_state", None)
    execution_state = (
        get_state(config_id) if get_state is not None else {"state": "ACTIVE"}
    )
    audit_preview = _load_audit_preview(config_id, next_day)
    candidates = recorder.get_active_batches_by_date(
        next_day, strategy_id=config_id,
    )
    if not candidates:
        return check_evening(
            next_day, None, [], config_id, execution_state, audit_preview,
        )
    # 同一交易日取最新 seq（batch_id 结尾为三位 seq）。
    candidates.sort(key=lambda batch: batch["batch_id"])
    batch = candidates[-1]

    inbox = Path(config["live"]["bridge_root"]) / "inbox"
    inbox_files = None
    if inbox.exists():
        inbox_files = [p.name for p in inbox.iterdir()]
    return check_evening(
        next_day, batch, inbox_files, config_id, execution_state, audit_preview,
    )


def _load_audit_preview(strategy_id: str, trade_date: str) -> dict | None:
    """Load one preview conservatively; malformed evidence is not a valid pause."""
    strategy_id = validate_identifier(strategy_id, "strategy_id")
    path = (
        PROJECT_ROOT / "live_trading" / "logs" / strategy_id / "previews"
        / f"signal_{trade_date}.json"
    )
    try:
        with path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def run_postmarket(date, recorder, store, config) -> list:
    strategy_id = config["live"]["strategy_id"]
    batches = recorder.get_active_batches_by_date(
        date, strategy_id=strategy_id,
    )
    importer = FillImporter(config["live"]["bridge_root"], recorder)
    reconciles = {b["batch_id"]: importer.reconcile(b["batch_id"]) for b in batches}
    # Plans and fills are strategy scoped.  The brokerage snapshot below stays
    # account-wide because main and probe deliberately share one account.
    fills = [
        fill for batch in batches for fill in recorder.get_fills(batch["batch_id"])
    ]

    prev_positions = None
    snaps = [s for s in store.get_snapshots(end=date) if s["date"] < date]
    if snaps:
        rows = store.get_position_snapshots(snaps[-1]["date"])
        prev_positions = {r["stock_code"]: r["shares"] for r in rows}

    thresholds = _thresholds(config)
    findings = check_postmarket(date, batches, reconciles, fills,
                               prev_positions,
                               reject_rate=thresholds["reject_rate"])
    snapshot_roots = [Path(config["live"]["bridge_root"])]
    if config.get("live", {}).get("broker_environment") == "REAL":
        snapshot_roots = list(_execution_roots(config))
    for snapshot_root in dict.fromkeys(snapshot_roots):
        status_path = snapshot_root / "snapshot_requests" / "status.json"
        findings += check_snapshot_protocol_status(
            _read_json_object(status_path), str(status_path),
            _scan_snapshot_protocol_residue(snapshot_root, recorder),
        )
    # Any LIVE strategy on the shared account requires account-wide reconcile.
    # In particular, a PAUSED main must not hide drift created by the probe.
    account_batches = recorder.get_active_batches_by_date(date)
    if any(b.get("mode") == "LIVE" for b in account_batches):
        reconcile_cfg = config.get("monitor", {}).get("broker_reconcile") or {}
        findings += check_broker_reconcile(
            date,
            recorder.get_broker_account_snapshot(date),
            recorder.get_broker_positions(date),
            {code: pos["shares"]
             for code, pos in recorder.get_positions().items()},
            recorder.get_cash(),
            cash_tolerance=thresholds["cash_tolerance"],
            check_cash=bool(reconcile_cfg.get("cash_check", True)),
            ledger_value_adjustment=recorder.get_value_adjustment(),
            broker_position_market_values=(
                recorder.get_broker_position_market_values(date)
            ),
            value_tolerance=thresholds["cash_tolerance"],
        )
    if config.get("live", {}).get("broker_environment") == "REAL":
        findings += _run_probe_checks(date, recorder, config)
    return findings


def _run_probe_checks(date, recorder, config) -> list:
    """Build probe evidence without changing the account-wide ledger."""
    probe_batches = recorder.get_active_batches_by_date(
        date, strategy_id=PROBE_STRATEGY_ID,
    )
    probe_batches.sort(key=lambda row: row["batch_id"])
    probe_batch = probe_batches[-1] if probe_batches else None
    probe_orders = (
        recorder.get_orders(probe_batch["batch_id"]) if probe_batch else []
    )
    probe_fills = (
        recorder.get_fills(probe_batch["batch_id"]) if probe_batch else []
    )
    main_root, probe_root = _execution_roots(config)
    main_marker = main_root / "state" / f"LIVE_OK_{date}"
    probe_marker = probe_root / "state" / f"PR49_LIVE_OK_{date}"
    authorization_intents = []
    for state_root in (main_root / "state", probe_root / "state"):
        authorization_intents.extend(
            str(path) for path in state_root.glob(
                f"*LIVE_OK_{date}.intent.*.tmp"
            ) if path.is_file()
        )
    event_log = probe_root / "logs" / f"qmt_events_{date}.jsonl"
    return check_probe_execution(
        date,
        main_authorized=main_marker.is_file(),
        probe_authorized=probe_marker.is_file(),
        probe_batch=probe_batch,
        probe_orders=probe_orders,
        probe_fills=probe_fills,
        broker_account=recorder.get_broker_account_snapshot(date),
        broker_positions=recorder.get_broker_positions(date),
        lifecycle=recorder.get_operator_probe_lifecycle(PROBE_STRATEGY_ID),
        qmt_events=_read_qmt_events(event_log),
        event_log_path=str(event_log),
        main_marker_path=str(main_marker),
        probe_marker_path=str(probe_marker),
        main_execution_state=recorder.get_execution_state(
            (
                MAIN_STRATEGY_ID
                if config["live"].get("kind") == "OPERATOR_PROBE"
                else config["live"]["strategy_id"]
            ),
        )["state"],
        authorization_intents=authorization_intents,
    )


def _execution_roots(config):
    current_root = Path(config["live"]["bridge_root"]).expanduser().resolve()
    if config["live"]["strategy_id"] == PROBE_STRATEGY_ID:
        return current_root.parent, current_root
    return current_root, current_root / "pr49_probe"


def _read_qmt_events(path: Path) -> list:
    """Read durable JSONL conservatively; malformed lines are not evidence."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _read_json_object(path: Path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, TypeError, ValueError):
        return "INVALID"
    return payload


def _scan_snapshot_protocol_residue(bridge_root: Path, recorder) -> list:
    request_root = bridge_root / "snapshot_requests"
    artifacts = []
    scan_errors = []
    try:
        list(bridge_root.iterdir())
    except FileNotFoundError:
        return [
            f"path={bridge_root};expected=directory;observed=missing"
        ]
    except NotADirectoryError:
        return [
            f"path={bridge_root};expected=directory;observed=not-directory"
        ]
    except OSError:
        return [
            f"path={bridge_root};expected=readable-directory;"
            "observed=list-error"
        ]
    authorization_root = (
        bridge_root.parent if bridge_root.name == "pr49_probe" else bridge_root
    )
    gate = authorization_root / "state" / SNAPSHOT_ADVANCE_GATE_NAME
    try:
        if gate.is_file():
            scan_errors.append("state/" + SNAPSHOT_ADVANCE_GATE_NAME)
    except OSError:
        scan_errors.append("state/<advance-gate-scan-error>")
    for directory in ("inbox", "processing", "archive", "responses"):
        root = request_root / directory
        try:
            paths = list(root.iterdir())
        except FileNotFoundError:
            scan_errors.append(
                f"path={root};expected=directory;observed=missing"
            )
            continue
        except NotADirectoryError:
            scan_errors.append(
                f"path={root};expected=directory;observed=not-directory"
            )
            continue
        except OSError:
            scan_errors.append(
                f"path={root};expected=readable-directory;"
                "observed=list-error"
            )
            continue
        for path in paths:
            name = path.name
            if not (
                name.startswith("request_snapshot_")
                or name.startswith("response_snapshot_")
            ):
                continue
            if not (
                name.endswith(".json") or name.endswith(".done")
                or name.endswith(".json.tmp") or name.endswith(".done.tmp")
                or ".intent" in name
            ):
                continue
            artifacts.append(f"{directory}/{name}")
    groups = {}
    for artifact in artifacts:
        match = re.search(
            r"(snapshot_[0-9]{8}_[a-f0-9]{32})", artifact,
        )
        request_id = match.group(1) if match else artifact
        groups.setdefault(request_id, []).append(artifact)
    unresolved = list(scan_errors)
    for request_id, group in groups.items():
        if not _mac_imported_snapshot_archive_valid(
            request_root, request_id, group, recorder,
        ):
            unresolved.extend(group)
    return sorted(unresolved)


def _mac_imported_snapshot_archive_valid(
    request_root: Path, request_id: str, artifacts: list, recorder,
) -> bool:
    if not re.fullmatch(r"snapshot_[0-9]{8}_[a-f0-9]{32}", request_id):
        return False
    expected = {
        f"archive/request_{request_id}.json",
        f"archive/request_{request_id}.done",
        f"archive/response_{request_id}.json",
        f"archive/response_{request_id}.done",
    }
    if set(artifacts) != expected:
        return False
    durable = recorder.get_account_snapshot_request(request_id)
    if durable is None or durable.get("status") != "IMPORTED_COMPLETE":
        return False
    archive = request_root / "archive"
    try:
        request = json.loads(
            (archive / f"request_{request_id}.json").read_text(encoding="utf-8")
        )
        request_done = (
            archive / f"request_{request_id}.done"
        ).read_text(encoding="utf-8").strip()
        response = json.loads(
            (archive / f"response_{request_id}.json").read_text(encoding="utf-8")
        )
        response_done = (
            archive / f"response_{request_id}.done"
        ).read_text(encoding="utf-8").strip()
    except (OSError, TypeError, ValueError):
        return False
    if not isinstance(request, dict) or not isinstance(response, dict):
        return False
    request_checksum = snapshot_artifact_checksum(request)
    response_checksum = snapshot_artifact_checksum(response)
    return bool(
        request.get("request_id") == request_id
        and request.get("checksum") == request_checksum
        and request_done == request_checksum
        and durable.get("request_checksum") == request_checksum
        and response.get("request_id") == request_id
        and response.get("request_checksum") == request_checksum
        and response.get("status") == "COMPLETE"
        and response.get("checksum") == response_checksum
        and response_done == response_checksum
        and durable.get("response_checksum") == response_checksum
    )


def run_corporate_actions(date, recorder, store, config) -> tuple:
    """分红/送股入账（快照前执行）。返回 (入账描述列表, findings)。"""
    applied = recorder.settle_due_corporate_actions(date)
    try:
        events = fetch_dividend_events(date)
    except Exception as e:
        logger.error("fetch dividend events failed: %s", e)
        return applied, [Finding(
            "CORP_ACTION_FAILED", "WARN",
            f"{date} 分红事件查询失败（{e}），若当日有持仓股除息请用 "
            "record_cash_flow.py 手工补录")]

    historical_codes = (
        set(recorder.get_positions()) | store.get_historical_position_codes()
    )

    tax_rate = fees_from_config(config)["dividend_tax_rate"]
    findings = []
    snapshots = {}
    for event in events:
        code = event["stock_code"]
        record_date = event.get("record_date")
        record_snapshot = store.get_snapshot(record_date) if record_date else None
        if record_snapshot is None:
            # The API returns market-wide events. Missing local history only
            # matters for a stock the account has held at some point.
            if code not in historical_codes:
                continue
            findings.append(Finding(
                "CORP_ACTION_ENTITLEMENT_MISSING", "WARN",
                f"{code} {event.get('ex_date')} 除息，但缺少股权登记日 "
                f"{record_date or 'UNKNOWN'} 持仓快照；未自动入账",
            ))
            continue
        if record_date not in snapshots:
            snapshots[record_date] = {
                row["stock_code"]: row
                for row in store.get_position_snapshots(record_date)
            }
        position = snapshots[record_date].get(code)
        if not position or position["shares"] <= 0:
            continue
        shares = int(position["shares"])
        if recorder.accrue_corporate_action(event, shares, tax_rate):
            gross = round(shares * event["cash_div_tax"], 2)
            tax = round(gross * tax_rate, 2)
            parts = []
            if gross > 0:
                parts.extend([
                    f"DIVIDEND_RECEIVABLE {code} +{gross:.2f}",
                    f"TAX_PROVISION -{tax:.2f}",
                ])
                if not event.get("pay_date"):
                    findings.append(Finding(
                        "CORP_ACTION_SETTLEMENT_DATE_MISSING", "WARN",
                        f"{code} {event.get('ex_date')} 现金分红缺少派息日；"
                        "已挂应收但不会自动转为现金",
                    ))
            bonus = int(shares * event["stk_div"])
            if bonus > 0:
                parts.append(f"PENDING_BONUS +{bonus}股")
                if not event.get("div_listdate"):
                    findings.append(Finding(
                        "CORP_ACTION_LIST_DATE_MISSING", "WARN",
                        f"{code} {event.get('ex_date')} 送转股缺少上市日；"
                        "已挂待上市股但不会自动转为普通持仓",
                    ))
            applied.append("; ".join(parts))
    return applied, findings


def _previous_performance_snapshot(date, previous, config):
    if previous:
        return previous[-1]
    baseline = config.get("monitor", {}).get("performance_baseline")
    if not baseline or date != baseline["first_snapshot_date"]:
        return None
    return {
        "total_value": float(baseline["opening_total_value"]),
        "cumulative_return": 0.0,
        "benchmark_close": float(baseline["benchmark_close"]),
        "benchmark_cumulative_return": 0.0,
    }


def run_report(date, calendar, recorder, store, config, notifier) -> list:
    latest_cal = calendar[-1] if calendar else None
    findings = check_report(date, latest_cal, [])
    if any(f.level == "CRIT" for f in findings):
        return findings  # 数据未更新，快照不可信，不落库

    corp_applied, corp_findings = run_corporate_actions(
        date, recorder, store, config,
    )
    findings += corp_findings

    positions = recorder.get_positions()   # {qmt_code: {shares, avg_cost}}
    cash = recorder.get_cash()
    corporate = recorder.get_corporate_balances()

    price_codes = set(positions) | set(corporate["pending_shares"])
    qlib_by_qmt = {code: qmt_to_qlib(code) for code in price_codes}
    prices_qlib = fetch_close_prices(list(qlib_by_qmt.values()), date)
    prices = {qmt: prices_qlib.get(ql) for qmt, ql in qlib_by_qmt.items()
              if prices_qlib.get(ql) is not None}

    benchmark = config.get("monitor", {}).get("benchmark", "SH000300")
    bench_close = fetch_benchmark_close(benchmark, date)

    prev_snaps = [s for s in store.get_snapshots(end=date) if s["date"] < date]
    prev_snapshot = _previous_performance_snapshot(date, prev_snaps, config)

    fills = recorder.get_fills_by_dates([date])
    fills_amount = sum_live_fills_amount(fills)

    daily_row, position_rows, missing = build_snapshot(
        date, positions, cash, prices, bench_close,
        prev_snapshot, fills_amount,
        external_flow=recorder.sum_external_flows(date),
        fees=recorder.sum_fees_by_date(date),
        receivables=corporate["receivables"],
        pending_shares=corporate["pending_shares"],
        tax_provision=corporate["tax_provision"],
        account_value_adjustment=recorder.get_value_adjustment(),
    )
    store.upsert_daily_snapshot(daily_row)
    store.upsert_position_snapshots(date, position_rows)
    logger.info("snapshot %s: total=%.2f positions=%d",
                date, daily_row["total_value"], daily_row["position_count"])

    findings += check_report(date, latest_cal, missing)
    findings += check_account(store.get_snapshots(end=date), _thresholds(config))

    if config.get("monitor", {}).get("notify", {}).get("daily_report", True):
        title = f"[实盘日报] {date}"
        body = _daily_report_md(date, daily_row, fills, findings, corp_applied)
        ok = notifier.send(title, body)
        logger.info("daily report sent=%s", ok)
    return findings


def _thresholds(config) -> dict:
    th = dict(DEFAULT_THRESHOLDS)
    th.update(config.get("monitor", {}).get("thresholds", {}) or {})
    return th


def _may_run_with_stale_calendar(stage, active_batches) -> bool:
    """Postmarket reconciliation needs receipts, not same-day qlib prices."""
    return stage == "postmarket" and bool(active_batches)


def _fmt_pct(v):
    return f"{v*100:+.2f}%" if v is not None else "—"


def _daily_report_md(date, snap, fills, findings, corp_applied=None) -> str:
    traded = [f for f in fills if f["mode"] == "LIVE"
              and f["status"] in {"FILLED", "PARTIAL"}]
    account_details = [
        f"现金 {snap['cash']:,.2f}",
        f"应收 {snap.get('receivables', 0):,.2f}",
        f"红利税准备 {snap.get('tax_provision', 0):,.2f}",
    ]
    if snap.get("account_value_adjustment"):
        account_details.append(
            f"账户价值调整 {snap['account_value_adjustment']:+,.2f}"
        )
    lines = [
        f"**总资产** {snap['total_value']:,.2f}（{'，'.join(account_details)}）",
        f"**日收益** {_fmt_pct(snap['daily_return'])}"
        f"　累计 {_fmt_pct(snap['cumulative_return'])}"
        f"　超额 {_fmt_pct(snap['excess_return'])}",
        f"**持仓** {snap['position_count']} 只　换手 {_fmt_pct(snap['turnover'])}"
        f"　费用 {snap.get('fees', 0):,.2f}",
        f"**当日 LIVE 成交** {len(traded)} 笔",
    ]
    if traded:
        for f in traded:
            lines.append(f"- {f['side']} {f['stock_code']} "
                         f"x{f['filled_qty']} @ {f['avg_price']}")
    if snap.get("external_flow"):
        lines.append(f"\n**出入金** {snap['external_flow']:+,.2f}（日收益已剔除）")
    if corp_applied:
        lines.append("\n**公司行为**")
        for msg in corp_applied:
            lines.append(f"- {msg}")
    if findings:
        lines.append("\n**告警**")
        for f in findings:
            lines.append(f"- [{f.level}] {f.rule}: {f.message}")
    else:
        lines.append("\n无告警")
    return "\n\n".join(lines[:4]) + "\n\n" + "\n".join(lines[4:])


# ---------- Finding 落库与推送 ----------

def dispatch_findings(findings, stage, date, store, notifier) -> None:
    if not findings:
        store.record_pipeline_event(date, stage, "OK", "")
        return
    for f in findings:
        status = "FAIL" if f.level == "CRIT" else "WARN"
        try:
            store.record_pipeline_event(date, stage, status, f"{f.rule}: {f.message}")
            if store.try_record_alert(date, f.level, f.rule, f.message):
                ok = notifier.send(f"[实盘{f.level}] {f.rule} {date}", f.message)
                store.mark_alert_sent(date, f.rule, notifier.channel, ok)
        except Exception as e:
            logger.exception("dispatch finding %s failed: %s", f.rule, e)
            try:
                store.record_pipeline_event(date, stage, "FAIL",
                                            f"dispatch error {f.rule}: {e}")
            except Exception:
                pass


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config_id = validate_identifier(args.config, "config")
    config = load_live_config(CONFIGS_DIR / f"{config_id}.yaml", PROJECT_ROOT)
    config["live"]["strategy_id"] = validate_identifier(
        config["live"]["strategy_id"], "strategy_id",
    )
    date = args.date or _date.today().strftime("%Y-%m-%d")

    db_path = str(PROJECT_ROOT / config["storage"]["db_path"])
    recorder = LiveRecorder(
        db_path,
        fees=fees_from_config(config),
        opening_cash=config.get("account", {}).get("opening_cash"),
        opening_value_adjustment=config.get("account", {}).get(
            "opening_value_adjustment"
        ),
    )
    store = MonitorStore(db_path)
    notifier = create_notifier(config.get("monitor", {}))

    init_qlib(config)
    calendar = get_calendar_dates()
    active_batches = recorder.get_active_batches_by_date(
        date, strategy_id=config["live"]["strategy_id"],
    )
    if date not in calendar:
        if _may_run_with_stale_calendar(args.stage, active_batches):
            logger.warning(
                "%s absent from qlib calendar; running postmarket for active batch",
                date,
            )
        # 区分节假日与数据过期：当日有批次说明是交易日，日历却没有 → 数据未更新
        elif active_batches:
            findings = [Finding(
                "DATA_STALE", "CRIT",
                f"{date} 有信号批次但 qlib 日历最新为 {calendar[-1] if calendar else None}："
                "数据未更新，请先跑数据更新再重跑 monitor")]
            dispatch_findings(findings, args.stage, date, store, notifier)
            return 2
        else:
            logger.info("%s is not a trading day, nothing to do", date)
            return 0

    try:
        if args.stage == "evening":
            findings = run_evening(date, recorder, config)
        elif args.stage == "postmarket":
            findings = run_postmarket(date, recorder, store, config)
        else:
            findings = run_report(date, calendar, recorder, store, config, notifier)
    except Exception as e:
        logger.exception("stage %s crashed: %s", args.stage, e)
        findings = [Finding("MONITOR_ERROR", "CRIT",
                            f"monitor {args.stage} 异常退出：{e}")]

    dispatch_findings(findings, args.stage, date, store, notifier)
    for f in findings:
        logger.warning("[%s] %s: %s", f.level, f.rule, f.message)

    if any(f.level == "CRIT" for f in findings):
        return 2
    if findings:
        return 1
    logger.info("stage %s OK for %s", args.stage, date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
