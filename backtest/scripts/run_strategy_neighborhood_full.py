"""Run the B2-S neighborhood once on the frozen B6-M CSI1000 full history."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import pandas as pd
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import load_config  # noqa: E402
from eval_protocol import yearly_ir  # noqa: E402
from generate_phase_s_predictions import prediction_index_sha256  # noqa: E402
import phase_s_prediction_validation as full_prediction_validation  # noqa: E402
from phase_s_protocol import (  # noqa: E402
    ACCOUNT,
    EXCHANGE_KWARGS,
    FULL_SEGMENT,
    POOL_BENCHMARKS,
    sha256_file,
)
from run_strategy_neighborhood import (  # noqa: E402
    build_checkpoint_contract,
    upsert_result,
    validate_checkpoint_contract,
    write_json_atomic,
)
from run_strategy_sweep import (  # noqa: E402
    _parse_result_dir,
    build_backtest_command,
    build_sweep_config,
    classify_strategy_outcome,
)
from strategy_neighborhood_protocol import (  # noqa: E402
    score_valid_candidates,
    strategy_neighborhood_grid,
)
from strategy_stability_metrics import (  # noqa: E402
    IncompletePortfolioError,
    summarize_period,
)

EXP_ID = "strategy-neighborhood/b2-s-local-full-v2"
EVALUATION_MODE = "full_history_in_sample"
MODEL_REF = "b6-m"
POOL = "csi1000"
SEGMENT = "full"
DATA_VERSION = full_prediction_validation.FULL_DATA_VERSION
FULL_PREDICTION_COVERAGE = copy.deepcopy(
    full_prediction_validation.FULL_PREDICTION_COVERAGE
)
DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT / "backtest/experiments/strategy-neighborhood/20260802_b2s_local_full"
)
DEFAULT_CONFIGS_DIR = (
    REPO_ROOT / "backtest/configs/strategy-neighborhood/b2-s-local-full"
)
DEFAULT_BASE_CONFIG = (
    REPO_ROOT
    / "backtest/configs/baseline-strategy/b2-s/topk-t30-d2-h20_csi1000_full.yaml"
)
DEFAULT_PREDICTION_MANIFEST = (
    REPO_ROOT
    / "backtest/experiments/strategy-stability/20260801_full_period"
    / "prediction_manifest.json"
)
SELECTION_RULE = [
    "axial_neighbor_ir_p25 desc",
    "own_ir desc",
    "annualized_return desc",
    "max_drawdown desc",
    "annualized_one_way_turnover asc",
    "candidate_id asc",
]


def _repo_path(path: Path) -> str:
    resolved = Path(path).expanduser().resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(resolved)


def _prediction_path(entry: dict[str, Any]) -> Path:
    return full_prediction_validation.prediction_path(entry, REPO_ROOT)


def protocol_payload(
    grid: Sequence[dict[str, Any]], base_config_path: Path
) -> dict[str, Any]:
    """Build the immutable v2 full-history selection protocol."""
    candidates = [copy.deepcopy(candidate) for candidate in grid]
    if candidates != strategy_neighborhood_grid():
        raise ValueError(
            "full neighborhood protocol requires the immutable 540-candidate grid"
        )
    base_path = Path(base_config_path).expanduser().resolve()
    return {
        "schema_version": 2,
        "exp_id": EXP_ID,
        "direction": "strategy-neighborhood-b2-s-full",
        "phase": "S",
        "evaluation_mode": EVALUATION_MODE,
        "baseline_ref": "B2-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "model_ref": MODEL_REF,
        "account": ACCOUNT,
        "fees": copy.deepcopy(EXCHANGE_KWARGS),
        "benchmark": POOL_BENCHMARKS[POOL],
        "base_config": _repo_path(base_path),
        "base_config_sha256": sha256_file(base_path),
        "selection_pool": POOL,
        "selection_segment": list(FULL_SEGMENT),
        "strategy_grid": candidates,
        "selection_rule": list(SELECTION_RULE),
    }


def full_prediction_entry(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the sole legal full-period prediction entry, failing closed."""
    return full_prediction_validation.full_prediction_entry(manifest)


