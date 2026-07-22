#!/usr/bin/env python3
"""回填历史预测分数到 live 账本 predictions 表。

默认回填 batches 表中出现过、但 predictions 表尚缺的全部 signal_date；
也可用 --dates 显式指定日期。

用法：
    python live_trading/scripts/backfill_predictions.py --config csi300_topk10_live
    python live_trading/scripts/backfill_predictions.py --config csi300_topk10_live \
        --dates 2026-07-15 2026-07-16 [--force]
"""

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.live_config import load_live_config

logger = logging.getLogger("live_trading.backfill_predictions")

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def parse_args():
    p = argparse.ArgumentParser(description="Backfill prediction scores")
    p.add_argument("--config", required=True, help="live config id (configs/*.yaml)")
    p.add_argument("--dates", nargs="*", default=None,
                   help="signal dates YYYY-MM-DD; default: all batch signal dates")
    p.add_argument("--force", action="store_true",
                   help="recompute even if the date already has predictions")
    return p.parse_args()


def collect_signal_dates(recorder: LiveRecorder) -> list:
    with recorder._conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT signal_date FROM batches "
            "WHERE signal_date IS NOT NULL AND signal_date != '' "
            "ORDER BY signal_date"
        ).fetchall()
        return [r["signal_date"] for r in rows]


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config = load_live_config(CONFIGS_DIR / f"{args.config}.yaml", PROJECT_ROOT)
    recorder = LiveRecorder(str(PROJECT_ROOT / config["storage"]["db_path"]))

    dates = args.dates or collect_signal_dates(recorder)
    if not dates:
        logger.info("no signal dates found, nothing to backfill")
        return
    if not args.force:
        existing = set(recorder.get_prediction_dates())
        dates = [d for d in dates if d not in existing]
    dates = sorted(set(dates))
    if not dates:
        logger.info("all signal dates already have predictions")
        return
    logger.info("backfilling %d dates: %s", len(dates), ", ".join(dates))

    import qlib
    qlib.init(
        provider_uri=str(Path(config["data"]["qlib_dir"]).expanduser()),
        region=config["data"]["region"],
    )

    from live_trading.modules.signal_generator import SignalGenerator
    gen = SignalGenerator(config, PROJECT_ROOT)
    gen.prepare_for_dates(max(dates))

    ok, failed = 0, []
    for d in dates:
        try:
            scores = gen.predict(d, allow_stale=False)
        except Exception as e:
            logger.error("predict failed for %s: %s", d, e)
            failed.append(d)
            continue
        saved = recorder.save_predictions(d, scores)
        logger.info("saved %d scores for %s", saved, d)
        ok += 1

    logger.info("backfill done: %d ok, %d failed%s",
                ok, len(failed), f" ({', '.join(failed)})" if failed else "")


if __name__ == "__main__":
    main()
