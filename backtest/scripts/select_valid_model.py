"""Select a deployment seed strictly from a valid-segment IC artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional, Sequence


def select_best_valid_model(result: dict, *, pool: str = "csi1000") -> dict:
    if result.get("eval_segment_name") != "valid":
        raise ValueError("deployment model selection requires a valid-segment artifact")
    try:
        metrics_by_seed = result["pools"][pool]["seeds"]
    except KeyError as exc:
        raise ValueError(f"pool is missing from evaluation artifact: {pool}") from exc

    sessions = {str(item["seed"]): item["session"] for item in result["sessions"]}
    candidates: list[dict[str, Any]] = []
    for seed, metrics in metrics_by_seed.items():
        rank_ic = metrics.get("rank_ic_mean")
        if rank_ic is None or seed not in sessions:
            continue
        candidates.append(
            {
                "seed": int(seed) if str(seed).isdigit() else seed,
                "session": sessions[seed],
                "rank_ic_mean": float(rank_ic),
                "rank_icir": (
                    float(metrics["rank_icir"])
                    if metrics.get("rank_icir") is not None
                    else None
                ),
            }
        )
    if not candidates:
        raise ValueError("no valid seed metrics available for selection")

    chosen = max(
        candidates,
        key=lambda item: (
            item["rank_ic_mean"],
            item["rank_icir"] if item["rank_icir"] is not None else float("-inf"),
        ),
    )
    return {
        "selection_metric": "valid_rank_ic_mean",
        "tie_breaker": "valid_rank_icir",
        "pool": pool,
        "eval_segment": result.get("eval_segment"),
        "data_version": result.get("data_version"),
        **chosen,
        "candidates": candidates,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 valid IC 结果选择实盘单模型")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pool", default="csi1000")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    result = json.loads(args.input.read_text(encoding="utf-8"))
    selection = select_best_valid_model(result, pool=args.pool)
    selection["source"] = str(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"selected seed={selection['seed']} session={selection['session']}")
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
