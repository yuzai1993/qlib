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
    "consecutive_loss_days": 5,
    "reject_rate": 0.5,
    "cash_tolerance": 100.0,
}


def _publish_recovery_hint(config_id, trade_date, has_batch):
    log_path = f"live_trading/logs/{config_id}_publish_cron.log"
    if has_batch:
        command = (
            f"bash live_trading/run_publish_cron.sh {config_id} {trade_date}"
        )
    else:
        command = (
            f"bash live_trading/run_publish_catchup_cron.sh {config_id}"
        )
    return f"；发布日志：{log_path}；人工恢复：{command}"


def check_evening(
    next_trade_date,
    batch,
    inbox_files,
    config_id,
    execution_state: dict | None = None,
    audit_preview: dict | None = None,
) -> list:
    """发布检查：下一交易日批次已入库且 inbox 有 jsonl + done。

    Args:
        next_trade_date: 下一交易日 YYYY-MM-DD
        batch: 该日 batches 行（dict）或 None
        inbox_files: inbox 目录文件名列表；挂载点不可用传 None
        config_id: 部署配置 ID，用于生成可执行的人工恢复命令
        execution_state: durable strategy state; PAUSED requires a current preview
        audit_preview: decoded evidence-only preview, if one was written
    """
    if execution_state and execution_state.get("state") == "PAUSED":
        if audit_preview and audit_preview.get("trade_date") == next_trade_date:
            return []
        return [Finding(
            "PAUSED_PREVIEW_MISSING", WARN,
            f"{next_trade_date} 策略已暂停，但没有当日经审计的信号预览；"
            "暂停状态不会发布 QMT 批次，请检查预览任务和发布日志："
            f"live_trading/logs/{config_id}/previews/signal_{next_trade_date}.json",
        )]
    if inbox_files is None:
        return [Finding("PUBLISH_MISSING", CRIT,
                        f"{next_trade_date} 批次检查失败：bridge inbox 不可访问"
                        "（SMB 挂载丢失？）；先恢复 SMB 挂载"
                        + _publish_recovery_hint(
                            config_id, next_trade_date, batch is not None,
                        ))]
    if batch is None:
        return [Finding("PUBLISH_MISSING", CRIT,
                        f"{next_trade_date} 无信号批次记录，明日将空仓不动"
                        "（若非刻意停发请立即补发）"
                        + _publish_recovery_hint(
                            config_id, next_trade_date, False,
                        ))]
    jsonl = f"signal_{batch['batch_id']}.jsonl"
    done = f"signal_{batch['batch_id']}.done"
    files = set(inbox_files)
    if jsonl not in files or done not in files:
        return [Finding("PUBLISH_MISSING", CRIT,
                        f"批次 {batch['batch_id']} 已入库但 inbox 缺文件"
                        f"（jsonl={'有' if jsonl in files else '无'}, "
                        f"done={'有' if done in files else '无'}），请重新发布"
                        + _publish_recovery_hint(
                            config_id, next_trade_date, True,
                        ))]
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
        planned = int(r.get("planned", b.get("planned_orders", 0)) or 0)
        terminal = int(r.get("terminal", 0) or 0)
        if missing > 0 or (planned > 0 and terminal == 0):
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