def _prediction_frame(path: Path) -> pd.DataFrame:
    return full_prediction_validation.prediction_frame(path)


def _authoritative_full_prediction_entry() -> dict[str, Any]:
    """Load coverage identity from the tracked full-period stability manifest."""
    manifest_path = DEFAULT_PREDICTION_MANIFEST.expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return full_prediction_entry(manifest)


def validate_prediction_artifact(entry: dict[str, Any], path: Path) -> dict[str, Any]:
    """Verify the score against its entry and the tracked coverage authority."""
    actual = full_prediction_validation.validate_prediction_artifact(
        entry,
        path,
        repo_root=REPO_ROOT,
        expected_coverage=FULL_PREDICTION_COVERAGE,
    )
    declared = entry.get("coverage") or {}
    authoritative = _authoritative_full_prediction_entry()
    same_identity = (
        (
            entry.get("model_ref"),
            entry.get("pool"),
            entry.get("segment"),
        )
        == (MODEL_REF, POOL, SEGMENT)
        and _prediction_path(entry) == _prediction_path(authoritative)
        and entry.get("prediction_sha256") == authoritative.get("prediction_sha256")
        and declared == (authoritative.get("coverage") or {})
    )
    if not same_identity:
        raise ValueError(
            "full prediction differs from authoritative tracked manifest: "
            f"{_repo_path(DEFAULT_PREDICTION_MANIFEST)}"
        )
    return actual


