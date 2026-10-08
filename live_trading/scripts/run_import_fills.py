#!/usr/bin/env python3
"""导入 QMT 回执并对账。

用法：
    python live_trading/scripts/run_import_fills.py \
        --config csi1000_b6m_b2s_postclose
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fees import fees_from_config
from live_trading.modules.fill_importer import FillImporter, LiveRecorder
from live_trading.modules.live_config import load_live_config
from live_trading.modules.signal_schema import SchemaError
from live_trading.scripts.next_trade_date import next_open_date

logger = logging.getLogger("live_trading.import")

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def advance_cohorts_through(recorder, business_date, *, horizon, strategy_id):
    """Catch up completed sessions in order, without using future plan dates."""
    from live_trading.modules.cohort_advance import advance_after_import

    state = recorder.load_cohort_state()
    latest = max((day for day, _ in state.layers), default="")
    if latest > business_date:
        raise SchemaError(f"future cohort layer {latest} requires audited repair")
    if latest:
        cursor = latest
    else:
        past = [b["trade_date"] for b in recorder.list_batches(limit=-1, strategy_id=strategy_id)
                if b["trade_date"] <= business_date and b.get("mode") != "SIMULATE"]
        first = min(past) if past else business_date
        cursor = (date.fromisoformat(first) - timedelta(days=1)).isoformat()
    while cursor < business_date:
        trade_date = next_open_date(cursor)
        if trade_date > business_date:
            break
        if trade_date <= cursor:
            raise SchemaError("trade calendar did not advance")
        advance_after_import(recorder, trade_date=trade_date, as_of=business_date,
                             horizon=horizon, strategy_id=strategy_id)
        print(f"cohort ladder settled for {trade_date}")
        cursor = trade_date


def main():
    p = argparse.ArgumentParser(description="Import QMT fill events")
    p.add_argument("--config", required=True, help="live config id (configs/*.yaml)")
    p.add_argument("--date", default=os.environ.get("QLIB_LIVE_BUSINESS_DATE"),
                   help="settle only through this business date (default: Shanghai today)")
    args = p.parse_args()
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.date()
    try:
        business_date = date.fromisoformat(args.date) if args.date else today
    except ValueError:
        p.error("--date must be YYYY-MM-DD")
    if business_date > today:
        p.error("cannot import against a future business date")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config = load_live_config(CONFIGS_DIR / f"{args.config}.yaml", PROJECT_ROOT)
    recorder = LiveRecorder(
        str(PROJECT_ROOT / config["storage"]["db_path"]),
        fees=fees_from_config(config),
        opening_cash=config.get("account", {}).get("opening_cash"),
        opening_value_adjustment=config.get("account", {}).get(
            "opening_value_adjustment"
        ),
    )
    importer = FillImporter(config["live"]["bridge_root"], recorder)
    strategy_id = config["live"]["strategy_id"]

    n = importer.import_fills()
    print(f"imported {n} fill events")

    snapshot_error = None
    try:
        snapshots = importer.import_broker_snapshots()
        print(f"imported {snapshots} broker account snapshots")
    except SchemaError as exc:
        snapshot_error = exc
        print(f"broker snapshot import incomplete: {exc}")
        logger.error("broker snapshot import incomplete: %s", exc)

    observations = importer.import_account_snapshot_responses()
    print(f"imported {observations} snapshot-only observations")

    for batch in recorder.list_batches(limit=5, strategy_id=strategy_id):
        r = importer.reconcile(batch["batch_id"])
        flag = "OK " if r["missing"] == 0 else "WARN"
        print(f"[{flag}] {batch['batch_id']} mode={batch['mode']} "
              f"planned={r['planned']} terminal={r['terminal']} missing={r['missing']}")

    if config["live"].get("kind") == "OPERATOR_PROBE":
        lifecycle = recorder.get_operator_probe_lifecycle(strategy_id)
        state = "NONE" if lifecycle is None else lifecycle["state"]
        print(f"probe lifecycle state={state}")

    if config.get("strategy", {}).get("class") == "CohortLadderStrategy":
        # Manual/overnight imports may read receipts, but must not settle the
        # still-open session (including a currently empty plan).
        closed_through = today if now.hour >= 15 else today - timedelta(days=1)
        cutoff = min(business_date, closed_through)
        advance_cohorts_through(recorder, cutoff.isoformat(),
                               horizon=int(config["strategy"]["horizon"]), strategy_id=strategy_id)

    positions = recorder.get_positions()
    print(f"\nlive positions ({len(positions)}), cash={recorder.get_cash():.2f}:")
    for code, pos in sorted(positions.items()):
        print(f"  {code}  {pos['shares']} shares @ {pos['avg_cost']:.3f}")

    if snapshot_error is not None:
        raise snapshot_error


if __name__ == "__main__":
    main()
