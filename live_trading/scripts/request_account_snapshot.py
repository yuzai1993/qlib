#!/usr/bin/env python3
"""Create an audited broker-observation request; this never authorizes orders."""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.live_config import load_live_config
from live_trading.modules.operator_probe import (
    build_account_snapshot_request,
    prepare_account_snapshot_request,
    publish_account_snapshot_request,
    resolve_real_account_id as resolve_account_id,
)

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collector-config", required=True)
    parser.add_argument("--for-config")
    parser.add_argument("--trade-date")
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--prepare", action="store_true",
        help="persist immutable canonical bytes without exposing them to QMT",
    )
    action.add_argument(
        "--publish-request-id",
        help="expose one existing prepared request by durable ID",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.publish_request_id is None and (
        not args.for_config or not args.trade_date
    ):
        raise SystemExit("preview/prepare requires --for-config and --trade-date")
    if args.trade_date is not None and args.trade_date != date.today().isoformat():
        raise SystemExit("snapshot observation trade date must equal today")
    collector = load_live_config(
        CONFIGS_DIR / f"{args.collector_config}.yaml", PROJECT_ROOT,
    )
    account_id = resolve_account_id(collector)
    recorder = LiveRecorder(
        str(PROJECT_ROOT / collector["storage"]["db_path"]),
        fees=collector.get("fees"),
        opening_cash=collector.get("account", {}).get("opening_cash"),
        opening_value_adjustment=collector.get("account", {}).get(
            "opening_value_adjustment"
        ),
    )
    if args.publish_request_id is not None:
        if os.environ.get("SNAPSHOT_OBSERVATION_CONFIRM") != "YES":
            raise SystemExit(
                "refusing publish without SNAPSHOT_OBSERVATION_CONFIRM=YES"
            )
        path = publish_account_snapshot_request(
            args.publish_request_id, collector, recorder,
            Path(collector["live"]["bridge_root"]), account_id,
        )
        print(path)
        print("STOP: no marker was created; wait for and import QMT response")
        return
    consumer = load_live_config(
        CONFIGS_DIR / f"{args.for_config}.yaml", PROJECT_ROOT,
    )
    if collector["storage"]["db_path"] != consumer["storage"]["db_path"]:
        raise SystemExit("collector and consumer must share the account ledger")
    request = build_account_snapshot_request(
        collector,
        trade_date=args.trade_date,
        collector_execution_profile=collector["live"]["execution_session"],
        requested_for_strategy_id=consumer["live"]["strategy_id"],
        account_id=account_id,
    )
    if not args.prepare:
        print(json.dumps({
            "dry_run": True,
            "read_only_observation": True,
            "request": request.to_dict(),
        }, ensure_ascii=False, sort_keys=True))
        return
    payload = prepare_account_snapshot_request(request, recorder, account_id)
    print(json.dumps({
        "prepared": True,
        "read_only_observation": True,
        "request": payload,
    }, ensure_ascii=False, sort_keys=True))
    print("STOP: review exact prepared bytes; publish only by this request_id")


if __name__ == "__main__":
    main()