def check_probe_execution(
    trade_date,
    *,
    main_authorized,
    probe_authorized,
    probe_batch,
    probe_orders,
    probe_fills,
    broker_account,
    broker_positions,
    lifecycle,
    qmt_events,
    event_log_path,
    main_marker_path,
    probe_marker_path,
    main_execution_state="ACTIVE",
    authorization_intents=(),
) -> list:
    """Fail closed on the prType=49 probe's execution evidence.

    Batch, order and fill inputs are probe-strategy scoped.  Broker account
    and position inputs deliberately remain account-wide because both
    strategies trade the same brokerage account.
    """
    del main_execution_state  # A deliberate PAUSE never suppresses evidence.
    findings = []
    batch_id = (
        probe_batch.get("batch_id") if probe_batch else "NONE"
    ) or "NONE"
    stock = _probe_stock(probe_orders, lifecycle)

    def critical(rule, expected, observed):
        findings.append(Finding(
            rule,
            CRIT,
            _probe_evidence_message(
                trade_date,
                batch_id,
                stock,
                expected,
                observed,
                event_log_path,
            ),
        ))

    if authorization_intents:
        critical(
            "AUTHORIZATION_INTENT_REMAINS",
            "no unresolved authorization intent",
            "intent files: " + ",".join(sorted(authorization_intents)),
        )

    if main_authorized and probe_authorized:
        critical(
            "DUAL_AUTHORIZATION",
            "exactly one LIVE execution authorization",
            f"both markers present: {main_marker_path}; {probe_marker_path}",
        )

    if probe_batch is None:
        return findings

    events = [
        event for event in qmt_events
        if event.get("batch_id") == batch_id
    ]
    order_by_id = {
        order.get("client_order_id"): order
        for order in probe_orders if order.get("client_order_id")
    }
    attempted = {
        event.get("client_order_id")
        for event in events if event.get("event") == "PASSORDER_ATTEMPT"
    }
    observed = {
        event.get("client_order_id")
        for event in events
        if event.get("event") == "ORDER_OBSERVED"
        and _has_real_qmt_order_id(event)
    }
    for client_order_id in sorted(attempted - observed):
        final = next(
            (
                event for event in reversed(events)
                if event.get("event") == "ORDER_FINALIZED"
                and event.get("client_order_id") == client_order_id
            ),
            None,
        )
        order = order_by_id.get(client_order_id)
        order_stock = order.get("stock_code") if order else stock
        final_state = (
            f"final={final.get('fill_status') or 'UNKNOWN'} "
            f"reason={final.get('reason') or 'UNKNOWN'}"
            if final else "final=ABSENT"
        )
        findings.append(Finding(
            "PROBE_ORDER_NOT_OBSERVED",
            CRIT,
            _probe_evidence_message(
                trade_date,
                batch_id,
                order_stock,
                "ORDER_OBSERVED with a real QMT order id or explicit API failure",
                f"PASSORDER_ATTEMPT; ORDER_OBSERVED absent; {final_state}",
                event_log_path,
            ),
        ))

    snapshot_batch_id = (
        broker_account.get("batch_id") if broker_account else None
    )
    snapshot_matches = snapshot_batch_id == batch_id
    if not snapshot_matches:
        critical(
            "PROBE_SNAPSHOT_MISSING",
            f"ACCOUNT snapshot bound to batch {batch_id}",
            "ACCOUNT snapshot absent" if broker_account is None else (
                f"latest ACCOUNT snapshot batch={snapshot_batch_id}"
            ),
        )

    side = _probe_side(probe_orders)
    traded_fills = [
        fill for fill in probe_fills
        if fill.get("batch_id") == batch_id
        and fill.get("mode") == "LIVE"
        and fill.get("side") == side
        and fill.get("status") in _TRADED_STATUS
    ]
    successful_qty = sum(
        int(fill.get("filled_qty") or 0)
        for fill in traded_fills
    )
    broker_shares = int(broker_positions.get(stock, 0))
    if snapshot_matches and traded_fills:
        expected_shares = 100 if side == "BUY" else 0
        if successful_qty != 100 or broker_shares != expected_shares:
            critical(
                "PROBE_POSITION_DRIFT",
                f"filled_qty=100 broker_shares={expected_shares}",
                f"filled_qty={successful_qty} broker_shares={broker_shares}",
            )

    lifecycle_error = _probe_lifecycle_error(
        probe_batch,
        probe_orders,
        probe_fills,
        broker_positions,
        broker_account,
        lifecycle,
    )
    if lifecycle_error:
        expected, observed_state = lifecycle_error
        critical("PROBE_LIFECYCLE_INVALID", expected, observed_state)
    return findings


def _probe_stock(probe_orders, lifecycle):
    if probe_orders and probe_orders[0].get("stock_code"):
        return probe_orders[0]["stock_code"]
    if lifecycle and lifecycle.get("stock_code"):
        return lifecycle["stock_code"]
    return "NONE"


def _has_real_qmt_order_id(event):
    order_ids = event.get("qmt_order_ids")
    return isinstance(order_ids, list) and any(
        isinstance(order_id, str) and bool(order_id.strip())
        for order_id in order_ids
    )


def _probe_side(probe_orders):
    if probe_orders:
        return probe_orders[0].get("side") or "UNKNOWN"
    return "UNKNOWN"


