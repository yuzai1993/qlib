#!/usr/bin/env python3
"""生成并发布 QMT 交易信号批次。

用法：
    python live_trading/scripts/run_publish_signals.py \
        --config csi300_topk10_live --trade-date 2026-07-14 [--mode SIMULATE] [--dry-run]

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

logger = logging.getLogger("live_trading.publish")

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def publish_recorded_plan(recorder, publisher, header, orders):
    """Validate, durably record, then make a batch visible to QMT."""
    order_lines = [order.to_json_line() for order in orders]
    validated_header = dataclasses.replace(
        header,
        order_count=len(orders),
        checksum=compute_checksum(order_lines),
    )
    validate_batch(validated_header, orders)
    publisher.ensure_available(header.batch_id)
    recorder.record_publish_plan(validated_header, orders)
    return publisher.publish(validated_header, orders)


def ensure_prior_live_batches_terminal(recorder, trade_date: str) -> None:
    """Refuse a new LIVE plan while an earlier live batch is unreconciled."""
    blockers = recorder.get_unreconciled_active_live_batches_before(trade_date)
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
    if mode == "LIVE" and os.environ.get("LIVE_TRADING_CONFIRM") != "YES":
        raise SystemExit(
            "refusing LIVE mode: set env LIVE_TRADING_CONFIRM=YES to confirm"
        )
    return mode


def resolve_account_id(config) -> str:
    account_id = config["live"].get("account_id") or os.environ.get("QMT_ACCOUNT_ID", "")
    if not account_id:
        raise SystemExit(
            "account_id missing: set live.account_id in config or env QMT_ACCOUNT_ID"
        )
    return account_id


def get_signal_date_and_scores(config, trade_date: str):
    """初始化 qlib，取 trade_date 前最后一个交易日的预测分数。"""
    import qlib
    from qlib.data import D
    import pandas as pd

    qlib.init(
        provider_uri=str(Path(config["data"]["qlib_dir"]).expanduser()),
        region=config["data"]["region"],
    )
    cal = D.calendar(end_time=trade_date)
    cal = [pd.Timestamp(c) for c in cal]
    target = pd.Timestamp(trade_date)
    prior = [c for c in cal if c < target]
    if not prior:
        raise SystemExit(f"no trading day before {trade_date} in calendar")
    signal_date = prior[-1].strftime("%Y-%m-%d")

    from live_trading.modules.signal_generator import SignalGenerator
    gen = SignalGenerator(config, PROJECT_ROOT)
    scores = gen.predict(signal_date, allow_stale=False)
    return signal_date, scores


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

    recorder = LiveRecorder(str(PROJECT_ROOT / config["storage"]["db_path"]))

    if mode == "LIVE":
        ensure_prior_live_batches_terminal(recorder, trade_date)

    # 1. 预测分数
    signal_date, scores = get_signal_date_and_scores(config, trade_date)
    logger.info("signal_date=%s, scored %d instruments", signal_date, len(scores))

    # 持久化全市场分数供监控查询（dry-run 不落库）
    if not args.dry_run:
        saved = recorder.save_predictions(signal_date, scores)
        logger.info("saved %d prediction scores for %s", saved, signal_date)

    # 2. 当前 live 持仓（QMT code → qlib instrument）
    qmt_positions = recorder.get_positions()
    current_positions = {
        qmt_to_qlib(code): {"shares": p["shares"], "cost_price": p["avg_cost"]}
        for code, p in qmt_positions.items()
    }
    cash = recorder.get_cash()
    logger.info("live positions: %d, cash: %.2f", len(current_positions), cash)

    # 3. 昨收价（含持仓与候选 topk）
    strategy_cfg = config["strategy"]
    top_candidates = list(scores.sort_values(ascending=False).head(
        strategy_cfg["topk"] * 2).index)
    need_price = sorted(set(top_candidates) | set(current_positions.keys()))
    prev_close = get_prev_close(config, need_price, signal_date)

    # 4. TopkDropout 意图
    from live_trading.modules.order_manager import OrderManager
    total_value = cash + sum(
        p["shares"] * prev_close.get(inst, 0)
        for inst, p in current_positions.items()
    )
    intents = OrderManager(config).generate_orders(
        scores, current_positions, cash, prev_close, total_value,
    )
    if not intents:
        logger.info("no orders to publish for %s", trade_date)
        return

    # 5. 订单行
    planner = OrderPlanner({
        "buy_slippage": live_cfg["buy_slippage"],
        "sell_slippage": live_cfg["sell_slippage"],
        "max_orders_per_day": live_cfg["max_orders_per_day"],
        "trade_unit": config["exchange"]["trade_unit"],
    })
    orders = planner.plan(
        intents, prev_close, batch_id, trade_date, batch_seq=args.seq,
    )

    if args.dry_run:
        print(f"[dry-run] batch {batch_id} mode={mode} ({len(orders)} orders):")
        for o in orders:
            print(f"  {o.side:4s} {o.stock_code} x{o.quantity} @ {o.limit_price}"
                  f"  ({o.client_order_id})")
        return

    # 6. 发布
    header = BatchHeader(
        batch_id=batch_id,
        strategy_id=live_cfg["strategy_id"],
        trade_date=trade_date,
        signal_date=signal_date,
        account_id=account_id,
        account_type=live_cfg.get("account_type", "STOCK"),
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
