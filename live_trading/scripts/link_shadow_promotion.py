#!/usr/bin/env python3
"""Link an archived Shadow batch to its audited LIVE replacement."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.signal_schema import SchemaError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--source-batch", required=True)
    parser.add_argument("--replacement-batch", required=True)
    args = parser.parse_args(argv)

    try:
        changed = LiveRecorder(args.db_path).promote_shadow_batch(
            args.source_batch, args.replacement_batch,
        )
    except SchemaError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps({
        "changed": changed,
        "replacement_batch_id": args.replacement_batch,
        "source_batch_id": args.source_batch,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
