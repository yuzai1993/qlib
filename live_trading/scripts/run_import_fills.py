#!/usr/bin/env python3
"""导入 QMT 回执并对账。

用法：
    python live_trading/scripts/run_import_fills.py --config csi300_topk10_live
"""

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import FillImporter, LiveRecorder
from live_trading.modules.live_config import load_live_config

logger = logging.getLogger("live_trading.import")

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def main():
    p = argparse.ArgumentParser(description="Import QMT fill events")
    p.add_argument("--config", required=True, help="live config id (configs/*.yaml)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config = load_live_config(CONFIGS_DIR / f"{args.config}.yaml", PROJECT_ROOT)
    recorder = LiveRecorder(str(PROJECT_ROOT / config["storage"]["db_path"]))
    importer = FillImporter(config["live"]["bridge_root"], recorder)

    n = importer.import_fills()
    print(f"imported {n} fill events")

    for batch in recorder.list_batches(limit=5):
        r = importer.reconcile(batch["batch_id"])
        flag = "OK " if r["missing"] == 0 else "WARN"
        print(f"[{flag}] {batch['batch_id']} mode={batch['mode']} "
              f"planned={r['planned']} terminal={r['terminal']} missing={r['missing']}")

    positions = recorder.get_positions()
    print(f"\nlive positions ({len(positions)}), cash={recorder.get_cash():.2f}:")
    for code, pos in sorted(positions.items()):
        print(f"  {code}  {pos['shares']} shares @ {pos['avg_cost']:.3f}")


if __name__ == "__main__":
    main()