def validate_full_prediction_manifest(
    manifest: dict[str, Any], manifest_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the one authoritative B6-M full prediction and its dataframe."""
    return full_prediction_validation.validate_full_prediction_manifest(
        manifest,
        manifest_path,
        repo_root=REPO_ROOT,
        authoritative_manifest_path=DEFAULT_PREDICTION_MANIFEST,
        expected_coverage=FULL_PREDICTION_COVERAGE,
    )


def effective_config_sha256(base: dict[str, Any], candidate: dict[str, Any]) -> str:
    rendered = yaml.safe_dump(
        build_sweep_config(base, candidate, pool=POOL, segment=SEGMENT),
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def pending_candidates(
    grid: Sequence[dict[str, Any]],
    checkpoint: Optional[dict[str, Any]],
    *,
    base: dict[str, Any],
    prediction_sha256: str,
) -> list[dict[str, Any]]:
    """Resume only rows whose prediction and rendered full config still match."""
    completed = set()
    by_id = {str(candidate["candidate_id"]): candidate for candidate in grid}
    for row in (checkpoint or {}).get("all_rows") or []:
        candidate_id = str(row.get("candidate_id") or "")
        candidate = by_id.get(candidate_id)
        if candidate is None or row.get("status") != "success":
            continue
        if row.get("source_pred_sha256") != prediction_sha256:
            continue
        if row.get("effective_config_sha256") != effective_config_sha256(
            base, candidate
        ):
            continue
        completed.add(candidate_id)
    return [
        copy.deepcopy(candidate)
        for candidate in grid
        if str(candidate["candidate_id"]) not in completed
    ]


def render_full_config(
    base: dict[str, Any],
    candidate: dict[str, Any],
    *,
    configs_dir: Path,
) -> tuple[Path, str]:
    config_path = Path(configs_dir) / f"{candidate['candidate_id']}_csi1000_full.yaml"
    rendered = yaml.safe_dump(
        build_sweep_config(base, candidate, pool=POOL, segment=SEGMENT),
        allow_unicode=True,
        sort_keys=False,
    )
    return config_path, rendered


def _load_report(path: Path) -> pd.DataFrame:
    report = pd.read_csv(path, parse_dates=["datetime"])
    return report.set_index("datetime").sort_index()


def load_result_metrics(result_dir: Path) -> dict[str, Any]:
    """Load selection metrics plus full absolute-return and yearly excess diagnostics."""
    run_dir = Path(result_dir) / "run_01"
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    report_path = run_dir / "report_normal.csv"
    report = _load_report(report_path)
    annual = yearly_ir(report_path)
    metrics["yearly_ir"] = {
        str(int(year)): float(value) for year, value in annual.items()
    }
    metrics["absolute_portfolio"] = summarize_period(report)
    return metrics


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _run_candidate(
    base: dict[str, Any],
    candidate: dict[str, Any],
    *,
    pred_path: Path,
    prediction_entry: dict[str, Any],
    configs_dir: Path,
) -> dict[str, Any]:
    config_path, rendered = render_full_config(base, candidate, configs_dir=configs_dir)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(rendered, encoding="utf-8")
    note = f"strategy_neighborhood_full_{candidate['candidate_id']}_csi1000_full"
    command = build_backtest_command(
        Path(sys.executable),
        SCRIPT_DIR / "run_pred_backtest.py",
        pred_path,
        config_path,
        note,
    )
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
    row: dict[str, Any] = {
        **candidate,
        "status": "failed",
        "source_pred": _repo_path(pred_path),
        "source_pred_sha256": prediction_entry["prediction_sha256"],
        "effective_config_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "returncode": completed.returncode,
        "config": _repo_path(config_path),
    }
    try:
        result_dir = _parse_result_dir(completed.stdout)
        row["result_dir"] = _repo_path(result_dir)
        meta = json.loads((result_dir / "meta.json").read_text(encoding="utf-8"))
        if meta.get("source_pred_sha256") != prediction_entry["prediction_sha256"]:
            raise ValueError("session prediction SHA differs from frozen prediction")
        metrics = load_result_metrics(result_dir)
        metrics_status = metrics.pop("status", None)
        if metrics_status != "success":
            raise ValueError(
                f"backtest metrics status must be success: {metrics_status!r}"
            )
        row.update(metrics)
        row["status"] = "success"
        classify_strategy_outcome(row)
    except Exception as exc:
        row.update(
            status="invalid" if isinstance(exc, IncompletePortfolioError) else "failed",
            error=str(exc),
        )
        if completed.stderr:
            row["error"] += f"\n{completed.stderr[-2000:]}"
    if completed.returncode != 0:
        row.update(
            status="failed",
            error=completed.stderr[-2000:] or "backtest subprocess failed",
        )
    return _json_safe(row)


def run_bounded_batches(
    candidates: Sequence[dict[str, Any]],
    *,
    run_candidate: Callable[[dict[str, Any]], dict[str, Any]],
    checkpoint: Callable[[dict[str, Any]], None],
    workers: int = 3,
    max_runtime_hours: Optional[float] = None,
) -> None:
    """Run worker-sized batches and checkpoint completed futures on the caller."""
    if workers < 1 or workers > 3:
        raise ValueError("workers must be between 1 and 3")
    started = time.monotonic()
    for offset in range(0, len(candidates), workers):
        if (
            max_runtime_hours is not None
            and (time.monotonic() - started) / 3600 >= max_runtime_hours
        ):
            raise RuntimeError("runtime budget reached before full grid completed")
        batch = candidates[offset : offset + workers]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(run_candidate, candidate): copy.deepcopy(candidate)
                for candidate in batch
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        **candidate,
                        "status": "failed",
                        "returncode": None,
                        "error": f"worker exception: {type(exc).__name__}: {exc}",
                    }
                checkpoint(_json_safe(row))


def completed_results_payload(
    rows: Sequence[dict[str, Any]],
    grid: Sequence[dict[str, Any]],
    *,
    protocol_sha256: str,
    run_contract: dict[str, str],
) -> dict[str, Any]:
    expected_ids = {str(candidate["candidate_id"]) for candidate in grid}
    actual_ids = {str(row.get("candidate_id") or "") for row in rows}
    if (
        len(grid) != 540
        or len(rows) != 540
        or actual_ids != expected_ids
        or any(row.get("status") != "success" for row in rows)
    ):
        raise ValueError("full neighborhood requires exactly 540 successful rows")
    scored, winner = score_valid_candidates(rows, grid)
    if not all(row.get("neighborhood_complete") for row in scored):
        raise ValueError(
            "full neighborhood requires finite metrics for all 540 successful rows"
        )
    return {
        "schema_version": 2,
        "state": "full_complete",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "exp_id": EXP_ID,
        "evaluation_mode": EVALUATION_MODE,
        "protocol_sha256": protocol_sha256,
        "run_contract": copy.deepcopy(run_contract),
        "model_ref": MODEL_REF,
        "pool": POOL,
        "segment": SEGMENT,
        "selection_segment": list(FULL_SEGMENT),
        "all_rows": scored,
        "winner": winner,
    }


def validate_completed_checkpoint(
    checkpoint: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    grid: Sequence[dict[str, Any]],
    *,
    protocol_sha256: str,
    run_contract: dict[str, str],
    manifest_path: Path,
    base_config_path: Path,
    prediction_entry: dict[str, Any],
    prediction_path: Path,
) -> None:
    """Fail closed unless an immutable completed checkpoint matches all inputs."""
    recomputed = completed_results_payload(
        rows,
        grid,
        protocol_sha256=protocol_sha256,
        run_contract=run_contract,
    )
    immutable_keys = (
        "schema_version",
        "state",
        "exp_id",
        "evaluation_mode",
        "protocol_sha256",
        "run_contract",
        "model_ref",
        "pool",
        "segment",
        "selection_segment",
        "all_rows",
        "winner",
    )
    for key in immutable_keys:
        if checkpoint.get(key) != recomputed.get(key):
            raise ValueError(f"completed full checkpoint {key} differs from recomputation")
    expected_artifacts = {
        "prediction_manifest": _repo_path(manifest_path),
        "base_config": _repo_path(base_config_path),
        "source_pred": _repo_path(prediction_path),
        "source_pred_sha256": prediction_entry["prediction_sha256"],
    }
    for key, expected in expected_artifacts.items():
        if checkpoint.get(key) != expected:
            raise ValueError(f"completed full checkpoint {key} differs from current inputs")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prediction-manifest", required=True, type=Path)
    parser.add_argument("--base-config", type=Path, default=DEFAULT_BASE_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--configs-dir", type=Path, default=DEFAULT_CONFIGS_DIR)
    parser.add_argument("--max-runtime-hours", type=float, default=5.0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    if args.workers < 1 or args.workers > 3:
        parser.error("--workers must be between 1 and 3")
    return args


def _running_payload(
    *,
    rows: Sequence[dict[str, Any]],
    protocol_sha256: str,
    run_contract: dict[str, str],
    manifest_path: Path,
    base_config_path: Path,
    prediction_entry: dict[str, Any],
    prediction_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "state": "running",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "exp_id": EXP_ID,
        "evaluation_mode": EVALUATION_MODE,
        "protocol_sha256": protocol_sha256,
        "run_contract": copy.deepcopy(run_contract),
        "prediction_manifest": _repo_path(manifest_path),
        "base_config": _repo_path(base_config_path),
        "source_pred": _repo_path(prediction_path),
        "source_pred_sha256": prediction_entry["prediction_sha256"],
        "model_ref": MODEL_REF,
        "pool": POOL,
        "segment": SEGMENT,
        "selection_segment": list(FULL_SEGMENT),
        "all_rows": list(rows),
    }


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    output_root = args.output_root.expanduser().resolve()
    configs_dir = args.configs_dir.expanduser().resolve()
    manifest_path = args.prediction_manifest.expanduser().resolve()
    base_config_path = args.base_config.expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verified_entry, coverage = validate_full_prediction_manifest(
        manifest, manifest_path
    )
    pred_path = _prediction_path(verified_entry)
    grid = strategy_neighborhood_grid()
    protocol = protocol_payload(grid, base_config_path)
    protocol_path = output_root / "protocol.json"
    results_path = output_root / "full_results.json"
    checkpoint_payload = (
        json.loads(results_path.read_text(encoding="utf-8"))
        if results_path.exists()
        else {}
    )
    if protocol_path.exists():
        if json.loads(protocol_path.read_text(encoding="utf-8")) != protocol:
            raise ValueError(
                "existing protocol differs from preregistered full protocol"
            )
    else:
        if (
            checkpoint_payload.get("state") == "full_complete"
            and not args.prepare_only
        ):
            raise ValueError(
                "completed full checkpoint requires its existing protocol; "
                "no files rewritten"
            )
        write_json_atomic(protocol_path, protocol)
    protocol_sha = sha256_file(protocol_path)
    print(
        f"protocol: {protocol_path} ({protocol_sha}); "
        f"prediction: {verified_entry['prediction_sha256']}; coverage: {coverage}",
        flush=True,
    )
    if args.prepare_only:
        return

    base = load_config(str(base_config_path))
    run_contract = build_checkpoint_contract(
        protocol_sha, manifest_path, base_config_path
    )
    validate_checkpoint_contract(checkpoint_payload, run_contract, "full")
    rows = list(checkpoint_payload.get("all_rows") or [])
    pending = pending_candidates(
        grid,
        checkpoint_payload,
        base=base,
        prediction_sha256=str(verified_entry["prediction_sha256"]),
    )
    if checkpoint_payload.get("state") == "full_complete":
        if pending:
            raise ValueError(
                "completed full checkpoint contains stale or missing candidate rows"
            )
        validate_completed_checkpoint(
            checkpoint_payload,
            rows,
            grid,
            protocol_sha256=protocol_sha,
            run_contract=run_contract,
            manifest_path=manifest_path,
            base_config_path=base_config_path,
            prediction_entry=verified_entry,
            prediction_path=pred_path,
        )
        print(
            f"status: already full_complete; no files rewritten: {results_path}",
            flush=True,
        )
        return
    started = time.monotonic()

    def run_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        return _run_candidate(
            base,
            candidate,
            pred_path=pred_path,
            prediction_entry=verified_entry,
            configs_dir=configs_dir,
        )

    completed_count = 0

    def checkpoint(row: dict[str, Any]) -> None:
        nonlocal rows, completed_count
        rows = upsert_result(rows, row, grid)
        completed_count += 1
        write_json_atomic(
            results_path,
            _running_payload(
                rows=rows,
                protocol_sha256=protocol_sha,
                run_contract=run_contract,
                manifest_path=manifest_path,
                base_config_path=base_config_path,
                prediction_entry=verified_entry,
                prediction_path=pred_path,
            ),
        )
        print(
            f"[{completed_count}/{len(pending)}] {row['candidate_id']} "
            f"status={row.get('status')} "
            f"elapsed={(time.monotonic() - started) / 3600:.2f}h",
            flush=True,
        )

    run_bounded_batches(
        pending,
        run_candidate=run_candidate,
        checkpoint=checkpoint,
        workers=args.workers,
        max_runtime_hours=args.max_runtime_hours,
    )
    complete = completed_results_payload(
        rows,
        grid,
        protocol_sha256=protocol_sha,
        run_contract=run_contract,
    )
    complete.update(
        {
            "prediction_manifest": _repo_path(manifest_path),
            "base_config": _repo_path(base_config_path),
            "source_pred": _repo_path(pred_path),
            "source_pred_sha256": verified_entry["prediction_sha256"],
        }
    )
    write_json_atomic(results_path, complete)
    print(f"full winner: {complete['winner']['candidate_id']}", flush=True)
    print(f"results: {results_path}", flush=True)


if __name__ == "__main__":
    main()
