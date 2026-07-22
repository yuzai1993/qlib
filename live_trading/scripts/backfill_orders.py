#!/usr/bin/env python3
"""一次性回补：从 bridge archive/inbox 导入历史 signal 订单。

用法：
    python live_trading/scripts/backfill_orders.py --config csi300_topk10_live
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.live_config import load_live_config
from live_trading.modules.signal_schema import SignalOrder


def parse_signal_orders(jsonl_path: Path) -> list:
    orders = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if d.get("type") != "order":
            continue
        orders.append(SignalOrder.from_dict(d))
    return orders


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    args = p.parse_args()

    config = load_live_config(
        PROJECT_ROOT / "live_trading" / "configs" / f"{args.config}.yaml",
        PROJECT_ROOT,
    )
    recorder = LiveRecorder(str(PROJECT_ROOT / config["storage"]["db_path"]))
    bridge = Path(config["live"]["bridge_root"])

    found = 0
    for folder in ("archive", "inbox", "processing"):
        root = bridge / folder
        if not root.exists():
            continue
        for path in sorted(root.glob("signal_*.jsonl")):
            batch_id = path.stem.replace("signal_", "", 1)
            orders = parse_signal_orders(path)
            if not orders:
                continue
            if not recorder.get_batch(batch_id):
                # 从 header 补批次元数据
                header = None
                for line in path.read_text(encoding="utf-8").splitlines():
                    d = json.loads(line)
                    if d.get("type") == "batch_header":
                        header = d
                        break
                if header:
                    recorder.record_batch(
                        batch_id, header["trade_date"], header["mode"],
                        planned_orders=len(orders),
                    )
            recorder.record_orders(batch_id, orders)
            print(f"  {folder}/{path.name}: {len(orders)} orders")
            found += 1
    print(f"batches backfilled: {found}")


if __name__ == "__main__":
    main()
