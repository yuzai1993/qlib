"""Guarded one-shot test evaluator for the frozen B5 RankIC winner."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

QLIB_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from backtest.scripts import eval_ic_multi_pool as evaluator  # noqa: E402
from backtest.scripts.config_loader import load_config  # noqa: E402
from backtest.scripts.freeze_b5_rankic_selection import (  # noqa: E402
    CANDIDATES,
    EVAL_LABEL_EXPR,
    MIN_COUNT,
    SEEDS,
    TEST_POOLS,
    TEST_SEGMENT,
    select_candidate,
    sha256_file,
    write_json_exclusive_atomic,
)


def _fail(message: str) -> None:
    raise ValueError(message)


def _manifest_sources(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, list[Path]], dict[str, Path]]:
    config_rows = manifest.get("config_hashes")
    valid_rows = manifest.get("valid_result_hashes")
    if not isinstance(config_rows, list) or len(config_rows) != 20:
        _fail("selection manifest must contain exactly 20 config hashes")
    if not isinstance(valid_rows, dict) or set(valid_rows) != set(CANDIDATES):
        _fail("selection manifest must contain all four valid artifact hashes")

    config_paths = {candidate: [] for candidate in CANDIDATES}
    seen: set[tuple[str, int]] = set()
    for row in config_rows:
        if not isinstance(row, dict):
            _fail("selection manifest config hash row invalid")
        candidate = row.get("candidate")
        try:
            seed = int(row.get("seed"))
        except (TypeError, ValueError):
            _fail("selection manifest config seed invalid")
        key = (candidate, seed)
        if candidate not in config_paths or seed not in SEEDS or key in seen:
            _fail("selection manifest config candidate/seed set invalid")
        seen.add(key)
        path = Path(str(row.get("path"))).expanduser().resolve()
        if sha256_file(path) != row.get("sha256"):
            _fail(f"selection manifest config hash mismatch: {path}")
        config_paths[candidate].append(path)
    if seen != {(candidate, seed) for candidate in CANDIDATES for seed in SEEDS}:
        _fail("selection manifest config candidate/seed matrix incomplete")

    valid_paths = {}
    for candidate in CANDIDATES:
        row = valid_rows[candidate]
        if not isinstance(row, dict):
            _fail("selection manifest valid artifact hash row invalid")
        path = Path(str(row.get("path"))).expanduser().resolve()
        if sha256_file(path) != row.get("sha256"):
            _fail(f"selection manifest valid artifact hash mismatch: {path}")
        valid_paths[candidate] = path
    return config_paths, valid_paths


def verify_manifest(manifest: Mapping[str, Any]) -> dict:
    """Re-hash all sources and reproduce the winner byte-for-byte in memory."""

    if (
        manifest.get("schema_version") != 1
        or manifest.get("frozen_before_test") is not True
        or manifest.get("test_metrics_opened") is not False
    ):
        _fail("selection manifest freeze state invalid")
    config_paths, valid_paths = _manifest_sources(manifest)
    valid_results = {}
    for candidate, path in valid_paths.items():
        try:
            valid_results[candidate] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(f"selection manifest valid artifact cannot be loaded: {path}: {exc}")
    recomputed = select_candidate(
        valid_results,
        config_paths,
        valid_paths,
        generated_at=manifest.get("generated_at"),
    )
    if recomputed != manifest:
        _fail("selection manifest differs from recomputed valid-only winner")
    return recomputed


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        _fail(f"{label} must be finite")
    if not math.isfinite(number):
        _fail(f"{label} must be finite")
    return number


def _check_mean(actual: Any, values: list[float], label: str) -> None:
    expected = sum(values) / len(values)
    number = _finite(actual, label)
    if not math.isclose(number, expected, rel_tol=1e-12, abs_tol=1e-15):
        _fail(f"{label} differs from recomputed seed mean")


def validate_test_result(result: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    if result.get("eval_segment_name") != "test":
        _fail("frozen evaluation did not return test segment")
    for key in ("eval_segment", "effective_eval_segment", "test_segment"):
        value = result.get(key)
        if not isinstance(value, (list, tuple)) or tuple(map(str, value)) != TEST_SEGMENT:
            _fail(f"frozen test result {key} drift")
    if (
        result.get("eval_label") != EVAL_LABEL_EXPR
        or result.get("eval_label_role") != "fixed_1d"
        or result.get("min_count") != MIN_COUNT
    ):
        _fail("frozen test result label/min_count protocol drift")
    if result.get("data_version") != manifest.get("data_version"):
        _fail("frozen test result data_version differs from valid selection")

    expected_sessions = manifest["selected_sessions"]
    if result.get("sessions") != expected_sessions:
        _fail("frozen test result sessions differ from selected sessions")
    expected_config = next(
        row["path"]
        for row in manifest["config_hashes"]
        if row["candidate"] == manifest["selected_candidate"] and row["seed"] == SEEDS[0]
    )
    if Path(str(result.get("config"))).expanduser().resolve() != Path(expected_config):
        _fail("frozen test result config differs from selected candidate")

    pools = result.get("pools")
    if not isinstance(pools, dict) or list(pools) != list(TEST_POOLS):
        _fail("frozen test result pools/order differ from official test pools")
    for pool_name in TEST_POOLS:
        pool = pools[pool_name]
        if not isinstance(pool, dict):
            _fail(f"frozen test result pool payload missing: {pool_name}")
        seed_rows = pool.get("seeds")
        if not isinstance(seed_rows, dict) or set(seed_rows) != {str(s) for s in SEEDS}:
            _fail(f"frozen test result seed set invalid: {pool_name}")
        metric_values = {
            key: []
            for key in ("ic_mean", "icir", "rank_ic_mean", "rank_icir")
        }
        for seed in SEEDS:
            row = seed_rows[str(seed)]
            if not isinstance(row, dict) or int(row.get("n_days", 0)) <= 0:
                _fail(f"frozen test result seed metrics missing: {pool_name}/{seed}")
            for key in metric_values:
                metric_values[key].append(
                    _finite(row.get(key), f"{pool_name}/{seed} {key} metric")
                )
        seed_mean = pool.get("seed_mean")
        if not isinstance(seed_mean, dict):
            _fail(f"frozen test result seed_mean missing: {pool_name}")
        for key, values in metric_values.items():
            _check_mean(
                seed_mean.get(key),
                values,
                f"{pool_name} {key} mean",
            )


def run_frozen_evaluation(*, manifest: Path, output: Path) -> dict:
    """Verify the complete selection chain before initializing Qlib."""

    manifest = Path(manifest)
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    manifest_raw = manifest.read_bytes()
    try:
        manifest_data = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"selection manifest cannot be loaded: {manifest}: {exc}")
    verified = verify_manifest(manifest_data)
    winner = verified["selected_candidate"]
    config_path = next(
        Path(row["path"])
        for row in verified["config_hashes"]
        if row["candidate"] == winner and row["seed"] == SEEDS[0]
    )
    sessions = [
        (row["session"], int(row["seed"]))
        for row in verified["selected_sessions"]
    ]
    cfg = load_config(str(config_path))

    evaluator._init_qlib(cfg)
    result = evaluator.evaluate(
        cfg,
        sessions,
        list(TEST_POOLS),
        segment="test",
        eval_label_expr=EVAL_LABEL_EXPR,
        eval_label_role="fixed_1d",
        eval_end=None,
        min_count=MIN_COUNT,
    )
    result["min_count"] = MIN_COUNT
    result["selection_manifest_sha256"] = sha256_file(manifest)
    result["selected_candidate"] = winner
    validate_test_result(result, verified)
    write_json_exclusive_atomic(output, result)
    return result


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate only the frozen B5 valid-RankIC winner on test"
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    result = run_frozen_evaluation(manifest=args.manifest, output=args.output)
    print(
        f"written: {args.output} "
        f"(selected {result['selected_candidate']})"
    )


if __name__ == "__main__":
    main()
