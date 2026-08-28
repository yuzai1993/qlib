#!/usr/bin/env python3
"""Create an auditable SELL-only replacement batch for the main strategy."""

import argparse
import json
import os
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
        path = root / "archive" / "superseded" / f"signal_{batch_id}.jsonl"
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
    reference_order = source_match or source_orders[0]
    if stock_code.endswith(".SH"):
        instrument_qlib = "SH" + stock_code[:-3]
    elif stock_code.endswith(".SZ"):
        instrument_qlib = "SZ" + stock_code[:-3]
    else:
        raise SystemExit(f"unsupported stock code: {stock_code}")
    batch_id = f"{source.trade_date.replace('-', '')}_{source.strategy_id}_override_{seq:03d}"
    order = SignalOrder(
        batch_id=batch_id,
        client_order_id=make_client_order_id(source.trade_date, seq, 1, "SELL"),
        stock_code=stock_code,
        side="SELL",
        quantity=quantity,
        target_value=0.0,
        price_type=reference_order.price_type,
        limit_price=0.0,
        priority=10,
        instrument_qlib=(
            source_match.instrument_qlib if source_match else instrument_qlib
        ),
        reason=f"manual_override source={source_batch} operator={operator}: {reason}",
    )
    header = BatchHeader(
        batch_id=batch_id,
        strategy_id=source.strategy_id,
        trade_date=source.trade_date,
        signal_date=source.signal_date,
        account_type=source.account_type,
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        order_count=1,
        checksum=compute_checksum([order.to_json_line()]),
    )
    validate_batch(header, [order])
    return header, [order]


def replace_unclaimed_batch(root: Path, db_path: Path, source_batch: str,
                            stock_code: str, quantity: int, reason: str,
                            operator: str, seq: int) -> Path:
    """Replace one wholly unclaimed inbox batch with a SELL-only batch."""
    root = Path(root)
    recorder = LiveRecorder(str(db_path))
    shares = int(recorder.get_positions().get(stock_code, {}).get("shares", 0))
    if shares < quantity:
        raise SystemExit(
            f"available holding {shares} is below requested SELL {quantity}"
        )

    processing = root / "processing"
    if any((processing / f"signal_{source_batch}{suffix}").exists()
           for suffix in (".jsonl", ".done")):
        raise SystemExit(f"processing artifact exists for {source_batch}")

    header, orders = build_override(
        root, source_batch, stock_code, quantity, reason, operator, seq,
    )
    prefix = f"signal_{header.trade_date.replace('-', '')}_{header.strategy_id}_"
    allowed = {
        f"signal_{source_batch}.jsonl", f"signal_{source_batch}.done",
        f"signal_{header.batch_id}.jsonl", f"signal_{header.batch_id}.done",
    }
    for directory in (root / "inbox", processing):
        if directory.is_dir():
            unexpected = [
                path.name for path in directory.iterdir()
                if path.name.startswith(prefix) and path.name not in allowed
            ]
            if unexpected:
                raise SystemExit(
                    f"unexpected same-date artifact in {directory}: {unexpected}"
                )

    inbox = root / "inbox"
    archive = root / "archive" / "superseded"
    source_jsonl = inbox / f"signal_{source_batch}.jsonl"
    source_done = inbox / f"signal_{source_batch}.done"
    archived_jsonl = archive / source_jsonl.name
    archived_done = archive / source_done.name
    source_visible = source_jsonl.is_file() and source_done.is_file()
    source_archived = archived_jsonl.is_file() and archived_done.is_file()
    if not source_visible and not source_archived:
        raise SystemExit("source batch must be a complete inbox or archived pair")

    recorder.record_publish_plan(header, orders)
    if source_visible:
        archive.mkdir(parents=True, exist_ok=True)
        os.replace(source_done, archived_done)
        os.replace(source_jsonl, archived_jsonl)
    path = SignalPublisher(root).publish(header, orders)
    recorder.supersede_batch(source_batch, header.batch_id)
    return path


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
    parser.add_argument(
        "--replace-source", action="store_true",
        help="archive the unclaimed source pair and supersede it",
    )
    args = parser.parse_args()
    if args.replace_source:
        path = replace_unclaimed_batch(
            args.bridge_root, args.db_path, args.source_batch,
            args.stock_code, args.quantity, args.reason, args.operator,
            args.seq,
        )
        print(path)
        return
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
