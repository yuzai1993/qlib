#!/usr/bin/env python3
"""实盘监控：流程健康检查、每日快照、告警与日报推送。

用法（三个 stage 对应三个 cron 时点，见 live_trading/README.md）：
    python live_trading/scripts/run_monitor.py \
        --config csi300_topk10_live --stage {postmarket,report,evening} [--date YYYY-MM-DD]

退出码：0 全部 OK；1 有 WARN；2 有 CRIT/FAIL。
设计文档：docs/superpowers/specs/2026-07-13-live-monitor-platform-design.md
"""

import argparse
import logging
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
from live_trading.modules.live_config import load_live_config
from live_trading.modules.monitor_store import MonitorStore
from live_trading.modules.notifier import create_notifier
from live_trading.modules.pipeline_monitor import (
    DEFAULT_THRESHOLDS,
    Finding,
    check_account,
    check_evening,
    check_postmarket,
    check_report,
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
    candidates = recorder.get_active_batches_by_date(next_day)
    if not candidates:
        return check_evening(next_day, None, [])
    # 同一交易日取最新 seq（batch_id 结尾为三位 seq）。
    candidates.sort(key=lambda batch: batch["batch_id"])
    batch = candidates[-1]

    inbox = Path(config["live"]["bridge_root"]) / "inbox"
    inbox_files = None
    if inbox.exists():
        inbox_files = [p.name for p in inbox.iterdir()]
    return check_evening(next_day, batch, inbox_files)


def run_postmarket(date, recorder, store, config) -> list:
    batches = recorder.get_active_batches_by_date(date)
    importer = FillImporter(config["live"]["bridge_root"], recorder)
    reconciles = {b["batch_id"]: importer.reconcile(b["batch_id"]) for b in batches}
    fills = recorder.get_fills_by_dates([date])

    prev_positions = None
    snaps = [s for s in store.get_snapshots(end=date) if s["date"] < date]
    if snaps:
        rows = store.get_position_snapshots(snaps[-1]["date"])
        prev_positions = {r["stock_code"]: r["shares"] for r in rows}

    reject_rate = _thresholds(config)["reject_rate"]
    return check_postmarket(date, batches, reconciles, fills,
                            prev_positions, reject_rate=reject_rate)


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
    prev_snapshot = prev_snaps[-1] if prev_snaps else None

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
    lines = [
        f"**总资产** {snap['total_value']:,.2f}（现金 {snap['cash']:,.2f}，"
        f"应收 {snap.get('receivables', 0):,.2f}，"
        f"红利税准备 {snap.get('tax_provision', 0):,.2f}）",
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

    config = load_live_config(CONFIGS_DIR / f"{args.config}.yaml", PROJECT_ROOT)
    date = args.date or _date.today().strftime("%Y-%m-%d")

    db_path = str(PROJECT_ROOT / config["storage"]["db_path"])
    recorder = LiveRecorder(db_path, fees=fees_from_config(config))
    store = MonitorStore(db_path)
    notifier = create_notifier(config.get("monitor", {}))

    init_qlib(config)
    calendar = get_calendar_dates()
    active_batches = recorder.get_active_batches_by_date(date)
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
