"""流程健康检查 + 账户告警规则（纯函数，输入显式传参）。

规则清单与级别见设计文档 §6。所有函数返回 list[Finding]，
由 run_monitor.py 负责落库（pipeline_events / alerts）与推送。
"""

from collections import namedtuple

Finding = namedtuple("Finding", "rule level message")

WARN = "WARN"
CRIT = "CRIT"

_REJECT_STATUS = {"REJECTED", "ERROR"}
_TRADED_STATUS = {"FILLED", "PARTIAL"}

DEFAULT_THRESHOLDS = {
    "daily_loss": -0.03,
    "max_drawdown": -0.10,
    "consecutive_loss_days": 5,
    "reject_rate": 0.5,
}


def check_evening(next_trade_date, batch, inbox_files) -> list:
    """发布检查：下一交易日批次已入库且 inbox 有 jsonl + done。

    Args:
        next_trade_date: 下一交易日 YYYY-MM-DD
        batch: 该日 batches 行（dict）或 None
        inbox_files: inbox 目录文件名列表；挂载点不可用传 None
    """
    if inbox_files is None:
        return [Finding("PUBLISH_MISSING", CRIT,
                        f"{next_trade_date} 批次检查失败：bridge inbox 不可访问（SMB 挂载丢失？）")]
    if batch is None:
        return [Finding("PUBLISH_MISSING", CRIT,
                        f"{next_trade_date} 无信号批次记录，明日将空仓不动（若非刻意停发请立即补发）")]
    jsonl = f"signal_{batch['batch_id']}.jsonl"
    done = f"signal_{batch['batch_id']}.done"
    files = set(inbox_files)
    if jsonl not in files or done not in files:
        return [Finding("PUBLISH_MISSING", CRIT,
                        f"批次 {batch['batch_id']} 已入库但 inbox 缺文件"
                        f"（jsonl={'有' if jsonl in files else '无'}, "
                        f"done={'有' if done in files else '无'}），请重新发布")]
    return []


def check_postmarket(trade_date, batches, reconciles, fills,
                     prev_positions, reject_rate=0.5) -> list:
    """盘后回执检查。

    Args:
        batches: 当日 batches 行列表
        reconciles: {batch_id: {"planned", "terminal", "missing"}}
        fills: 当日全部 fills 行
        prev_positions: {stock_code: shares} 前一日持仓（负持仓矛盾检查用）；
            传 None 表示无基线（如首日无快照），跳过该项检查
    """
    findings = []
    if not batches:
        return findings  # 当日无批次不告警：可能刻意停发，evening 阶段已把关

    for b in batches:
        r = reconciles.get(b["batch_id"], {})
        missing = r.get("missing", 0)
        if missing > 0 or r.get("terminal", 0) == 0:
            findings.append(Finding(
                "FILLS_MISSING", CRIT,
                f"批次 {b['batch_id']} 回执不全：planned={r.get('planned')} "
                f"terminal={r.get('terminal')} missing={missing}，"
                "请对照 QMT 界面委托记录人工核对"))
            break  # 同日一条即可（alerts 表也会按规则去重）

    live_batches = [b for b in batches if b.get("mode") == "LIVE"]
    live_batch_ids = {b["batch_id"] for b in live_batches}
    planned_live = sum(
        int(reconciles.get(b["batch_id"], {}).get(
            "planned", b.get("planned_orders", 0),
        ))
        for b in live_batches
    )
    live_fills = [
        f for f in fills
        if f.get("mode") == "LIVE" and f.get("batch_id") in live_batch_ids
    ]
    if (
        planned_live > 0
        and len(live_fills) >= planned_live
        and all(
            f.get("status") == "SKIPPED"
            and int(f.get("filled_qty") or 0) == 0
            for f in live_fills
        )
    ):
        reasons = sorted({
            f.get("message", "").strip()
            for f in live_fills if f.get("message", "").strip()
        })
        suffix = f"；原因：{'；'.join(reasons)}" if reasons else ""
        findings.append(Finding(
            "ALL_ORDERS_SKIPPED", CRIT,
            f"{trade_date} 活动 LIVE 批次全部 {planned_live} 笔订单被跳过，"
            f"没有产生委托或成交{suffix}",
        ))

    total = len(fills)
    if total:
        rejected = sum(1 for f in fills if f.get("status") in _REJECT_STATUS)
        if rejected >= total * reject_rate:
            findings.append(Finding(
                "REJECT_RATE_HIGH", WARN,
                f"{trade_date} 拒单/错误 {rejected}/{total}，检查限价是否过保守或权限问题"))

    oversold = _oversold_codes(fills, prev_positions)
    if oversold:
        findings.append(Finding(
            "NEGATIVE_POSITION", CRIT,
            f"{trade_date} 卖出量超过昨日持仓：{', '.join(oversold)}，"
            "账本可能漂移，停止次日发布并全量核对"))
    return findings


def _oversold_codes(fills, prev_positions) -> list:
    if prev_positions is None:
        return []
    sold = {}
    for f in fills:
        if f.get("mode") == "LIVE" and f.get("status") in _TRADED_STATUS \
                and f.get("side") == "SELL":
            sold[f["stock_code"]] = sold.get(f["stock_code"], 0) + (f.get("filled_qty") or 0)
    return sorted(
        code for code, qty in sold.items()
        if qty > prev_positions.get(code, 0)
    )


def check_report(trade_date, latest_calendar_date, missing_price_codes) -> list:
    """快照前置检查：数据新鲜度、缺价。"""
    findings = []
    if latest_calendar_date is None or latest_calendar_date < trade_date:
        findings.append(Finding(
            "DATA_STALE", CRIT,
            f"qlib 日历最新 {latest_calendar_date}，未包含 {trade_date}：数据未更新，"
            "快照与次日信号均不可信，请先跑数据更新"))
    if missing_price_codes:
        findings.append(Finding(
            "PRICE_MISSING", WARN,
            f"{trade_date} 持仓缺收盘价（按成本估值）：{', '.join(missing_price_codes)}"))
    return findings


def check_account(snapshots, thresholds=None) -> list:
    """账户风险规则。snapshots 为按日期升序的 daily_snapshot 行列表。"""
    th = dict(DEFAULT_THRESHOLDS)
    th.update(thresholds or {})
    findings = []
    if not snapshots:
        return findings

    latest = snapshots[-1]
    date = latest["date"]

    daily = latest.get("daily_return")
    if daily is not None and daily < th["daily_loss"]:
        findings.append(Finding(
            "DAILY_LOSS", WARN,
            f"{date} 单日收益 {daily*100:.2f}%，超过阈值 {th['daily_loss']*100:.1f}%"))

    peak, max_dd = None, 0.0
    for s in snapshots:
        v = s["total_value"]
        peak = v if peak is None or v > peak else peak
        dd = v / peak - 1
        max_dd = min(max_dd, dd)
    if max_dd < th["max_drawdown"]:
        findings.append(Finding(
            "MAX_DRAWDOWN", CRIT,
            f"{date} 最大回撤 {max_dd*100:.2f}%，超过阈值 {th['max_drawdown']*100:.1f}%"))

    n = int(th["consecutive_loss_days"])
    recent = [s.get("daily_return") for s in snapshots[-n:]]
    if len(recent) >= n and all(r is not None and r < 0 for r in recent):
        findings.append(Finding(
            "CONSECUTIVE_LOSS", WARN,
            f"{date} 已连续 {n} 个交易日亏损"))
    return findings
