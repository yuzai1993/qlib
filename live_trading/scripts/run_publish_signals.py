#!/usr/bin/env python3
"""生成并发布 QMT 交易信号批次。

用法：
    python live_trading/scripts/run_publish_signals.py \
        --config csi1000_b6m_b2s_postclose --trade-date 2026-08-03 \
        [--mode SIMULATE] [--dry-run]

流程（设计文档 §7.1）：
    qlib init → 预测 signal_date 分数 → 读取 live 持仓 → TopkDropout 意图
    → OrderPlanner → SignalPublisher 原子发布到 {bridge_root}/inbox/

安全：--mode LIVE 需要环境变量 LIVE_TRADING_CONFIRM=YES。
"""

import argparse
import dataclasses
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.code_map import qmt_to_qlib
from live_trading.modules.backtest_parity import validate_configured_backtest
from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.live_config import load_live_config
from live_trading.modules.order_planner import OrderPlanner
from live_trading.modules.signal_publisher import SignalPublisher
from live_trading.modules.signal_schema import (
    BatchHeader,
    compute_checksum,
    validate_batch,
)
from qlib.contrib.strategy.topk_dropout import stable_rank_scores

logger = logging.getLogger("live_trading.publish")

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def calculate_account_value(
    cash: float,
    positions: dict,
    prices: dict,
    value_adjustment: float = 0.0,
) -> float:
    """Economic NAV used for target sizing; cash remains spendable cash."""
    return (
        float(cash)
        + float(value_adjustment)
        + sum(
            p["shares"] * prices.get(instrument, 0.0)
            for instrument, p in positions.items()
        )
    )


def get_price_instruments(scores, current_positions: dict, topk: int) -> list:
    """Return the deterministic candidate/holding universe needing prices."""
    candidates = stable_rank_scores(scores).head(topk * 2).index
    return sorted(set(candidates) | set(current_positions))


def publish_recorded_plan(recorder, publisher, header, orders):
    """Validate, durably record, then make a batch visible to QMT."""
    order_lines = [order.to_json_line() for order in orders]
    validated_header = dataclasses.replace(
        header,
        order_count=len(orders),
        checksum=compute_checksum(order_lines),
    )
    validate_batch(validated_header, orders)
    publisher.ensure_publishable(validated_header, orders)
    recorder.record_publish_plan(validated_header, orders)
    return publisher.publish(validated_header, orders)


def ensure_prior_live_batches_terminal(
    recorder, trade_date: str, strategy_id: str | None = None,
) -> None:
    """Refuse a new LIVE plan while an earlier live batch is unreconciled."""
    blockers = recorder.get_unreconciled_active_live_batches_before(
        trade_date, strategy_id=strategy_id,
    )
    if not blockers:
        return
    details = ", ".join(
        f"{batch['batch_id']} "
        f"({batch['planned_orders'] - batch['terminal_orders']} missing)"
        for batch in blockers
    )
    raise SystemExit(
        "refusing LIVE publish: import/reconcile prior fills first: " + details
    )


def ensure_no_failed_prior_sells(recorder, trade_date: str) -> None:
    blockers = recorder.get_failed_live_sells_before(trade_date)
    if not blockers:
        return
    details = ", ".join(
        f"{row['client_order_id']}={row['status']}" for row in blockers
    )
    raise SystemExit(
        "refusing LIVE publish: failed prior SELL requires reconciliation: "
        + details
    )


def parse_args():
    p = argparse.ArgumentParser(description="Publish QMT signal batch")
    p.add_argument("--config", required=True, help="live config id (configs/*.yaml)")
    p.add_argument("--trade-date", required=True, help="planned execution date YYYY-MM-DD")
    p.add_argument("--mode", choices=["SIMULATE", "LIVE"], default=None,
                   help="default: live.default_mode from config")
    p.add_argument("--seq", type=int, default=1, help="batch seq of the day")
    p.add_argument("--dry-run", action="store_true", help="print orders, do not write files")
    return p.parse_args()


