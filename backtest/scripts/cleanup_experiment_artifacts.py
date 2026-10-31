"""Plan or apply registry-driven cleanup of MLflow and backtest result artifacts."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

import yaml

if __package__:
    from .baseline_references import load_historical_baselines
    from .experiment_archive import classify_experiment
    from .research_layout import resolve_research_path
else:
    from baseline_references import load_historical_baselines
    from experiment_archive import classify_experiment
    from research_layout import resolve_research_path

if __package__:
    from .experiment_registry import (
        load_registry as _load_registry_rows,
    )
else:
    from experiment_registry import (
        load_registry as _load_registry_rows,
    )

DEFAULT_POOLS = ("csi300", "csi500", "csi1000")
PRIMARY_TEST_POOL = "csi1000"
FIXED_SEEDS = (42, 1000, 2000, 3000, 4000)
PHASE_S_POOLS = ("csi1000", "csi300", "csi500")
PHASE_S_BASELINE_ID = "topk-t10-d2-h1"


def _current_phase_s_baseline(rows: Sequence[dict]) -> dict | None:
    anchors = [
        row
        for row in rows
        if row.get("phase") == "S"
        and row.get("conclusion") == "baseline"
        and row.get("direction") == "baseline-strategy"
        and row.get("cleanup_retention_eligible") is True
    ]
    return anchors[-1] if anchors else None


def load_registry(path: Path) -> list[dict]:
    return _load_registry_rows(path)


def _metric(row: dict, pool: str, key: str) -> float | None:
    value = (row.get("metrics_summary") or {}).get(pool, {}).get(key)
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return number if number is not None and math.isfinite(number) else None


def _current_baseline(rows: Sequence[dict]) -> dict:
    anchors = [
        row
        for row in rows
        if row.get("direction") == "baseline"
        and row.get("phase") == "M"
        and str(row.get("conclusion") or "").lower() == "baseline"
    ]
    if not anchors:
        raise ValueError("registry does not contain a baseline anchor")
    return anchors[-1]


def _phase_m_candidate_score(
    row: dict,
    baseline: dict,
) -> tuple[float, float, float] | None:
    if row.get("phase") != "M":
        return None
    if row.get("cleanup_retention_eligible", True) is not True:
        return None
    if row.get("direction") == "baseline":
        return None
    if row.get("baseline_ref") != baseline.get("baseline_ref"):
        return None
    if tuple(sorted(row.get("seeds") or [])) != FIXED_SEEDS:
        return None

    rank_deltas = []
    rank_icir_deltas = []
    for pool in DEFAULT_POOLS:
        candidate_rank = _metric(row, pool, "rank_ic_mean")
        baseline_rank = _metric(baseline, pool, "rank_ic_mean")
        if (
            candidate_rank is None
            or baseline_rank is None
            or candidate_rank <= baseline_rank
        ):
            return None
        rank_deltas.append(candidate_rank - baseline_rank)

        candidate_ir = _metric(row, pool, "rank_icir")
        baseline_ir = _metric(baseline, pool, "rank_icir")
        if candidate_ir is not None and baseline_ir is not None:
            rank_icir_deltas.append(candidate_ir - baseline_ir)

    primary_rank_delta = _metric(row, PRIMARY_TEST_POOL, "rank_ic_mean") - _metric(
        baseline, PRIMARY_TEST_POOL, "rank_ic_mean"
    )
    primary_ir = _metric(row, PRIMARY_TEST_POOL, "rank_icir")
    baseline_primary_ir = _metric(baseline, PRIMARY_TEST_POOL, "rank_icir")
    primary_ir_delta = (
        primary_ir - baseline_primary_ir
        if primary_ir is not None and baseline_primary_ir is not None
        else float("-inf")
    )
    return primary_rank_delta, primary_ir_delta, mean(rank_deltas)


def select_retained_rows(rows: Sequence[dict]) -> list[dict]:
    """Return current baseline and at most one best complete Phase M candidate."""
    baseline = _current_baseline(rows)
    candidates = []
    for row in rows:
        score = _phase_m_candidate_score(row, baseline)
        if score is not None:
            candidates.append((score, str(row.get("exp_id") or ""), row))
    if not candidates:
        return [baseline]
    best = max(candidates, key=lambda item: (item[0], item[1]))
    return [baseline, best[2]]


def _direct_child(root: Path, candidate: Path) -> Path | None:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    return resolved if resolved.parent == resolved_root else None


def _safe_child_dirs(root: Path, errors: list[str]) -> list[Path]:
    if not root.is_dir():
        return []
    children = []
    for item in root.iterdir():
        if not item.is_dir():
            continue
        if item.is_symlink():
            errors.append(f"symlink directory is not allowed: {item}")
            continue
        child = _direct_child(root, item)
        if child is None:
            errors.append(f"unsafe child directory: {item}")
            continue
        children.append(child)
    return sorted(children)


def _validate_artifact_roots(
    repo_path: Path, errors: list[str]
) -> tuple[Path, Path, Path, bool]:
    unresolved_repo = repo_path.expanduser().absolute()
    unresolved_backtest = unresolved_repo / "backtest"
    unresolved_result = unresolved_backtest / "result"
    unresolved_mlruns = unresolved_repo / "mlruns"
    initial_error_count = len(errors)
    for label, path in (
        ("repository root", unresolved_repo),
        ("artifact root ancestor", unresolved_backtest),
        ("artifact root", unresolved_result),
        ("artifact root", unresolved_mlruns),
    ):
        if path.is_symlink():
            errors.append(f"{label} must not be a symlink: {path}")

    resolved_repo = unresolved_repo.resolve()
    if unresolved_result.resolve().parent != resolved_repo / "backtest":
        errors.append(
            f"artifact root resolves outside repository backtest dir: "
            f"{unresolved_result}"
        )
    if unresolved_mlruns.resolve().parent != resolved_repo:
        errors.append(
            f"artifact root resolves outside repository root: " f"{unresolved_mlruns}"
        )
    roots_safe = len(errors) == initial_error_count
    return (
        resolved_repo,
        resolved_repo / "backtest" / "result",
        resolved_repo / "mlruns",
        roots_safe,
    )


def _validate_baseline(baseline: dict, errors: list[str]) -> None:
    if tuple(sorted(baseline.get("seeds") or [])) != FIXED_SEEDS:
        errors.append(f"baseline seeds must be exactly {list(FIXED_SEEDS)}")
    for pool in DEFAULT_POOLS:
        for key in ("rank_ic_mean", "rank_icir"):
            if _metric(baseline, pool, key) is None:
                errors.append(f"baseline metric must be finite: {pool}.{key}")


def _retained_result_dirs(
    repo_root: Path,
    retained_rows: Sequence[dict],
    warnings: list[str],
    errors: list[str],
) -> list[Path]:
    result_root = (repo_root / "backtest" / "result").resolve()
    retained = set()
    for row in retained_rows:
        row_sessions = set()
        if tuple(sorted(row.get("seeds") or [])) != FIXED_SEEDS:
            errors.append(
                f"retained experiment seeds must be exactly {list(FIXED_SEEDS)}: {row.get('exp_id')}"
            )
        for raw in row.get("result_dirs") or []:
            raw_path = Path(str(raw))
            if not raw_path.is_absolute() and (
                len(raw_path.parts) < 3
                or raw_path.parts[:2]
                != (
                    "backtest",
                    "result",
                )
            ):
                continue
            unresolved = repo_root / raw_path
            if unresolved.is_symlink():
                errors.append(f"retained result path is a symlink: {raw}")
                continue
            candidate = _direct_child(result_root, unresolved)
            if candidate is None:
                errors.append(f"unsafe result path: {raw}")
                continue
            if not candidate.is_dir():
                errors.append(f"retained result session missing: {raw}")
                continue
            row_sessions.add(candidate)
        if len(row_sessions) != len(FIXED_SEEDS):
            errors.append(
                f"retained experiment {row.get('exp_id')} has "
                f"{len(row_sessions)} existing result sessions; "
                f"expected {len(FIXED_SEEDS)}"
            )
        retained.update(row_sessions)
    if errors:
        warnings.append(
            "Retention is confined to the selected repository; use --repo-root to explicitly select the real research root. No sibling repository is searched."
        )
    return sorted(retained)


def select_phase_s_retained_result_paths(rows: Sequence[dict]) -> set[str]:
    """Return the current formal session, accepting historical split schemas."""
    anchor = _current_phase_s_baseline(rows)
    if anchor is not None:
        exp_id = str(anchor.get("exp_id") or "")
        if anchor.get("state") != "baseline":
            raise ValueError(f"Phase S baseline row is malformed: {exp_id}")
        candidate_id = str((anchor.get("strategy") or {}).get("candidate_id") or "")
        if not candidate_id:
            raise ValueError(f"Phase S baseline strategy is missing: {exp_id}")
        if anchor.get("evaluation_mode") == "full_history_in_sample":
            paths = anchor.get("result_dirs") or []
            if (
                anchor.get("selection_pool") != "csi1000"
                or len(paths) != 1
                or not isinstance(paths[0], str)
                or not paths[0]
            ):
                raise ValueError(
                    f"Phase S baseline requires one CSI1000 full-period session: {exp_id}"
                )
            return set(paths)
        test_results = anchor.get("test_results") or {}
        if set(test_results) != set(PHASE_S_POOLS):
            raise ValueError(f"Phase S baseline test matrix is incomplete: {exp_id}")
        retained = set()
        for pool in PHASE_S_POOLS:
            candidate = test_results[pool]
            if candidate.get("candidate_id") != candidate_id:
                raise ValueError(
                    f"Phase S baseline candidate mismatch: {exp_id}/{pool}"
                )
            raw = candidate.get("result_dir")
            if not isinstance(raw, str) or not raw:
                raise ValueError(
                    f"Phase S baseline result_dir missing: {exp_id}/{pool}"
                )
            retained.add(raw)
        if len(retained) != len(PHASE_S_POOLS):
            raise ValueError(
                f"Phase S baseline must reference three distinct test sessions: {exp_id}"
            )
        interactive = anchor.get("interactive_full_period_result")
        if interactive is not None:
            if not isinstance(interactive, dict):
                raise ValueError(
                    f"Phase S baseline interactive result is malformed: {exp_id}"
                )
            raw = interactive.get("result_dir")
            if not isinstance(raw, str) or not raw:
                raise ValueError(
                    f"Phase S baseline interactive result_dir missing: {exp_id}"
                )
            if raw in retained:
                raise ValueError(
                    f"Phase S baseline interactive result must be a distinct session: {exp_id}"
                )
            retained.add(raw)
        return retained

    # Compatibility path for registries created before strategy baseline promotion.
    retained: set[str] = set()
    for row in rows:
        if row.get("phase") != "S":
            continue
        exp_id = str(row.get("exp_id") or "")
        if exp_id.startswith("baseline/"):
            continue
        diagnostic_markers = (
            exp_id.startswith("strategy-stability-full-period/"),
            row.get("direction") == "strategy-stability-full-period",
            row.get("conclusion") == "diagnostic_no_selection",
        )
        if any(diagnostic_markers):
            if not (
                all(diagnostic_markers)
                and row.get("state") == "complete"
                and row.get("cleanup_retention_eligible") is False
            ):
                raise ValueError(f"Phase S diagnostic row is malformed: {exp_id}")
            continue
        if row.get("state") != "test_complete":
            raise ValueError(
                f"Phase S row is incomplete: {exp_id} ({row.get('state')})"
            )
        if row.get("cleanup_retention_eligible") is not True:
            raise ValueError(f"Phase S row is not retention eligible: {exp_id}")
        winner = str(row.get("selected_candidate_id") or "")
        if not winner:
            raise ValueError(f"Phase S row has no frozen winner: {exp_id}")
        test_results = row.get("test_results") or {}
        if set(test_results) != set(PHASE_S_POOLS):
            raise ValueError(f"Phase S test pool matrix is incomplete: {exp_id}")
        expected_ids = {PHASE_S_BASELINE_ID, winner}
        for pool in PHASE_S_POOLS:
            candidates = test_results[pool]
            if {
                candidate.get("candidate_id") for candidate in candidates
            } != expected_ids:
                raise ValueError(
                    f"Phase S candidate matrix is incomplete: {exp_id}/{pool}"
                )
            for candidate in candidates:
                raw = candidate.get("result_dir")
                if not isinstance(raw, str) or not raw:
                    raise ValueError(
                        f"Phase S result_dir missing: {exp_id}/{pool}/"
                        f"{candidate.get('candidate_id')}"
                    )
                retained.add(raw)
    return retained


def _retained_phase_s_dirs(
    repo_root: Path,
    raw_paths: Sequence[str],
    errors: list[str],
) -> list[Path]:
    result_root = (repo_root / "backtest" / "result").resolve()
    retained = set()
    for raw in raw_paths:
        raw_path = Path(raw)
        if raw_path.is_absolute():
            unresolved = raw_path
        else:
            if len(raw_path.parts) < 3 or raw_path.parts[:2] != (
                "backtest",
                "result",
            ):
                errors.append(f"unsafe Phase S result path: {raw}")
                continue
            unresolved = repo_root / raw_path
        if unresolved.is_symlink():
            errors.append(f"retained Phase S result path is a symlink: {raw}")
            continue
        candidate = _direct_child(result_root, unresolved)
        if candidate is None:
            errors.append(f"unsafe Phase S result path: {raw}")
            continue
        if not candidate.is_dir():
            errors.append(f"retained Phase S result session missing: {raw}")
            continue
        retained.add(candidate)
    return sorted(retained)


def _repo_resolved(repo_root: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    return resolve_research_path(raw, repo_root)


def _validate_phase_s_baseline_sessions(
    repo_root: Path,
    anchor: dict,
    errors: list[str],
) -> None:
    """Bind each retained Phase S session to its pool-specific registry metadata."""
    if anchor.get("evaluation_mode") == "full_history_in_sample":
        _validate_full_period_baseline_session(repo_root, anchor, errors)
        return
    strategy = anchor.get("strategy") or {}
    test_segment = anchor.get("test_segment") or []
    fees = anchor.get("fees") or {}
    benchmarks = anchor.get("benchmarks") or {}
    for pool in PHASE_S_POOLS:
        expected = (anchor.get("test_results") or {}).get(pool) or {}
        session = _repo_resolved(repo_root, expected.get("result_dir"))
        if session is None:
            errors.append(f"Phase S baseline result_dir missing: {pool}")
            continue
        meta_path = session / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Phase S baseline metadata unreadable: {pool}: {exc}")
            continue

        backtest = meta.get("backtest") or {}
        actual_strategy = meta.get("strategy") or {}
        expected_strategy = {
            "class": strategy.get("strategy_class"),
            "risk_degree": anchor.get("risk_degree"),
            "topk": strategy.get("topk"),
            "n_drop": strategy.get("n_drop"),
            "hold_thresh": strategy.get("hold_thresh"),
        }
        mismatches = []
        if _repo_resolved(repo_root, meta.get("config_path")) != _repo_resolved(
            repo_root, expected.get("config")
        ):
            mismatches.append("config_path")
        if _repo_resolved(repo_root, meta.get("source_pred")) != _repo_resolved(
            repo_root, expected.get("source_pred")
        ):
            mismatches.append("source_pred")
        if meta.get("source_pred_sha256") != expected.get("source_pred_sha256"):
            mismatches.append("source_pred_sha256")
        if len(test_segment) != 2 or [
            backtest.get("start_time"),
            backtest.get("end_time"),
        ] != list(test_segment):
            mismatches.append("test_segment")
        if backtest.get("benchmark") != benchmarks.get(pool):
            mismatches.append("benchmark")
        if backtest.get("account") != anchor.get("account"):
            mismatches.append("account")
        exchange = backtest.get("exchange_kwargs") or {}
        if any(exchange.get(key) != value for key, value in fees.items()):
            mismatches.append("fees")
        if any(
            actual_strategy.get(key) != value
            for key, value in expected_strategy.items()
        ):
            mismatches.append("strategy")
        if mismatches:
            errors.append(
                f"Phase S baseline metadata mismatch for {pool}: "
                + ", ".join(mismatches)
            )


def _validate_full_period_baseline_session(
    repo_root: Path, anchor: dict, errors: list[str]
) -> None:
    """Check the config, frozen model, and canonical prediction before cleanup."""
    try:
        import yaml
        from phase_s_prediction_validation import (
            FULL_PREDICTION_COVERAGE,
            validate_full_prediction_manifest,
        )
        from phase_s_protocol import sha256_file

        paths = select_phase_s_retained_result_paths([anchor])
        session = _repo_resolved(repo_root, next(iter(paths)))
        if (
            session is None
            or _direct_child(repo_root / "backtest/result", session) is None
        ):
            raise ValueError("full-period session is outside selected --repo-root")
        configs = anchor.get("configs") or []
        if len(configs) != 1:
            raise ValueError("full-period baseline requires one config")
        config_path = _repo_resolved(repo_root, configs[0])
        if config_path is None or not config_path.is_relative_to(repo_root):
            raise ValueError("full-period config is outside selected --repo-root")
        if sha256_file(config_path) != anchor.get("config_sha256"):
            raise ValueError("full-period config SHA mismatch")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        manifest_path = resolve_research_path(
            "backtest/experiments/strategy-stability/20260801_full_period/prediction_manifest.json",
            repo_root,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry, _ = validate_full_prediction_manifest(
            manifest,
            manifest_path,
            repo_root=repo_root,
            authoritative_manifest_path=manifest_path,
            expected_coverage=FULL_PREDICTION_COVERAGE,
        )
        expected_pred = _repo_resolved(repo_root, entry.get("path"))
        if expected_pred is None or not expected_pred.is_relative_to(repo_root):
            raise ValueError("full-period prediction is outside selected --repo-root")
        strategy = config.get("strategy") or {}
        expected_strategy = {
            "class": strategy.get("class"),
            **(strategy.get("kwargs") or {}),
        }
        expected_strategy.update(
            {key: strategy.get(key) for key in ("topk", "n_drop", "hold_thresh")}
        )
        backtest = meta.get("backtest") or {}
        expected_backtest = {
            **(config.get("backtest") or {}),
            "benchmark": (config.get("data") or {}).get("benchmark"),
        }
        period = anchor.get("selection_segment")
        if (
            anchor.get("model_ref") != "b6-m"
            or anchor.get("frozen_model_ref") != "B6 v1.0"
            or (config.get("data") or {}).get("instruments") != "csi1000"
            or period != anchor.get("test_segment")
            or period != (config.get("segments") or {}).get("test")
            or [backtest.get("start_time"), backtest.get("end_time")] != period
            or backtest.get("account") != anchor.get("account")
            or any(
                backtest.get(key) != value for key, value in expected_backtest.items()
            )
            or any(
                (meta.get("strategy") or {}).get(key) != value
                for key, value in expected_strategy.items()
            )
            or any(
                (meta.get("strategy") or {}).get(key)
                != (anchor.get("strategy") or {}).get(key)
                for key in ("topk", "n_drop", "hold_thresh", "risk_degree")
            )
            or _repo_resolved(repo_root, meta.get("config_path")) != config_path
            or _repo_resolved(repo_root, meta.get("source_pred")) != expected_pred
            or meta.get("source_pred_sha256") != entry.get("prediction_sha256")
        ):
            raise ValueError("full-period baseline metadata mismatch")
    except (OSError, ValueError, TypeError, KeyError, StopIteration) as exc:
        errors.append(f"Phase S full-period baseline validation failed: {exc}")


def _phase_m_v1_retained_rows(
    rows: Sequence[dict], errors: list[str]
) -> tuple[list[dict], list[dict]]:
    """Keep versioned model baselines independently of CSI1000 metric ranking."""
    models = {
        row.get("exp_id"): row
        for row in rows
        if row.get("phase") == "M"
        and row.get("phase_m_protocol") == "v1"
        and row.get("baseline_version")
    }
    for row in models.values():
        if row.get("state") != "completed":
            errors.append(f"Phase M v1 baseline is incomplete: {row.get('exp_id')}")
    current_bt = [
        row
        for row in rows
        if row.get("report_kind") == "phase_m_v1_bt_baseline"
        and row.get("current_bt") is True
    ]
    if len(current_bt) > 1:
        errors.append("multiple current Phase M v1 BT baselines")
    for row in current_bt:
        if row.get("model_ref") not in models:
            errors.append(
                f"current BT model_ref has no versioned Phase M v1 baseline: {row.get('model_ref')}"
            )
        if tuple(sorted(row.get("seeds") or [])) != FIXED_SEEDS:
            errors.append("current BT must reference the complete fixed five seeds")
    return list(models.values()), current_bt


def _validate_local_model_sessions(
    repo_root: Path, rows: Sequence[dict], sessions: Sequence[Path], errors: list[str]
) -> None:
    for row in rows:
        seeds = []
        expected_sessions = {
            _repo_resolved(repo_root, raw) for raw in row.get("result_dirs") or []
        }
        for session in sessions:
            if session not in expected_sessions:
                continue
            try:
                paths = [
                    session / "meta.json",
                    session / "run_01/mlruns_link.json",
                    session / "run_01/train_summary.json",
                    session / "run_01/artifacts_root/artifacts/trained_model",
                ]
                for path in paths:
                    if (
                        not path.is_file()
                        or path.stat().st_size == 0
                        or any(
                            parent.is_symlink()
                            for parent in (path, *path.parents)
                            if parent != repo_root
                        )
                    ):
                        raise ValueError(
                            f"local training artifact missing or symlink: {path}"
                        )
                meta, link, summary = [
                    json.loads(path.read_text(encoding="utf-8")) for path in paths[:3]
                ]
                seed = (meta.get("regime_adapt") or {}).get("seed")
                if (
                    meta.get("mode") != "train_only"
                    or meta.get("n_runs") != 1
                    or seed != summary.get("seed")
                    or _repo_resolved(repo_root, link.get("train_artifacts"))
                    != session / "run_01/artifacts_root"
                ):
                    raise ValueError(f"local training metadata mismatch: {session}")
                seeds.append(seed)
            except (OSError, ValueError, TypeError) as exc:
                errors.append(f"Phase M v1 baseline validation failed: {exc}")
        if sorted(seeds) != list(FIXED_SEEDS):
            errors.append(
                f"Phase M v1 baseline must contain one local model per fixed seed: {row.get('exp_id')}"
            )


def _train_experiment_ids(
    session_dirs: Sequence[Path],
    warnings: list[str],
    errors: list[str],
    *,
    mlruns_root: Path,
) -> set[str]:
    experiment_ids = set()
    for session in session_dirs:
        meta_path = session / "meta.json"
        if not meta_path.is_file():
            errors.append(f"session meta missing: {meta_path}")
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"session meta unreadable: {meta_path}: {exc}")
            continue
        runs = meta.get("runs") or []
        successful_runs = []
        session_ids = set()
        for run in runs:
            if run.get("status") != "success":
                continue
            successful_runs.append(run)
            experiment_id = str(run.get("train_experiment_id") or "")
            if experiment_id.isdigit():
                session_ids.add(experiment_id)
            try:
                run_index = int(run.get("run") or run.get("fold") or 0)
                run_dir = session / f"run_{run_index:02d}"
                link_path = run_dir / "mlruns_link.json"
                if run_index < 1 or run_dir.is_symlink() or link_path.is_symlink():
                    raise ValueError(f"unsafe train run metadata: {link_path}")
                link = json.loads(link_path.read_text(encoding="utf-8"))
                _validate_recorder(
                    mlruns_root, experiment_id, run, link, "train", require_model=True
                )
            except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
                errors.append(f"train recorder validation failed: {session}: {exc}")
        if meta.get("mode") == "rolling_train_only":
            expected = int(meta.get("expected_fold_count") or 0)
            folds = meta.get("rolling_folds") or []
            successful_by_fold = {
                int(run.get("fold") or run.get("run") or 0): run
                for run in successful_runs
            }
            valid = (
                expected > 0
                and len(folds) == expected
                and len(successful_runs) == expected
                and len(successful_by_fold) == expected
                and set(successful_by_fold) == set(range(1, expected + 1))
                and len(session_ids) == expected
            )
            if valid:
                valid = all(
                    successful_by_fold[int(fold.get("fold") or 0)].get("segments")
                    == fold.get("segments")
                    for fold in folds
                )
            if not valid:
                errors.append(
                    "rolling session must reference one successful unique train "
                    f"experiment per declared fold: {meta_path}"
                )
        elif len(session_ids) != 1:
            errors.append(
                f"session must reference exactly one successful train experiment: "
                f"{meta_path}"
            )
        experiment_ids.update(session_ids)
    return experiment_ids


def _validate_recorder(
    mlruns_root: Path,
    experiment_id: str,
    run: dict,
    link: dict,
    kind: str,
    *,
    require_model: bool = False,
) -> None:
    """Bind session metadata to one real successful recorder under its experiment."""
    recorder_id = link.get(f"{kind}_recorder_id")
    if (
        not experiment_id.isdigit()
        or str(link.get(f"{kind}_experiment_id") or "") != experiment_id
        or not isinstance(recorder_id, str)
        or not recorder_id
        or recorder_id in (".", "..")
        or Path(recorder_id).name != recorder_id
        or run.get(f"{kind}_recorder_id") != recorder_id
        or (
            f"{kind}_experiment_id" in run
            and str(run[f"{kind}_experiment_id"]) != experiment_id
        )
    ):
        raise ValueError(
            f"{kind} recorder/experiment ID differs between session and link or is unsafe"
        )
    experiment = mlruns_root / experiment_id
    recorder = experiment / recorder_id
    recorder_meta = recorder / "meta.yaml"
    if (
        _direct_child(experiment, recorder) is None
        or not recorder.is_dir()
        or not recorder_meta.is_file()
        or any(path.is_symlink() for path in (experiment, recorder, recorder_meta))
    ):
        raise ValueError(f"{kind} recorder missing, unsafe or symlink: {recorder}")
    info = yaml.safe_load(recorder_meta.read_text(encoding="utf-8"))
    if (
        not isinstance(info, dict)
        or str(info.get("experiment_id")) != experiment_id
        or info.get("run_id") != recorder_id
        or info.get("status") != 3
        or info.get("lifecycle_stage") != "active"
    ):
        raise ValueError(
            f"{kind} recorder metadata mismatch or not successful: {recorder}"
        )
    raw_artifacts = link.get(f"{kind}_artifacts")
    if (
        raw_artifacts is not None
        and _repo_resolved(mlruns_root.parent, raw_artifacts) != recorder
    ):
        raise ValueError(f"{kind} recorder artifact link mismatch: {recorder}")
    if require_model:
        model = recorder / "artifacts/trained_model"
        if (
            raw_artifacts is None
            or model.parent.is_symlink()
            or model.is_symlink()
            or not model.is_file()
            or model.stat().st_size == 0
            or model.resolve().parent != recorder / "artifacts"
        ):
            raise ValueError(
                f"train recorder model missing, empty, unsafe or symlink: {model}"
            )


def _backtest_experiment_ids(
    session_dirs: Sequence[Path], errors: list[str], *, mlruns_root: Path
) -> set[str]:
    experiment_ids = set()
    for session in session_dirs:
        meta_path = session / "meta.json"
        link_path = session / "run_01" / "mlruns_link.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            link = json.loads(link_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Phase S session metadata unreadable: {session}: {exc}")
            continue
        successful = [
            run for run in (meta.get("runs") or []) if run.get("status") == "success"
        ]
        experiment_id = str(link.get("backtest_experiment_id") or "")
        if (
            meta.get("mode") != "pred_backtest"
            or len(successful) != 1
            or not experiment_id.isdigit()
        ):
            errors.append(
                f"Phase S session must contain one successful pred_backtest: {session}"
            )
            continue
        try:
            _validate_recorder(
                mlruns_root, experiment_id, successful[0], link, "backtest"
            )
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            errors.append(f"Phase S recorder validation failed: {session}: {exc}")
            continue
        experiment_ids.add(experiment_id)
    return experiment_ids


def build_cleanup_plan(repo_root: Path, rows: Sequence[dict]) -> dict[str, Any]:
    if not rows:
        raise ValueError("registry does not contain any research records")
    warnings: list[str] = []
    errors: list[str] = []
    repo_root, result_root, mlruns_root, roots_safe = _validate_artifact_roots(
        Path(repo_root), errors
    )

    csi_rows = [
        row for row in rows if classify_experiment(row)["track"] == "csi1000"
    ]
    scope = (
        "csi1000_and_all_a" if csi_rows and len(csi_rows) < len(rows)
        else "csi1000" if csi_rows else "all_a"
    )
    retained_rows = select_retained_rows(csi_rows) if csi_rows else []
    if retained_rows:
        _validate_baseline(retained_rows[0], errors)
    phase_m_result_dirs = (
        _retained_result_dirs(repo_root, retained_rows, warnings, errors)
        if roots_safe
        else []
    )
    phase_s_raw_paths: set[str] = set()
    try:
        phase_s_raw_paths = select_phase_s_retained_result_paths(csi_rows)
    except ValueError as exc:
        errors.append(str(exc))
    phase_s_result_dirs = (
        _retained_phase_s_dirs(repo_root, sorted(phase_s_raw_paths), errors)
        if roots_safe
        else []
    )
    phase_s_anchor = _current_phase_s_baseline(csi_rows)
    if roots_safe and phase_s_anchor is not None:
        _validate_phase_s_baseline_sessions(repo_root, phase_s_anchor, errors)
    try:
        historical_rows = load_historical_baselines(repo_root)
    except (OSError, ValueError) as exc:
        historical_rows = []
        errors.append(f"historical model baseline reference validation failed: {exc}")
    local_rows, current_bt = _phase_m_v1_retained_rows([*historical_rows, *rows], errors)
    if not csi_rows and not local_rows:
        errors.append("all-A registry has no versioned Phase M v1 baseline")
    local_dirs = (
        _retained_result_dirs(repo_root, local_rows, warnings, errors)
        if roots_safe
        else []
    )
    if roots_safe:
        _validate_local_model_sessions(repo_root, local_rows, local_dirs, errors)
    bt_raw_paths = []
    for row in current_bt:
        raw = row.get("ensemble_session")
        if not isinstance(raw, str) or not raw:
            errors.append(f"current BT ensemble_session missing: {row.get('exp_id')}")
        else:
            bt_raw_paths.append(raw)
    bt_dirs = (
        _retained_phase_s_dirs(repo_root, bt_raw_paths, errors) if roots_safe else []
    )
    keep_result_dirs = sorted(
        set(phase_m_result_dirs + phase_s_result_dirs + local_dirs + bt_dirs)
    )
    all_result_dirs = _safe_child_dirs(result_root, errors) if roots_safe else []
    for path in all_result_dirs:
        if path not in keep_result_dirs and not (path / "meta.json").is_file():
            keep_result_dirs.append(path)
            warnings.append(f"non-session result directory preserved for audit: {path}")
    keep_result_dirs.sort()
    delete_result_dirs = [
        path for path in all_result_dirs if path not in set(keep_result_dirs)
    ]

    keep_experiment_ids = _train_experiment_ids(
        phase_m_result_dirs, warnings, errors, mlruns_root=mlruns_root
    )
    keep_experiment_ids.update(
        _backtest_experiment_ids(
            phase_s_result_dirs + bt_dirs, errors, mlruns_root=mlruns_root
        )
    )
    keep_mlruns_dirs = []
    delete_mlruns_dirs = []
    if roots_safe and mlruns_root.is_dir():
        for path in _safe_child_dirs(mlruns_root, errors):
            if path.name in keep_experiment_ids:
                keep_mlruns_dirs.append(path)
            elif path.name == ".trash" or path.name.isdigit():
                delete_mlruns_dirs.append(path)
            else:
                warnings.append(f"unknown mlruns directory preserved: {path}")
    if roots_safe:
        for experiment_id in sorted(keep_experiment_ids):
            if not (mlruns_root / experiment_id).is_dir():
                errors.append(
                    f"retained MLflow experiment missing: {mlruns_root / experiment_id}"
                )

    if errors:
        delete_result_dirs = []
        delete_mlruns_dirs = []

    return {
        "repo_root": repo_root,
        "result_root": result_root,
        "mlruns_root": mlruns_root,
        "scope": scope,
        "baseline_exp_id": retained_rows[0].get("exp_id") if retained_rows else None,
        "candidate_exp_id": (
            retained_rows[1].get("exp_id") if len(retained_rows) > 1 else None
        ),
        "phase_m_v1_baseline_exp_ids": [row.get("exp_id") for row in local_rows],
        "current_bt_exp_ids": [row.get("exp_id") for row in current_bt],
        "keep_result_dirs": keep_result_dirs,
        "delete_result_dirs": delete_result_dirs,
        "keep_mlruns_dirs": keep_mlruns_dirs,
        "delete_mlruns_dirs": delete_mlruns_dirs,
        "warnings": warnings,
        "errors": errors,
    }


def apply_cleanup(plan: dict[str, Any], *, apply: bool) -> None:
    if not apply:
        return
    if plan.get("errors"):
        raise ValueError("cleanup plan contains retention errors; refusing deletion")
    targets = []
    for key, root_key in (
        ("delete_result_dirs", "result_root"),
        ("delete_mlruns_dirs", "mlruns_root"),
    ):
        root = Path(plan[root_key]).resolve()
        for raw in plan[key]:
            raw_path = Path(raw)
            if raw_path.is_symlink():
                raise ValueError(f"refusing to delete symlink: {raw}")
            target = _direct_child(root, raw_path)
            if target is None:
                raise ValueError(f"refusing to delete non-child path: {raw}")
            targets.append(target)
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)


def _jsonable(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            [str(item) for item in value]
            if isinstance(value, list) and value and isinstance(value[0], Path)
            else str(value) if isinstance(value, Path) else value
        )
        for key, value in plan.items()
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    repo_default = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="清理实验产物；默认 dry-run，--apply 才删除"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=repo_default,
        help="Explicit artifact/research root; no sibling fallback or cross-root deletion",
    )
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    repo_root = args.repo_root.expanduser().absolute()
    registry = (
        args.registry.resolve()
        if args.registry
        else repo_root / "backtest" / "experiments" / "registry.jsonl"
    )
    plan = build_cleanup_plan(repo_root, load_registry(registry))
    print(json.dumps(_jsonable(plan), ensure_ascii=False, indent=2))
    apply_cleanup(plan, apply=args.apply)
    print("cleanup applied" if args.apply else "dry-run only", file=sys.stderr)


if __name__ == "__main__":
    main()
