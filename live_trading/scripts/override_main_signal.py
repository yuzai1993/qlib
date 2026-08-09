#!/usr/bin/env python3
"""Create an auditable SELL-only replacement batch for the main strategy."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.signal_publisher import SignalPublisher
from live_trading.modules.signal_schema import (
    BatchHeader,
    SignalOrder,
    compute_checksum,
    make_client_order_id,
    validate_batch,
)


def load_source(root: Path, batch_id: str):
    path = root / "inbox" / f"signal_{batch_id}.jsonl"
    if not path.is_file():
        raise SystemExit(f"source batch not found: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if not rows or rows[0].get("type") != "batch_header":
        raise SystemExit("source batch header missing")
    header = BatchHeader.from_dict(rows[0])
    orders = [SignalOrder.from_dict(row) for row in rows[1:]]
    validate_batch(header, orders)
    return header, orders


def build_override(root: Path, source_batch: str, stock_code: str, quantity: int,
                   reason: str, operator: str, seq: int):
    source, source_orders = load_source(root, source_batch)
    if source.strategy_id == "csi1000_pr49_one_lot_probe":
        raise SystemExit("pr49 probe batches cannot be overridden by main strategy")
    if quantity <= 0 or quantity % 100:
        raise SystemExit("quantity must be a positive multiple of 100")
    source_match = next((o for o in source_orders if o.stock_code == stock_code), None)
    if source_match is None:
        raise SystemExit(f"stock {stock_code} is not present in source batch")
    batch_id = f"{source.trade_date.replace('-', '')}_{source.strategy_id}_override_{seq:03d}"
    order = SignalOrder(
        batch_id=batch_id,
        client_order_id=make_client_order_id(source.trade_date, seq, 1, "SELL"),
        stock_code=stock_code,
        side="SELL",
        quantity=quantity,
        target_value=0.0,
        price_type=source_match.price_type,
        limit_price=0.0,
        priority=10,
        instrument_qlib=source_match.instrument_qlib,
        reason=f"manual_override source={source_batch} operator={operator}: {reason}",
    )
    header = BatchHeader(
        batch_id=batch_id,
        strategy_id=source.strategy_id,
        trade_date=source.trade_date,
        signal_date=source.signal_date,
        account_id=source.account_id,
        account_type=source.account_type,
        account_environment=source.account_environment,
        mode="LIVE",
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        order_count=1,
        checksum=compute_checksum([order.to_json_line()]),
    )
    validate_batch(header, [order])
    return header, [order]


def main():
    parser = argparse.ArgumentParser(description="Publish an explicit main SELL override")
    parser.add_argument("--bridge-root", type=Path, required=True)
    parser.add_argument("--source-batch", required=True)
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--quantity", type=int, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--seq", type=int, required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args()
    header, orders = build_override(
        args.bridge_root, args.source_batch, args.stock_code, args.quantity,
        args.reason, args.operator, args.seq,
    )
    recorder = LiveRecorder(str(args.db_path))
    publisher = SignalPublisher(args.bridge_root)
    recorder.record_publish_plan(header, orders)
    path = publisher.publish(header, orders)
    print(path)


if __name__ == "__main__":
    main()
