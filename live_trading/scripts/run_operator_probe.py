#!/usr/bin/env python3
"""Preview or publish one audited, one-lot fixed-price operator probe."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.live_config import load_live_config
from live_trading.modules.operator_probe import (
    OperatorProbeRequest,
    preview_operator_probe,
    publish_operator_probe,
    resolve_real_account_id as resolve_account_id,
)
from live_trading.modules.signal_publisher import SignalPublisher

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="live config id")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--side", required=True, choices=["BUY", "SELL"])
    parser.add_argument("--quantity", type=int, default=100)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--eligibility-confirmed", action="store_true",
        help="confirm the operator independently checked BUY eligibility",
    )
    parser.add_argument(
        "--publish", action="store_true",
        help="write the recorded plan and QMT inbox files (requires confirmation)",
    )
    return parser.parse_args()


def _recorder(config: dict, *, read_only: bool) -> LiveRecorder:
    return LiveRecorder(
        str(PROJECT_ROOT / config["storage"]["db_path"]),
        fees=config.get("fees"),
        opening_cash=config.get("account", {}).get("opening_cash"),
        opening_value_adjustment=config.get("account", {}).get(
            "opening_value_adjustment"
        ),
        read_only=read_only,
    )


def _require_writable_bridge_root(bridge_root: str) -> Path:
    root = Path(bridge_root)
    if not root.is_dir() or not os.access(root, os.W_OK | os.X_OK):
        raise SystemExit(f"probe bridge root is not writable: {root}")
    return root


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_live_config(CONFIGS_DIR / f"{args.config}.yaml", PROJECT_ROOT)
    account_id = resolve_account_id(config)
    request = OperatorProbeRequest(
        config_id=args.config,
        trade_date=args.trade_date,
        stock_code=args.stock_code,
        side=args.side,
        quantity=args.quantity,
        reason=args.reason,
        eligibility_confirmed=getattr(args, "eligibility_confirmed", False),
    )

    if not args.publish:
        # Opening the ledger read-only is deliberately the only I/O on a
        # preview path: no SQLite journal, no inbox directory and no SMB file.
        header, order = preview_operator_probe(
            request, config, _recorder(config, read_only=True), account_id,
        )
        print(json.dumps({
            "dry_run": True,
            "header": json.loads(header.to_json_line()),
            "order": json.loads(order.to_json_line()),
            "checksum": header.checksum,
        }, ensure_ascii=False, sort_keys=True))
        return

    if os.environ.get("LIVE_TRADING_CONFIRM") != "YES":
        raise SystemExit("refusing --publish without LIVE_TRADING_CONFIRM=YES")
    bridge_root = _require_writable_bridge_root(config["live"]["bridge_root"])
    path = publish_operator_probe(
        request,
        config,
        _recorder(config, read_only=False),
        SignalPublisher(bridge_root),
        account_id,
    )
    print(path)


if __name__ == "__main__":
    main()
