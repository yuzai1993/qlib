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

DEFAULT_POOLS = ("csi300", "csi500", "csi1000")
FIXED_SEEDS = (42, 1000, 2000, 3000, 4000)


def load_registry(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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
) -> tuple[float, float] | None:
    if row.get("phase") != "M":
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

    ir_score = mean(rank_icir_deltas) if len(rank_icir_deltas) == 3 else float("-inf")
    return mean(rank_deltas), ir_score


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
    best = max(candidates, key=lambda item: (item[0][0], item[0][1], item[1]))
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
        for raw in row.get("result_dirs") or []:
            raw_path = Path(str(raw))
            if len(raw_path.parts) < 3 or raw_path.parts[:2] != (
                "backtest",
                "result",
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
    return sorted(retained)


def _train_experiment_ids(
    session_dirs: Sequence[Path],
    warnings: list[str],
    errors: list[str],
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
        session_ids = set()
        for run in meta.get("runs") or []:
            if run.get("status") != "success":
                continue
            experiment_id = str(run.get("train_experiment_id") or "")
            if experiment_id.isdigit():
                session_ids.add(experiment_id)
        if len(session_ids) != 1:
            errors.append(
                f"session must reference exactly one successful train experiment: "
                f"{meta_path}"
            )
        experiment_ids.update(session_ids)
    return experiment_ids


def build_cleanup_plan(repo_root: Path, rows: Sequence[dict]) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    repo_root, result_root, mlruns_root, roots_safe = _validate_artifact_roots(
        Path(repo_root), errors
    )

    retained_rows = select_retained_rows(rows)
    _validate_baseline(retained_rows[0], errors)
    keep_result_dirs = (
        _retained_result_dirs(repo_root, retained_rows, warnings, errors)
        if roots_safe
        else []
    )
    all_result_dirs = _safe_child_dirs(result_root, errors) if roots_safe else []
    delete_result_dirs = [
        path for path in all_result_dirs if path not in set(keep_result_dirs)
    ]

    keep_experiment_ids = _train_experiment_ids(keep_result_dirs, warnings, errors)
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

    if errors:
        delete_result_dirs = []
        delete_mlruns_dirs = []

    return {
        "repo_root": repo_root,
        "result_root": result_root,
        "mlruns_root": mlruns_root,
        "baseline_exp_id": retained_rows[0].get("exp_id"),
        "candidate_exp_id": (
            retained_rows[1].get("exp_id") if len(retained_rows) > 1 else None
        ),
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
    parser.add_argument("--repo-root", type=Path, default=repo_default)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
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
