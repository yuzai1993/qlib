#!/usr/bin/env python3
"""Read-only lookup of the latest active batch for one trade date."""

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.live_config import load_live_config


def _query_active_batch_id(db_path, trade_date: str) -> str:
    path = Path(db_path)
    if not path.is_file():
        return ""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT batch_id FROM batches "
            "WHERE trade_date=? AND superseded_by IS NULL "
            "ORDER BY batch_id DESC LIMIT 1",
            (trade_date,),
        ).fetchone()
    return str(row[0]) if row else ""


def find_active_batch_id(db_path, trade_date: str) -> str:
    """Convenience API: missing/uninitialized databases have no active batch."""
    try:
        return _query_active_batch_id(db_path, trade_date)
    except sqlite3.Error:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--trade-date", required=True)
    args = parser.parse_args()

    config_path = PROJECT_ROOT / "live_trading" / "configs" / f"{args.config}.yaml"
    config = load_live_config(config_path, PROJECT_ROOT)
    db_path = PROJECT_ROOT / config["storage"]["db_path"]
    try:
        batch_id = _query_active_batch_id(db_path, args.trade_date)
    except sqlite3.Error as exc:
        print(f"batch status database error: {exc}", file=sys.stderr)
        return 2
    print(batch_id)
    return 0 if batch_id else 1


if __name__ == "__main__":
    raise SystemExit(main())
