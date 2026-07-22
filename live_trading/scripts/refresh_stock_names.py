#!/usr/bin/env python3
"""Refresh the Live Trading stock-name cache directly from Tushare."""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.live_config import load_live_config
from live_trading.modules.stock_names import fetch_stock_names


def main():
    parser = argparse.ArgumentParser(description="Refresh Live stock names")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required")

    import tushare as ts

    config = load_live_config(
        PROJECT_ROOT / "live_trading" / "configs" / f"{args.config}.yaml",
        PROJECT_ROOT,
    )
    recorder = LiveRecorder(str(PROJECT_ROOT / config["storage"]["db_path"]))
    rows = fetch_stock_names(ts.pro_api(token))
    recorder.save_stock_names(rows)
    print(f"stock names refreshed: {len(rows)}")


if __name__ == "__main__":
    main()
