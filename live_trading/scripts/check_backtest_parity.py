#!/usr/bin/env python3
"""Validate one Live config against its designated parity Backtest."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.backtest_parity import validate_configured_backtest
from live_trading.modules.live_config import load_live_config


def main():
    parser = argparse.ArgumentParser(description="Check Live/Backtest parity")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    live_path = (
        PROJECT_ROOT / "live_trading" / "configs" / f"{args.config}.yaml"
    )
    live = load_live_config(live_path, PROJECT_ROOT)
    backtest_path = validate_configured_backtest(live, PROJECT_ROOT)
    print(f"parity OK: {live_path} <-> {backtest_path}")


if __name__ == "__main__":
    main()