def _probe_evidence_message(
    trade_date, batch_id, stock, expected, observed, event_log_path,
):
    return (
        f"date={trade_date} strategy_id=csi1000_pr49_one_lot_probe "
        f"batch_id={batch_id} stock={stock} expected={expected} "
        f"observed={observed} log={event_log_path}"
    )


def _probe_lifecycle_error(
    probe_batch,
    probe_orders,
    probe_fills,
    broker_positions,
    broker_account,
    lifecycle,
):
    batch_id = probe_batch.get("batch_id")
    stock = _probe_stock(probe_orders, lifecycle)
    side = _probe_side(probe_orders)
    if lifecycle is None:
        return "lifecycle row bound to the probe plan", "lifecycle absent"
    if lifecycle.get("strategy_id") != "csi1000_pr49_one_lot_probe":
        return (
            "probe strategy lifecycle",
            f"strategy_id={lifecycle.get('strategy_id')}",
        )
    if lifecycle.get("stock_code") != stock:
        return (
            f"lifecycle stock={stock}",
            f"lifecycle stock={lifecycle.get('stock_code')}",
        )
    binding_field = "buy_batch_id" if side == "BUY" else "sell_batch_id"
    if lifecycle.get(binding_field) != batch_id:
        return (
            f"lifecycle batch binding {binding_field}={batch_id}",
            f"lifecycle batch binding {binding_field}={lifecycle.get(binding_field)}",
        )

    trade_date = probe_batch.get("trade_date")
    if side == "BUY" and lifecycle.get("buy_trade_date") != trade_date:
        return (
            f"buy_trade_date={trade_date}",
            f"buy_trade_date={lifecycle.get('buy_trade_date')}",
        )
    if side == "SELL":
        buy_batch_id = lifecycle.get("buy_batch_id")
        buy_trade_date = lifecycle.get("buy_trade_date")
        sell_trade_date = lifecycle.get("sell_trade_date")
        dates_valid = (
            isinstance(buy_batch_id, str) and bool(buy_batch_id.strip())
            and isinstance(buy_trade_date, str) and bool(buy_trade_date.strip())
            and sell_trade_date == trade_date
            and buy_trade_date < sell_trade_date
        )
        if not dates_valid:
            return (
                f"sell lifecycle dates buy_trade_date<sell_trade_date={trade_date}",
                "sell lifecycle dates "
                f"buy_batch_id={buy_batch_id} buy_trade_date={buy_trade_date} "
                f"sell_trade_date={sell_trade_date}",
            )

    valid_states = {
        "BUY_PLANNED", "BUY_FILLED", "SELL_PLANNED", "CLOSED", "FAILED",
    }
    state = lifecycle.get("state")
    if state not in valid_states:
        return "valid probe lifecycle state", f"state={state}"

    terminal = [
        fill for fill in probe_fills
        if fill.get("batch_id") == batch_id and fill.get("side") == side
        and fill.get("status") in {
            "FILLED", "PARTIAL", "REJECTED", "SKIPPED", "EXPIRED", "ERROR",
        }
    ]
    if not terminal:
        expected_state = "BUY_PLANNED" if side == "BUY" else "SELL_PLANNED"
    elif any(fill.get("status") in _TRADED_STATUS for fill in terminal):
        snapshot_matches = (
            broker_account is not None
            and broker_account.get("batch_id") == batch_id
        )
        filled_qty = sum(
            int(fill.get("filled_qty") or 0)
            for fill in terminal if fill.get("status") in _TRADED_STATUS
        )
        expected_shares = 100 if side == "BUY" else 0
        if not snapshot_matches or filled_qty != 100 \
                or int(broker_positions.get(stock, 0)) != expected_shares:
            return None
        expected_state = "BUY_FILLED" if side == "BUY" else "CLOSED"
    else:
        expected_state = "FAILED"
    if state != expected_state:
        return f"state={expected_state}", f"state={state}"
    return None


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