def resolve_mode(args, config) -> str:
    mode = args.mode or config["live"].get("default_mode", "SIMULATE")
    if (
        config["live"].get("broker_environment") == "REAL"
        and mode != "LIVE"
    ):
        raise SystemExit("REAL broker environment requires LIVE mode")
    if mode == "LIVE" and os.environ.get("LIVE_TRADING_CONFIRM") != "YES":
        raise SystemExit(
            "refusing LIVE mode: set env LIVE_TRADING_CONFIRM=YES to confirm"
        )
    return mode


def resolve_account_id(config) -> str:
    live_cfg = config["live"]
    environment = live_cfg.get("broker_environment")
    if environment == "REAL":
        if live_cfg.get("allow_real_money") is not True:
            raise SystemExit(
                "refusing REAL broker environment without allow_real_money"
            )
        variable = "QMT_REAL_ACCOUNT_ID"
        account_id = os.environ.get(variable, "")
    elif environment == "SIMULATION":
        variable = "QMT_SIM_ACCOUNT_ID"
        account_id = live_cfg.get("account_id") or os.environ.get(variable, "")
    else:
        raise SystemExit("unsupported broker environment")
    if not account_id:
        raise SystemExit(
            f"{environment} account_id missing: set {variable}"
        )
    return account_id


def to_strategy_positions(qmt_positions: dict) -> dict:
    """Map durable QMT positions to strategy metadata without dropping age."""
    return {
        qmt_to_qlib(code): {
            "shares": position["shares"],
            "cost_price": position["avg_cost"],
            "opened_trade_date": position.get("opened_trade_date"),
        }
        for code, position in qmt_positions.items()
    }


def resolve_signal_calendar(
    calendar,
    trade_date: str,
    next_open_resolver=None,
) -> tuple[str, list[str]]:
    """Return the latest local signal session before a validated target."""
    import pandas as pd

    sessions = sorted({pd.Timestamp(value) for value in calendar})
    target = pd.Timestamp(trade_date)
    prior = [value for value in sessions if value < target]
    if not prior:
        raise SystemExit(f"no trading day before {trade_date} in calendar")
    signal_date = prior[-1].strftime("%Y-%m-%d")
    if next_open_resolver is not None:
        expected = next_open_resolver(signal_date)
        if expected != trade_date:
            raise SystemExit(
                f"trade_date {trade_date} is not the next open trading day "
                f"after signal_date {signal_date} (expected {expected})"
            )
    return signal_date, [
        value.strftime("%Y-%m-%d") for value in sessions if value < target
    ]


def get_signal_date_and_scores(config, trade_date: str):
    """初始化 qlib，取 trade_date 前最后一个交易日的预测分数。"""
    import qlib
    from qlib.data import D

    qlib.init(
        provider_uri=str(Path(config["data"]["qlib_dir"]).expanduser()),
        region=config["data"]["region"],
    )
    from live_trading.scripts.next_trade_date import next_open_date

    signal_date, trade_dates = resolve_signal_calendar(
        D.calendar(end_time=trade_date),
        trade_date,
        next_open_resolver=next_open_date,
    )

    from live_trading.modules.signal_generator import SignalGenerator
    gen = SignalGenerator(config, PROJECT_ROOT)
    scores = gen.predict(signal_date, allow_stale=False)
    return signal_date, scores, trade_dates


