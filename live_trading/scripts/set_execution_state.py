#!/usr/bin/env python3
"""Inspect or change a strategy's durable publication state."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.execution_state import (
    ExecutionStateError,
    validate_identifier,
)
from live_trading.modules.live_config import load_live_config

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="live config id")
    parser.add_argument("--get", action="store_true", help="print the current state")
    parser.add_argument(
        "--get-strategy-id", action="store_true",
        help="print the strategy ID bound to this config",
    )
    parser.add_argument("--state", choices=["ACTIVE", "PAUSED"])
    parser.add_argument("--reason", default="")
    parser.add_argument("--changed-at", default=None)
    parser.add_argument("--validate-config-id", default=None, metavar="ID")
    parser.add_argument("--validate-strategy-id", default=None, metavar="ID")
    args = parser.parse_args()
    operations = sum((
        args.get,
        args.get_strategy_id,
        args.state is not None,
        args.validate_config_id is not None,
        args.validate_strategy_id is not None,
    ))
    if operations != 1:
        parser.error("choose exactly one state operation or identifier validation")
    try:
        if args.validate_config_id is not None:
            validate_identifier(args.validate_config_id, "config")
            return args
        if args.validate_strategy_id is not None:
            validate_identifier(args.validate_strategy_id, "strategy_id")
            return args
        if args.config is None:
            parser.error("--config is required for state operations")
        validate_identifier(args.config, "config")
    except ExecutionStateError as exc:
        parser.error(str(exc))
    if args.state is not None and not args.reason.strip():
        parser.error("--reason must be nonblank for a state transition")
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
    if args.validate_config_id is not None:
        print(args.validate_config_id)
        return
    if args.validate_strategy_id is not None:
        print(args.validate_strategy_id)
        return
    config = load_live_config(CONFIGS_DIR / f"{args.config}.yaml", PROJECT_ROOT)
    recorder = _recorder(config)
    strategy_id = validate_identifier(config["live"]["strategy_id"], "strategy_id")
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