def check_broker_reconcile(trade_date, broker_account, broker_positions,
                           ledger_positions, ledger_cash,
                           cash_tolerance=None, check_cash=True,
                           ledger_value_adjustment=0.0,
                           broker_position_market_values=None,
                           value_tolerance=None) -> list:
    """二道对账：券商快照 vs 本地账本。

    回执只反映策略「以为」成交了什么；券商快照是账户自己的口径，
    两者对不上说明账本漂移（漏记拆单、费率差异、期初资金不符）。

    Args:
        broker_account: broker_account_snapshot 行（dict）或 None
        broker_positions: {stock_code: shares} 券商持仓；无快照传空 dict
        ledger_positions: {stock_code: shares} 账本持仓
        ledger_cash: 账本现金
        cash_tolerance: 现金差额容忍额（元）
        check_cash: False 时跳过现金类告警（CASH_NEGATIVE /
            BROKER_CASH_MISMATCH），只对持仓。QMT 模拟盘的可用资金口径
            不可信、以账本为准时用；切真实账户后应恢复 True。
        ledger_value_adjustment: 账本中不属于普通持仓的账户价值调整。
        broker_position_market_values: 券商逐仓市值；字段不完整时跳过
            账户价值调整对账。
        value_tolerance: 账户价值调整差额容忍额，默认与现金一致。
    """
    tol = DEFAULT_THRESHOLDS["cash_tolerance"] if cash_tolerance is None \
        else float(cash_tolerance)
    findings = []

    if check_cash and ledger_cash < 0:
        findings.append(Finding(
            "CASH_NEGATIVE", CRIT,
            f"{trade_date} 账本现金为负（{ledger_cash:.2f}）：期初资金或成交记录有缺口，"
            "先与 QMT 可用资金对账再发布次日信号"))

    if broker_account is None and not broker_positions:
        findings.append(Finding(
            "BROKER_SNAPSHOT_MISSING", WARN,
            f"{trade_date} 缺少券商账户快照，二道对账未执行"
            "（检查桥接策略版本与 outbound/account_*.jsonl）"))
        return findings

    diffs = []
    for code in sorted(set(broker_positions) | set(ledger_positions)):
        broker_shares = int(broker_positions.get(code, 0))
        ledger_shares = int(ledger_positions.get(code, 0))
        if broker_shares != ledger_shares:
            diffs.append(f"{code} 券商{broker_shares}/账本{ledger_shares}")
    if diffs:
        findings.append(Finding(
            "BROKER_POSITION_MISMATCH", CRIT,
            f"{trade_date} 持仓与券商不一致：{', '.join(diffs)}，"
            "停止次日发布并核对成交明细"))

    broker_cash = None if (broker_account is None or not check_cash) \
        else broker_account.get("available_cash")
    if broker_cash is not None:
        gap = ledger_cash - float(broker_cash)
        if abs(gap) > tol:
            findings.append(Finding(
                "BROKER_CASH_MISMATCH", CRIT,
                f"{trade_date} 现金与券商差 {gap:.2f} 元"
                f"（账本 {ledger_cash:.2f} / 券商可用 {float(broker_cash):.2f}），"
                f"超过容忍 {tol:.2f}，核对后用 record_cash_flow CORRECTION 校正"))

    aggregate_market_value = (
        broker_account.get("market_value") if broker_account is not None else None
    )
    position_values_complete = (
        broker_position_market_values is not None
        and set(broker_position_market_values) == set(broker_positions)
        and all(
            value is not None
            for value in broker_position_market_values.values()
        )
    )
    if aggregate_market_value is not None and position_values_complete:
        broker_residual = float(aggregate_market_value) - sum(
            float(value) for value in broker_position_market_values.values()
        )
        adjustment_gap = broker_residual - float(ledger_value_adjustment)
        adjustment_tol = tol if value_tolerance is None else float(value_tolerance)
        if abs(adjustment_gap) > adjustment_tol:
            findings.append(Finding(
                "BROKER_VALUE_ADJUSTMENT_MISMATCH", CRIT,
                f"{trade_date} 券商隐含账户价值调整与账本差 "
                f"{adjustment_gap:.2f} 元（券商 {broker_residual:.2f} / "
                f"账本 {float(ledger_value_adjustment):.2f}），超过容忍 "
                f"{adjustment_tol:.2f}；停止次日发布并核对 QMT 总市值",
            ))
    return findings


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

    n = int(th["consecutive_loss_days"])
    recent = [s.get("daily_return") for s in snapshots[-n:]]
    if len(recent) >= n and all(r is not None and r < 0 for r in recent):
        findings.append(Finding(
            "CONSECUTIVE_LOSS", WARN,
            f"{date} 已连续 {n} 个交易日亏损"))
    return findings