def get_prev_close(config, instruments: list, signal_date: str) -> dict:
    """取 signal_date 的未复权收盘价（下单限价基准）。"""
    from qlib.data import D
    if not instruments:
        return {}
    # $close 为复权价，$close/$factor 才是真实价格
    df = D.features(
        instruments, ["$close/$factor"],
        start_time=signal_date, end_time=signal_date,
    )
    result = {}
    for (inst, _dt), row in df.iterrows():
        result[inst] = float(row.iloc[0])
    return result


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config_path = CONFIGS_DIR / f"{args.config}.yaml"
    config = load_live_config(config_path, PROJECT_ROOT)
    parity_path = validate_configured_backtest(config, PROJECT_ROOT)
    logger.info("Live/Backtest parity gate passed: %s", parity_path)
    live_cfg = config["live"]

    mode = resolve_mode(args, config)
    account_id = resolve_account_id(config)
    trade_date = args.trade_date
    batch_id = f"{trade_date.replace('-', '')}_{live_cfg['strategy_id']}_{args.seq:03d}"

    recorder = LiveRecorder(
        str(PROJECT_ROOT / config["storage"]["db_path"]),
        fees=config.get("fees"),
        opening_cash=config.get("account", {}).get("opening_cash"),
        opening_value_adjustment=config.get("account", {}).get(
            "opening_value_adjustment"
        ),
    )

    if mode == "LIVE":
        ensure_prior_live_batches_terminal(
            recorder, trade_date, live_cfg["strategy_id"],
        )
        ensure_no_failed_prior_sells(recorder, trade_date)

    # 1. 预测分数
    signal_date, scores, trade_dates = get_signal_date_and_scores(
        config, trade_date
    )
    logger.info("signal_date=%s, scored %d instruments", signal_date, len(scores))

    # 持久化全市场分数供监控查询（dry-run 不落库）
    if not args.dry_run:
        saved = recorder.save_predictions(signal_date, scores)
        logger.info("saved %d prediction scores for %s", saved, signal_date)

    # 2. 当前 live 持仓（QMT code → qlib instrument）
    qmt_positions = recorder.get_positions()
    current_positions = to_strategy_positions(qmt_positions)
    cash = recorder.get_cash()
    logger.info("live positions: %d, cash: %.2f", len(current_positions), cash)

    # 3. 昨收价（含持仓与候选 topk）
    strategy_cfg = config["strategy"]
    need_price = get_price_instruments(
        scores, current_positions, strategy_cfg["topk"],
    )
    prev_close = get_prev_close(config, need_price, signal_date)

    # 4. TopkDropout 意图
    from live_trading.modules.order_manager import OrderManager
    total_value = calculate_account_value(
        cash,
        current_positions,
        prev_close,
        recorder.get_value_adjustment(),
    )
    intents = OrderManager(config).generate_orders(
        scores,
        current_positions,
        cash,
        prev_close,
        total_value,
        signal_date=signal_date,
        trade_dates=trade_dates,
    )
    if not intents:
        logger.info("no orders planned for %s; publishing terminal empty batch", trade_date)

    # 5. 订单行
    planner = OrderPlanner({
        "max_orders_per_day": live_cfg["max_orders_per_day"],
        "trade_unit": config["exchange"]["trade_unit"],
    })
    orders = planner.plan(
        intents, prev_close, batch_id, trade_date, batch_seq=args.seq,
    )

    if args.dry_run:
        print(f"[dry-run] batch {batch_id} mode={mode} ({len(orders)} orders):")
        for o in orders:
            target = (
                f"target_value={o.target_value:.2f}"
                if o.side == "BUY"
                else f"quantity={o.quantity}"
            )
            print(
                f"  {o.side:4s} {o.stock_code} {target} "
                f"({o.client_order_id})"
            )
        return

    # 6. 发布
    header = BatchHeader(
        batch_id=batch_id,
        strategy_id=live_cfg["strategy_id"],
        trade_date=trade_date,
        signal_date=signal_date,
        account_id=account_id,
        account_type=live_cfg.get("account_type", "STOCK"),
        account_environment=live_cfg["broker_environment"],
        mode=mode,
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        order_count=0,   # publisher 填充
        checksum="",     # publisher 填充
    )
    publisher = SignalPublisher(live_cfg["bridge_root"])
    path = publish_recorded_plan(recorder, publisher, header, orders)
    logger.info("published %d orders to %s", len(orders), path)


if __name__ == "__main__":
    main()
