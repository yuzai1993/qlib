#!/usr/bin/env python3
"""Inspect or safely archive one claimed, unexecuted Shadow batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.shadow_promotion import retire_claimed_shadow
from live_trading.modules.signal_schema import SchemaError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-root", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument(
        "--execute", action="store_true",
        help="archive verified files; omission performs a dry-run",
    )
    args = parser.parse_args(argv)

    try:
        result = retire_claimed_shadow(
            Path(args.bridge_root), args.batch_id, execute=args.execute,
        )
    except (SchemaError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
