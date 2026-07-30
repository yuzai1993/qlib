"""Controlled CSI1000 valid evaluator for the B5 RankIC search."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

QLIB_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from backtest.scripts import eval_ic_multi_pool as evaluator  # noqa: E402
from backtest.scripts.config_loader import load_config, resolve_config_path  # noqa: E402
from backtest.scripts.freeze_b5_rankic_selection import (  # noqa: E402
    EVAL_LABEL_EXPR,
    MIN_COUNT,
    SAFE_VALID_SEGMENT,
    SEEDS,
    VALID_POOL,
    normalize_and_validate_sessions,
    validate_config_set,
    validate_valid_result,
    write_json_exclusive_atomic,
)


def _candidate_config_set(config_path: Path) -> tuple[str, dict[int, Path]]:
    config_path = Path(config_path).resolve()
    candidate = config_path.parent.name
    paths = [
        config_path.parent
        / f"mh_{candidate.replace('-', '_')}_s{seed}.yaml"
        for seed in SEEDS
    ]
    by_seed = validate_config_set(candidate, paths)
    if config_path != by_seed[SEEDS[0]]:
        raise ValueError("controlled valid evaluator requires the candidate seed-42 config")
    return candidate, by_seed


def run_valid_evaluation(
    *,
    config: str,
    sessions: Sequence[str],
    output: Path,
) -> dict:
    """Validate all local inputs, evaluate once, and exclusively publish JSON."""

    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    config_path = resolve_config_path(config)
    candidate, configs_by_seed = _candidate_config_set(config_path)
    parsed_sessions = [evaluator._parse_session(raw) for raw in sessions]
    canonical_sessions = normalize_and_validate_sessions(
        parsed_sessions,
        candidate=candidate,
        configs_by_seed=configs_by_seed,
    )
    cfg = load_config(str(config_path))

    evaluator._init_qlib(cfg)
    result = evaluator.evaluate(
        cfg,
        canonical_sessions,
        [VALID_POOL],
        segment="valid",
        eval_label_expr=EVAL_LABEL_EXPR,
        eval_label_role="fixed_1d",
        eval_end=SAFE_VALID_SEGMENT[1],
        min_count=MIN_COUNT,
    )
    result["min_count"] = MIN_COUNT
    result["candidate"] = candidate
    validate_valid_result(
        candidate,
        result,
        artifact_path=output,
        configs_by_seed=configs_by_seed,
    )
    write_json_exclusive_atomic(output, result)
    return result


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate B5 candidate on fixed safe CSI1000 valid RankIC"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--sessions",
        nargs="+",
        required=True,
        metavar="SESSION:SEED",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    run_valid_evaluation(
        config=args.config,
        sessions=args.sessions,
        output=args.output,
    )
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
