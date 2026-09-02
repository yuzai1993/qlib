#!/usr/bin/env python3
"""导入 QMT 回执并对账。

用法：
    python live_trading/scripts/run_import_fills.py \
        --config csi1000_b6m_b2s_postclose
"""

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fees import fees_from_config
from live_trading.modules.fill_importer import FillImporter, LiveRecorder
from live_trading.modules.live_config import load_live_config
from live_trading.modules.signal_schema import SchemaError

logger = logging.getLogger("live_trading.import")

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def main():
    p = argparse.ArgumentParser(description="Import QMT fill events")
    p.add_argument("--config", required=True, help="live config id (configs/*.yaml)")
    args = p.parse_args()

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
        from live_trading.modules.cohort_advance import advance_after_import

        batches = recorder.list_batches(limit=1, strategy_id=strategy_id)
        if not batches:
            print("no imported trade date; cohort ladder not advanced")
        else:
            trade_date = batches[0]["trade_date"]
            advanced = advance_after_import(
                recorder,
                trade_date=trade_date,
                horizon=int(config["strategy"]["horizon"]),
                strategy_id=strategy_id,
            )
            if advanced is None:
                print(f"cohort ladder already advanced for {trade_date}")
            else:
                print(
                    f"cohort ladder advanced to {trade_date}: "
                    f"{len(advanced.layers)} layers, "
                    f"{len(advanced.pending)} pending names"
                )

    positions = recorder.get_positions()
    print(f"\nlive positions ({len(positions)}), cash={recorder.get_cash():.2f}:")
    for code, pos in sorted(positions.items()):
        print(f"  {code}  {pos['shares']} shares @ {pos['avg_cost']:.3f}")

    if snapshot_error is not None:
        raise snapshot_error


if __name__ == "__main__":
    main()
