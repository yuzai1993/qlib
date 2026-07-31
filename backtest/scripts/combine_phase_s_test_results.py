"""Combine the three frozen Phase S test-pool comparisons into one registry payload."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional, Sequence

from phase_s_protocol import MODEL_REFS, POOL_BENCHMARKS


def combine_payloads(model_ref: str, payloads: list[dict[str, Any]]) -> dict[str, Any]:
    pools: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        if payload.get("model_ref") != model_ref or payload.get("segment") != "test":
            raise ValueError("test payload identity mismatch")
        pool = payload.get("pool")
        if pool in pools:
            raise ValueError(f"duplicate test pool: {pool}")
        pools[pool] = payload
    if set(pools) != set(POOL_BENCHMARKS):
        raise ValueError(
            f"test pool matrix mismatch: expected={sorted(POOL_BENCHMARKS)}, actual={sorted(pools)}"
        )
    return {
        "schema_version": 1,
        "model_ref": model_ref,
        "pools": {pool: pools[pool] for pool in POOL_BENCHMARKS},
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-ref", required=True, choices=MODEL_REFS)
    parser.add_argument("--result", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in args.result]
    combined = combine_payloads(args.model_ref, payloads)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)


if __name__ == "__main__":
    main()
