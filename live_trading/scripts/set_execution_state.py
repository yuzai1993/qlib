#!/usr/bin/env python3
"""Inspect or change a strategy's durable publication state."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.live_config import load_live_config

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="live config id")
    parser.add_argument("--get", action="store_true", help="print the current state")
    parser.add_argument(
        "--get-strategy-id", action="store_true",
        help="print the strategy ID bound to this config",
    )
    parser.add_argument("--state", choices=["ACTIVE", "PAUSED"])
    parser.add_argument("--reason", default="")
    parser.add_argument("--changed-at", default=None)
    args = parser.parse_args()
    operations = sum((args.get, args.get_strategy_id, args.state is not None))
    if operations != 1:
        parser.error("choose exactly one of --get, --get-strategy-id, or --state")
    return args


def _recorder(config: dict) -> LiveRecorder:
    return LiveRecorder(
        str(PROJECT_ROOT / config["storage"]["db_path"]),
        fees=config.get("fees"),
        opening_cash=config.get("account", {}).get("opening_cash"),
        opening_value_adjustment=config.get("account", {}).get(
            "opening_value_adjustment",
        ),
    )


def main():
    args = parse_args()
    config = load_live_config(CONFIGS_DIR / f"{args.config}.yaml", PROJECT_ROOT)
    recorder = _recorder(config)
    strategy_id = config["live"]["strategy_id"]
    if args.get_strategy_id:
        print(strategy_id)
        return
    if args.get:
        print(recorder.get_execution_state(strategy_id)["state"])
        return
    state = recorder.set_execution_state(
        strategy_id, args.state, args.reason, args.changed_at,
    )
    print(state["state"])


if __name__ == "__main__":
    main()
