"""Promote the frozen B5 RankIC-search winner to the B6 model baseline.

The promotion is deliberately fail closed: every source, metric summary, hash,
model artifact, and report payload is validated or constructed before any
destination is replaced.  The historical selection manifest remains intact;
the new freeze manifest contains only the five selected winner artifacts and
can therefore be verified after loser-session cleanup.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import pickle
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

QLIB_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QLIB_ROOT))

from backtest.scripts import build_experiment_report as report_builder  # noqa: E402
from backtest.scripts.freeze_b5_rankic_selection import (  # noqa: E402
    CANDIDATES,
    EVAL_LABEL_EXPR,
    MIN_COUNT,
    SAFE_VALID_SEGMENT,
    SEEDS,
    TEST_POOLS,
    TEST_SEGMENT,
    TRAIN_SEGMENT,
    VALID_SEGMENT,
    validate_candidate_config,
)


SOURCE_EXP_ID = "model-hyperparam/valid-rankic-search-v1"
B5_EXP_ID = "baseline/b5-m"
B5_REF = "B5 v1.0"
B6_EXP_ID = "baseline/b6-m"
B6_REF = "B6 v1.0"
WINNER = "rankic-es-lr010"
SELF_LABEL_EXPR = "Ref($close, -41)/Ref($close, -1)-1"
FORMAL_DATA_VERSION = "2026-07-30"
SELF_DATA_VERSION = "2026-07-31"
EXPECTED_SELF_SHA256 = "68598f64dcd8be9c344a26a33842660ba5f449ef52902f713aa9bfddf73e6d4a"
EXPECTED_BEST_ITERATIONS = {
    42: [96, 88, 65],
    1000: [99, 72, 68],
    2000: [73, 94, 116],
    3000: [68, 96, 76],
    4000: [111, 64, 74],
}
METRIC_KEYS = ("ic_mean", "icir", "rank_ic_mean", "rank_icir")

DEFAULT_REGISTRY = BACKTEST_ROOT / "experiments/registry.jsonl"
DEFAULT_SELECTION_MANIFEST = BACKTEST_ROOT / "experiments/b5_rankic_hyperparam_selection.json"
DEFAULT_FORMAL_RESULT = BACKTEST_ROOT / "experiments/ic/mh_valid_rankic_selected_test_1d.json"
DEFAULT_SELF_RESULT = BACKTEST_ROOT / "experiments/ic/mh_valid_rankic_selected_test_self.json"
DEFAULT_FREEZE = BACKTEST_ROOT / "experiments/b6_model_freeze.json"
DEFAULT_REPORT = BACKTEST_ROOT / "experiments/report.html"


def _fail(message: str) -> None:
    raise ValueError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"{label} cannot be loaded: {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        _fail(f"{label} must be finite")
    if not math.isfinite(number):
        _fail(f"{label} must be finite")
    return number


def _same_number(actual: Any, expected: float, label: str) -> None:
    number = _finite(actual, label)
    if not math.isclose(number, expected, rel_tol=1e-12, abs_tol=1e-15):
        _fail(f"{label} differs from independently recomputed value")


def _sample_std(values: Sequence[float]) -> float:
    if len(values) != len(SEEDS):
        _fail("sample standard deviation requires exact five seeds")
    mean = math.fsum(values) / len(values)
    return math.sqrt(
        math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)
    )


def _exact_sequence(value: Any, expected: Sequence[Any], label: str) -> None:
    if not isinstance(value, (list, tuple)) or list(value) != list(expected):
        _fail(f"{label} drift: expected {list(expected)!r}, got {value!r}")


def _inside_repo(repo_root: Path, path: Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        _fail(f"{label} must be inside repository: {resolved}")
    return resolved


def _resolve_repo_path(repo_root: Path, raw: Any, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        _fail(f"{label} path missing")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return _inside_repo(repo_root, path, label)


def _resolve_session_path(repo_root: Path, raw: Any, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        _fail(f"{label} session path missing")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        if len(path.parts) >= 2 and path.parts[:2] == ("backtest", "result"):
            path = repo_root / path
        else:
            path = repo_root / "backtest/result" / path
    path = _inside_repo(repo_root, path, label)
    if path.parent != (repo_root / "backtest/result").resolve():
        _fail(f"{label} session must be a direct result child: {path}")
    return path


def _relative(repo_root: Path, path: Path, label: str) -> str:
    return _inside_repo(repo_root, path, label).relative_to(repo_root).as_posix()


def _registry_rows(raw: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, raw_line in enumerate(raw.splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail(f"registry line {number} is not valid UTF-8 JSON: {exc}")
        if not isinstance(value, dict):
            _fail(f"registry line {number} must be a JSON object")
        rows.append(value)
    return rows


def _single_row(rows: Sequence[dict[str, Any]], exp_id: str) -> dict[str, Any]:
    matches = [row for row in rows if row.get("exp_id") == exp_id]
    if len(matches) != 1:
        _fail(f"registry must contain exactly one {exp_id} row")
    return matches[0]


def _validate_source_row(source: Mapping[str, Any]) -> None:
    exact = {
        "direction": "model-hyperparam",
        "phase": "M",
        "baseline_ref": B5_REF,
        "train_pool": "csi1000",
        "label_kind": "cumulative_return",
        "label_horizon": 40,
        "label": "Ref($close,-41)/Ref($close,-1)-1",
        "purge_trading_days": 41,
        "feature_groups": ["range"],
        "model": "RankICEarlyStoppingDEnsembleModel",
        "training_objective": "H40 CSRankNorm MSE",
        "learn_processors": ["DropnaLabel", "CSRankNorm(label)"],
        "early_stopping_metric": "fixed_next_day_valid_daily_rank_ic",
        "early_stopping_rounds": 20,
        "selection_segment": "valid",
        "selection_official_segment": list(VALID_SEGMENT),
        "selection_effective_segment": list(SAFE_VALID_SEGMENT),
        "selection_label": EVAL_LABEL_EXPR,
        "selection_label_role": "fixed_1d",
        "selection_min_count": MIN_COUNT,
        "selection_metric": "csi1000.valid.rank_ic_mean",
        "selection_tie_breaker": ["rank_icir", "candidate_id"],
        "selected_candidate": WINNER,
        "primary_test_pool": "csi1000",
        "test_pools": list(TEST_POOLS),
        "test_segment": list(TEST_SEGMENT),
        "test_label": EVAL_LABEL_EXPR,
        "test_label_role": "fixed_1d",
        "test_min_count": MIN_COUNT,
        "test_policy": "freeze_valid_winner_then_test_once",
        "data_version": FORMAL_DATA_VERSION,
        "conclusion": "improve",
    }
    for key, expected in exact.items():
        if source.get(key) != expected:
            _fail(f"source experiment {key} protocol drift")
    _exact_sequence(source.get("seeds"), SEEDS, "source experiment seeds")
    _exact_sequence(source.get("selection_candidates"), CANDIDATES, "source candidates")
    if source.get("config_count") != len(CANDIDATES) * len(SEEDS):
        _fail("source experiment config_count must be 20")


def _normalize_sessions(repo_root: Path, value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(SEEDS):
        _fail(f"{label} must contain exact five sessions")
    by_seed: dict[int, Path] = {}
    seen: set[Path] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"session", "seed"}:
            _fail(f"{label} session row invalid")
        try:
            seed = int(item["seed"])
        except (TypeError, ValueError):
            _fail(f"{label} session seed invalid")
        path = _resolve_session_path(repo_root, item["session"], label)
        if seed not in SEEDS or seed in by_seed or path in seen:
            _fail(f"{label} session seed/path matrix invalid")
        by_seed[seed] = path
        seen.add(path)
    if set(by_seed) != set(SEEDS):
        _fail(f"{label} session seeds differ from fixed five")
    return [
        {"session": _relative(repo_root, by_seed[seed], label), "seed": seed}
        for seed in SEEDS
    ]


def _validate_metric_pool(pool: Any, label: str) -> dict[str, float]:
    if not isinstance(pool, dict):
        _fail(f"{label} pool payload missing")
    seed_rows = pool.get("seeds")
    expected_keys = {str(seed) for seed in SEEDS}
    if not isinstance(seed_rows, dict) or set(seed_rows) != expected_keys:
        _fail(f"{label} must contain exact five seeds")
    values = {key: [] for key in METRIC_KEYS}
    for seed in SEEDS:
        row = seed_rows[str(seed)]
        if not isinstance(row, dict):
            _fail(f"{label}/{seed} metrics missing")
        try:
            n_days = int(row.get("n_days"))
        except (TypeError, ValueError):
            _fail(f"{label}/{seed} n_days invalid")
        if n_days <= 0:
            _fail(f"{label}/{seed} n_days must be positive")
        for key in METRIC_KEYS:
            values[key].append(_finite(row.get(key), f"{label}/{seed} {key}"))
    seed_mean = pool.get("seed_mean")
    if not isinstance(seed_mean, dict):
        _fail(f"{label} seed_mean missing")
    normalized: dict[str, float] = {}
    for key, per_seed in values.items():
        expected = math.fsum(per_seed) / len(per_seed)
        _same_number(seed_mean.get(key), expected, f"{label} seed_mean {key}")
        normalized[key] = expected
    expected_std = _sample_std(values["rank_ic_mean"])
    _same_number(
        seed_mean.get("rank_ic_mean_std"),
        expected_std,
        f"{label} rank_ic_mean_std",
    )
    normalized["rank_ic_mean_std"] = expected_std
    return normalized


def _compare_summary(actual: Any, expected: Mapping[str, Mapping[str, float]], label: str) -> None:
    if not isinstance(actual, dict) or set(actual) != set(TEST_POOLS):
        _fail(f"{label} must contain exact three pools")
    for pool in TEST_POOLS:
        row = actual[pool]
        if not isinstance(row, dict):
            _fail(f"{label}/{pool} summary missing")
        for key, value in expected[pool].items():
            _same_number(row.get(key), value, f"{label}/{pool} {key}")


def _validate_evaluation(
    result: Mapping[str, Any],
    *,
    repo_root: Path,
    selected_sessions: Sequence[dict[str, Any]],
    selected_config: Path,
    role: str,
) -> dict[str, dict[str, float]]:
    if result.get("eval_segment_name") != "test":
        _fail(f"{role} evaluation must use test segment")
    for key in ("eval_segment", "effective_eval_segment", "test_segment"):
        _exact_sequence(result.get(key), TEST_SEGMENT, f"{role} {key}")
    if role == "eval_1d":
        if (
            result.get("eval_label") != EVAL_LABEL_EXPR
            or result.get("eval_label_role") != "fixed_1d"
            or result.get("min_count") != MIN_COUNT
            or result.get("selected_candidate") != WINNER
            or result.get("data_version") != FORMAL_DATA_VERSION
        ):
            _fail("formal fixed-one-day evaluation protocol drift")
    elif role == "eval_self":
        if (
            result.get("eval_label") != SELF_LABEL_EXPR
            or result.get("eval_label_role") != "self"
            or result.get("data_version") != SELF_DATA_VERSION
        ):
            _fail("diagnostic self evaluation protocol drift")
    else:
        _fail(f"unknown evaluation role: {role}")
    actual_config = _resolve_repo_path(repo_root, result.get("config"), f"{role} config")
    if actual_config != selected_config:
        _fail(f"{role} evaluation config differs from selected seed-42 config")
    sessions = _normalize_sessions(repo_root, result.get("sessions"), f"{role} evaluation")
    if sessions != list(selected_sessions):
        _fail(f"{role} evaluation sessions differ from frozen winner")
    pools = result.get("pools")
    if not isinstance(pools, dict) or list(pools) != list(TEST_POOLS):
        _fail(f"{role} evaluation must contain exact ordered three pools")
    return {
        pool: _validate_metric_pool(pools[pool], f"{role}/{pool}")
        for pool in TEST_POOLS
    }


def _selection_config_matrix(
    manifest: Mapping[str, Any],
    source: Mapping[str, Any],
    repo_root: Path,
) -> dict[str, dict[int, dict[str, Any]]]:
    rows = manifest.get("config_hashes")
    if not isinstance(rows, list) or len(rows) != len(CANDIDATES) * len(SEEDS):
        _fail("selection manifest must contain exactly 20 config hashes")
    matrix: dict[str, dict[int, dict[str, Any]]] = {candidate: {} for candidate in CANDIDATES}
    for row in rows:
        if not isinstance(row, dict):
            _fail("selection config hash row invalid")
        candidate = row.get("candidate")
        try:
            seed = int(row.get("seed"))
        except (TypeError, ValueError):
            _fail("selection config seed invalid")
        if candidate not in matrix or seed not in SEEDS or seed in matrix[candidate]:
            _fail("selection config candidate/seed matrix invalid")
        path = _resolve_repo_path(repo_root, row.get("path"), "selection config")
        if not path.is_file() or sha256_file(path) != row.get("sha256"):
            _fail(f"selection config hash mismatch: {path}")
        validate_candidate_config(path, str(candidate), seed)
        matrix[str(candidate)][seed] = {
            "candidate": candidate,
            "seed": seed,
            "path": path,
            "sha256": row["sha256"],
        }
    if any(set(by_seed) != set(SEEDS) for by_seed in matrix.values()):
        _fail("selection config matrix incomplete")

    source_rows = source.get("config_hashes")
    if not isinstance(source_rows, list) or len(source_rows) != len(rows):
        _fail("source experiment config hash matrix missing")
    normalized_manifest = {
        (candidate, seed, _relative(repo_root, row["path"], "config"), row["sha256"])
        for candidate, by_seed in matrix.items()
        for seed, row in by_seed.items()
    }
    normalized_source = set()
    for row in source_rows:
        if not isinstance(row, dict):
            _fail("source config hash row invalid")
        try:
            seed = int(row.get("seed"))
        except (TypeError, ValueError):
            _fail("source config seed invalid")
        path = _resolve_repo_path(repo_root, row.get("path"), "source config")
        normalized_source.add((row.get("candidate"), seed, _relative(repo_root, path, "config"), row.get("sha256")))
    if normalized_source != normalized_manifest:
        _fail("source and selection manifest config hashes differ")
    return matrix


def _validate_selection(
    manifest: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    repo_root: Path,
) -> tuple[dict[str, dict[int, dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    exact = {
        "schema_version": 1,
        "frozen_before_test": True,
        "test_metrics_opened": False,
        "selection_segment": "valid",
        "selection_metric": "csi1000.valid.rank_ic_mean",
        "tie_breaker": ["rank_icir", "candidate_id"],
        "eval_label": EVAL_LABEL_EXPR,
        "eval_label_role": "fixed_1d",
        "min_count": MIN_COUNT,
        "data_version": FORMAL_DATA_VERSION,
        "official_valid_segment": list(VALID_SEGMENT),
        "effective_valid_segment": list(SAFE_VALID_SEGMENT),
        "seeds": list(SEEDS),
        "selected_candidate": WINNER,
        "selected_seeds": list(SEEDS),
    }
    for key, expected in exact.items():
        if manifest.get(key) != expected:
            _fail(f"selection manifest {key} protocol drift")

    configs = _selection_config_matrix(manifest, source, repo_root)
    valid_hashes = manifest.get("valid_result_hashes")
    candidates = manifest.get("candidates")
    if not isinstance(valid_hashes, dict) or set(valid_hashes) != set(CANDIDATES):
        _fail("selection manifest must contain four valid-result hashes")
    if not isinstance(candidates, dict) or set(candidates) != set(CANDIDATES):
        _fail("selection manifest must contain four candidates")

    recomputed: dict[str, dict[str, Any]] = {}
    for candidate in CANDIDATES:
        hash_row = valid_hashes[candidate]
        if not isinstance(hash_row, dict):
            _fail(f"valid artifact hash row invalid: {candidate}")
        path = _resolve_repo_path(repo_root, hash_row.get("path"), f"{candidate} valid artifact")
        if not path.is_file() or sha256_file(path) != hash_row.get("sha256"):
            _fail(f"valid artifact hash mismatch: {path}")
        result = _load_json(path, f"{candidate} valid artifact")
        if (
            result.get("candidate") != candidate
            or result.get("eval_segment_name") != "valid"
            or result.get("eval_label") != EVAL_LABEL_EXPR
            or result.get("eval_label_role") != "fixed_1d"
            or result.get("min_count") != MIN_COUNT
            or result.get("data_version") != FORMAL_DATA_VERSION
        ):
            _fail(f"valid artifact protocol drift: {candidate}")
        _exact_sequence(result.get("eval_segment"), VALID_SEGMENT, f"{candidate} valid segment")
        _exact_sequence(
            result.get("effective_eval_segment"),
            SAFE_VALID_SEGMENT,
            f"{candidate} effective valid segment",
        )
        config = _resolve_repo_path(repo_root, result.get("config"), f"{candidate} valid config")
        if config != configs[candidate][SEEDS[0]]["path"]:
            _fail(f"valid artifact seed-42 config drift: {candidate}")
        pools = result.get("pools")
        if not isinstance(pools, dict) or list(pools) != ["csi1000"]:
            _fail(f"valid artifact pool drift: {candidate}")
        summary = _validate_metric_pool(pools["csi1000"], f"{candidate}/valid/csi1000")
        sessions = _normalize_sessions(repo_root, result.get("sessions"), f"{candidate} valid")
        candidate_row = candidates[candidate]
        if not isinstance(candidate_row, dict):
            _fail(f"selection candidate row invalid: {candidate}")
        _same_number(candidate_row.get("rank_ic_mean"), summary["rank_ic_mean"], f"{candidate} selected rank_ic_mean")
        _same_number(candidate_row.get("rank_icir"), summary["rank_icir"], f"{candidate} selected rank_icir")
        if candidate_row.get("data_version") != FORMAL_DATA_VERSION:
            _fail(f"selection candidate data_version drift: {candidate}")
        _exact_sequence(candidate_row.get("seeds"), SEEDS, f"{candidate} selected seeds")
        if _normalize_sessions(repo_root, candidate_row.get("sessions"), f"{candidate} selected") != sessions:
            _fail(f"selection candidate sessions differ from valid artifact: {candidate}")
        recomputed[candidate] = {
            "rank_ic_mean": summary["rank_ic_mean"],
            "rank_icir": summary["rank_icir"],
            "sessions": sessions,
        }

    ordered = sorted(
        CANDIDATES,
        key=lambda candidate: (
            -recomputed[candidate]["rank_ic_mean"],
            -recomputed[candidate]["rank_icir"],
            candidate,
        ),
    )
    if manifest.get("candidate_order") != ordered or ordered[0] != WINNER:
        _fail("selection winner differs from independently replayed valid-only ordering")
    selected_sessions = _normalize_sessions(repo_root, manifest.get("selected_sessions"), "selected winner")
    if selected_sessions != recomputed[WINNER]["sessions"]:
        _fail("selected sessions differ from recomputed winner")

    winner_row = candidates[WINNER]
    provenance = winner_row.get("session_provenance")
    if not isinstance(provenance, list) or len(provenance) != len(SEEDS):
        _fail("selected winner provenance must contain exact five rows")
    return configs, selected_sessions, provenance


def _extract_best_iterations(model: Any, seed: int) -> list[int]:
    model_type = type(model)
    if (
        model_type.__name__ != "RankICEarlyStoppingDEnsembleModel"
        or model_type.__module__ != "backtest.models.rankic_early_stop"
    ):
        _fail(f"seed {seed} trained model class drift")
    if (
        getattr(model, "epochs", None) != 200
        or getattr(model, "early_stopping_rounds", None) != 20
        or getattr(model, "num_models", None) != 3
    ):
        _fail(f"seed {seed} trained model early-stop contract drift")
    params = getattr(model, "params", None)
    required_params = {
        "objective": "mse",
        "learning_rate": 0.1,
        "colsample_bytree": 0.8879,
        "subsample": 0.8789,
        "lambda_l1": 205.6999,
        "lambda_l2": 580.9768,
        "max_depth": 8,
        "num_leaves": 210,
        "seed": seed,
    }
    if not isinstance(params, Mapping) or any(params.get(key) != value for key, value in required_params.items()):
        _fail(f"seed {seed} trained model parameter contract drift")
    ensemble = getattr(model, "ensemble", None)
    if not isinstance(ensemble, list) or len(ensemble) != 3:
        _fail(f"seed {seed} trained model must contain three boosters")
    try:
        iterations = [int(item.best_iteration) for item in ensemble]
    except (AttributeError, TypeError, ValueError) as exc:
        _fail(f"seed {seed} best iterations cannot be extracted: {exc}")
    if iterations != EXPECTED_BEST_ITERATIONS[seed]:
        _fail(f"seed {seed} best iterations drift: {iterations}")
    return iterations


def _load_model(path: Path, seed: int) -> tuple[Any, list[int]]:
    try:
        with Path(path).open("rb") as handle:
            model = pickle.load(handle)
    except Exception as exc:
        _fail(f"seed {seed} trained model cannot be decoded: {path}: {exc}")
    return model, _extract_best_iterations(model, seed)


def _validate_winner_artifact(
    *,
    repo_root: Path,
    seed: int,
    session_path: Path,
    config_path: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    if not session_path.is_dir():
        raise FileNotFoundError(f"selected session missing: {session_path}")
    meta_path = session_path / "meta.json"
    link_path = session_path / "run_01/mlruns_link.json"
    for key, path, hash_key in (
        ("meta", meta_path, "meta_sha256"),
        ("mlruns_link", link_path, "mlruns_link_sha256"),
        ("config", config_path, "config_sha256"),
    ):
        expected_path_key = {"meta": "meta_path", "mlruns_link": "mlruns_link_path", "config": "config"}[key]
        if expected.get(expected_path_key) is not None:
            expected_path = _resolve_repo_path(repo_root, expected[expected_path_key], f"seed {seed} {key}")
            if expected_path != path:
                _fail(f"seed {seed} {key} path differs from frozen provenance")
        if not path.is_file() or sha256_file(path) != expected.get(hash_key):
            _fail(f"seed {seed} {key} hash mismatch: {path}")

    meta = _load_json(meta_path, f"seed {seed} meta")
    link = _load_json(link_path, f"seed {seed} MLflow link")
    runs = meta.get("runs")
    if not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], dict):
        _fail(f"seed {seed} meta must contain one run")
    run = runs[0]
    meta_config = _resolve_repo_path(repo_root, meta.get("config_path"), f"seed {seed} meta config")
    exact_meta = (
        meta.get("session_name") == session_path.name
        and meta.get("note") == f"mh_rankic_es_lr010_s{seed}"
        and meta.get("mode") == "train_only"
        and meta.get("n_runs") == 1
        and meta.get("market") == "csi1000"
        and meta_config == config_path
        and run.get("run") == 1
        and run.get("status") == "success"
        and run.get("backtest_experiment_id") is None
        and run.get("backtest_recorder_id") is None
    )
    if not exact_meta:
        _fail(f"seed {seed} session metadata/config binding drift")
    segments = meta.get("segments")
    if not isinstance(segments, dict):
        _fail(f"seed {seed} session segments missing")
    _exact_sequence(segments.get("train"), TRAIN_SEGMENT, f"seed {seed} train")
    _exact_sequence(segments.get("valid"), VALID_SEGMENT, f"seed {seed} valid")
    _exact_sequence(segments.get("test"), TEST_SEGMENT, f"seed {seed} test")
    id_keys = ("train_experiment_name", "train_experiment_id", "train_recorder_id")
    for key in id_keys:
        if not run.get(key) or str(run[key]) != str(link.get(key)) or str(run[key]) != str(expected.get(key)):
            _fail(f"seed {seed} meta/link/provenance {key} mismatch")
    artifact_root = _resolve_repo_path(repo_root, link.get("train_artifacts"), f"seed {seed} train artifacts")
    model_path = artifact_root / "artifacts/trained_model"
    expected_model = _resolve_repo_path(repo_root, expected.get("trained_model_path") or expected.get("model"), f"seed {seed} model")
    if model_path != expected_model or not model_path.is_file():
        _fail(f"seed {seed} model path mismatch")
    model_hash = expected.get("trained_model_sha256") or expected.get("model_sha256")
    if sha256_file(model_path) != model_hash:
        _fail(f"seed {seed} trained model hash mismatch: {model_path}")
    _, best_iterations = _load_model(model_path, seed)
    return {
        "seed": seed,
        "config": _relative(repo_root, config_path, "config"),
        "config_sha256": expected["config_sha256"],
        "session": _relative(repo_root, session_path, "session"),
        "meta": _relative(repo_root, meta_path, "meta"),
        "meta_sha256": expected["meta_sha256"],
        "mlruns_link": _relative(repo_root, link_path, "MLflow link"),
        "mlruns_link_sha256": expected["mlruns_link_sha256"],
        "model": _relative(repo_root, model_path, "model"),
        "model_sha256": model_hash,
        "best_iterations": best_iterations,
        **{key: str(run[key]) for key in id_keys},
    }


def _winner_artifacts(
    *,
    repo_root: Path,
    configs: Mapping[str, Mapping[int, Mapping[str, Any]]],
    sessions: Sequence[Mapping[str, Any]],
    provenance: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_seed: dict[int, Mapping[str, Any]] = {}
    for row in provenance:
        if not isinstance(row, Mapping):
            _fail("winner provenance row invalid")
        try:
            seed = int(row.get("seed"))
        except (TypeError, ValueError):
            _fail("winner provenance seed invalid")
        if seed not in SEEDS or seed in by_seed:
            _fail("winner provenance seed matrix invalid")
        by_seed[seed] = row
    if set(by_seed) != set(SEEDS):
        _fail("winner provenance must contain fixed five seeds")
    artifacts = []
    for session_row in sessions:
        seed = int(session_row["seed"])
        session = _resolve_repo_path(repo_root, session_row["session"], f"seed {seed} session")
        expected = dict(by_seed[seed])
        provenance_session = _resolve_session_path(repo_root, expected.get("session"), f"seed {seed} provenance")
        if session != provenance_session:
            _fail(f"seed {seed} selected/provenance session mismatch")
        config = configs[WINNER][seed]
        expected["config"] = str(config["path"])
        expected["config_sha256"] = config["sha256"]
        artifacts.append(
            _validate_winner_artifact(
                repo_root=repo_root,
                seed=seed,
                session_path=session,
                config_path=config["path"],
                expected=expected,
            )
        )
    return artifacts


def _freeze_manifest(
    *,
    repo_root: Path,
    selection_path: Path,
    selection_sha256: str,
    formal_path: Path,
    formal_sha256: str,
    formal_summary: Mapping[str, Mapping[str, float]],
    self_path: Path,
    self_sha256: str,
    self_summary: Mapping[str, Mapping[str, float]],
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "baseline_exp_id": B6_EXP_ID,
        "baseline_ref": B6_REF,
        "promoted_from": SOURCE_EXP_ID,
        "selected_candidate": WINNER,
        "selection_source": {
            "path": _relative(repo_root, selection_path, "selection manifest"),
            "sha256": selection_sha256,
        },
        "selection_metric": "csi1000.valid.rank_ic_mean",
        "selection_tie_breaker": ["rank_icir", "candidate_id"],
        "selection_min_count": MIN_COUNT,
        "seeds": list(SEEDS),
        "train_pool": "csi1000",
        "test_pools": list(TEST_POOLS),
        "model_contract": {
            "handler": "Alpha158Technical",
            "feature_groups": ["range"],
            "label_kind": "cumulative_return",
            "label_horizon": 40,
            "label": "Ref($close,-41)/Ref($close,-1)-1",
            "learn_processors": ["DropnaLabel", "CSRankNorm(label)"],
            "model": "RankICEarlyStoppingDEnsembleModel",
            "learning_rate": 0.1,
            "epochs": 200,
            "early_stopping_rounds": 20,
            "early_stopping_metric": "fixed_next_day_valid_daily_rank_ic",
            "train_segment": list(TRAIN_SEGMENT),
            "valid_segment": list(VALID_SEGMENT),
            "effective_valid_segment": list(SAFE_VALID_SEGMENT),
            "test_segment": list(TEST_SEGMENT),
        },
        "evaluations": {
            "formal_fixed_1d": {
                "path": _relative(repo_root, formal_path, "formal result"),
                "sha256": formal_sha256,
                "eval_label": EVAL_LABEL_EXPR,
                "eval_label_role": "fixed_1d",
                "data_version": FORMAL_DATA_VERSION,
                "segment": list(TEST_SEGMENT),
                "min_count": MIN_COUNT,
                "metrics_summary": formal_summary,
            },
            "diagnostic_self": {
                "path": _relative(repo_root, self_path, "self result"),
                "sha256": self_sha256,
                "eval_label": SELF_LABEL_EXPR,
                "eval_label_role": "self",
                "data_version": SELF_DATA_VERSION,
                "segment": list(TEST_SEGMENT),
                "metrics_summary": self_summary,
            },
        },
        "metrics_by_eval_label": {
            "eval_1d": formal_summary,
            "eval_self": self_summary,
        },
        "artifacts": list(artifacts),
    }


def _verify_freeze_mapping(manifest: Mapping[str, Any], *, repo_root: Path) -> dict[str, Any]:
    exact = {
        "schema_version": 1,
        "baseline_exp_id": B6_EXP_ID,
        "baseline_ref": B6_REF,
        "promoted_from": SOURCE_EXP_ID,
        "selected_candidate": WINNER,
        "selection_metric": "csi1000.valid.rank_ic_mean",
        "selection_tie_breaker": ["rank_icir", "candidate_id"],
        "selection_min_count": MIN_COUNT,
        "seeds": list(SEEDS),
        "train_pool": "csi1000",
        "test_pools": list(TEST_POOLS),
    }
    for key, expected in exact.items():
        if manifest.get(key) != expected:
            _fail(f"B6 freeze {key} drift")
    selection_source = manifest.get("selection_source")
    if not isinstance(selection_source, dict) or set(selection_source) != {
        "path",
        "sha256",
    }:
        _fail("B6 freeze selection_source schema drift")
    selection_raw_path = selection_source["path"]
    if Path(str(selection_raw_path)).is_absolute():
        _fail("B6 freeze selection_source path must be repository-relative")
    selection_path = _resolve_repo_path(
        repo_root,
        selection_raw_path,
        "B6 freeze selection source",
    )
    if (
        _relative(repo_root, selection_path, "selection source")
        != selection_raw_path
        or selection_raw_path
        != "backtest/experiments/b5_rankic_hyperparam_selection.json"
        or not _valid_sha256(selection_source["sha256"])
    ):
        _fail("B6 freeze selection_source path/hash drift")
    if (
        selection_path.exists()
        and sha256_file(selection_path) != selection_source["sha256"]
    ):
        _fail("B6 freeze selection_source hash mismatch")
    contract = manifest.get("model_contract")
    expected_contract = {
        "handler": "Alpha158Technical",
        "feature_groups": ["range"],
        "label_kind": "cumulative_return",
        "label_horizon": 40,
        "label": "Ref($close,-41)/Ref($close,-1)-1",
        "learn_processors": ["DropnaLabel", "CSRankNorm(label)"],
        "model": "RankICEarlyStoppingDEnsembleModel",
        "learning_rate": 0.1,
        "epochs": 200,
        "early_stopping_rounds": 20,
        "early_stopping_metric": "fixed_next_day_valid_daily_rank_ic",
        "train_segment": list(TRAIN_SEGMENT),
        "valid_segment": list(VALID_SEGMENT),
        "effective_valid_segment": list(SAFE_VALID_SEGMENT),
        "test_segment": list(TEST_SEGMENT),
    }
    if contract != expected_contract:
        _fail("B6 freeze model contract drift")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(SEEDS):
        _fail("B6 freeze must contain exactly five selected artifacts")
    by_seed: dict[int, Mapping[str, Any]] = {}
    for row in artifacts:
        if not isinstance(row, Mapping):
            _fail("B6 freeze artifact row invalid")
        try:
            seed = int(row.get("seed"))
        except (TypeError, ValueError):
            _fail("B6 freeze artifact seed invalid")
        if seed not in SEEDS or seed in by_seed:
            _fail("B6 freeze artifact seed matrix invalid")
        for key in ("config", "session", "meta", "mlruns_link", "model"):
            if Path(str(row.get(key))).is_absolute():
                _fail(f"B6 freeze {key} path must be repository-relative")
        by_seed[seed] = row
    if list(by_seed) != list(SEEDS):
        _fail("B6 freeze artifacts must be ordered by fixed seeds")

    normalized_artifacts = []
    selected_sessions = []
    for seed in SEEDS:
        row = by_seed[seed]
        config = _resolve_repo_path(repo_root, row.get("config"), f"seed {seed} config")
        validate_candidate_config(config, WINNER, seed)
        session = _resolve_session_path(repo_root, row.get("session"), f"seed {seed} session")
        expected = {
            "config": row.get("config"),
            "config_sha256": row.get("config_sha256"),
            "meta_path": row.get("meta"),
            "meta_sha256": row.get("meta_sha256"),
            "mlruns_link_path": row.get("mlruns_link"),
            "mlruns_link_sha256": row.get("mlruns_link_sha256"),
            "trained_model_path": row.get("model"),
            "trained_model_sha256": row.get("model_sha256"),
            "train_experiment_name": row.get("train_experiment_name"),
            "train_experiment_id": row.get("train_experiment_id"),
            "train_recorder_id": row.get("train_recorder_id"),
        }
        normalized = _validate_winner_artifact(
            repo_root=repo_root,
            seed=seed,
            session_path=session,
            config_path=config,
            expected=expected,
        )
        if normalized != dict(row):
            _fail(f"B6 freeze artifact row differs from independent verification: seed {seed}")
        normalized_artifacts.append(normalized)
        selected_sessions.append({"session": normalized["session"], "seed": seed})

    evaluations = manifest.get("evaluations")
    if not isinstance(evaluations, dict) or set(evaluations) != {"formal_fixed_1d", "diagnostic_self"}:
        _fail("B6 freeze evaluations must contain formal and diagnostic artifacts")
    selected_config = _resolve_repo_path(repo_root, normalized_artifacts[0]["config"], "selected config")
    summaries: dict[str, dict[str, dict[str, float]]] = {}
    for key, role in (("formal_fixed_1d", "eval_1d"), ("diagnostic_self", "eval_self")):
        row = evaluations[key]
        expected_metadata = (
            {
                "eval_label": EVAL_LABEL_EXPR,
                "eval_label_role": "fixed_1d",
                "data_version": FORMAL_DATA_VERSION,
                "segment": list(TEST_SEGMENT),
                "min_count": MIN_COUNT,
            }
            if role == "eval_1d"
            else {
                "eval_label": SELF_LABEL_EXPR,
                "eval_label_role": "self",
                "data_version": SELF_DATA_VERSION,
                "segment": list(TEST_SEGMENT),
            }
        )
        expected_keys = {
            "path",
            "sha256",
            "metrics_summary",
            *expected_metadata,
        }
        if not isinstance(row, dict) or set(row) != expected_keys:
            _fail(f"B6 freeze {key} evaluation schema drift")
        if any(row.get(field) != value for field, value in expected_metadata.items()):
            _fail(f"B6 freeze {key} evaluation metadata drift")
        if Path(str(row.get("path"))).is_absolute():
            _fail(f"B6 freeze {key} evaluation path must be repository-relative")
        path = _resolve_repo_path(repo_root, row.get("path"), key)
        if (
            _relative(repo_root, path, key) != row.get("path")
            or not _valid_sha256(row.get("sha256"))
            or not path.is_file()
            or sha256_file(path) != row.get("sha256")
        ):
            _fail(f"B6 freeze {key} evaluation hash mismatch")
        result = _load_json(path, key)
        summary = _validate_evaluation(
            result,
            repo_root=repo_root,
            selected_sessions=selected_sessions,
            selected_config=selected_config,
            role=role,
        )
        _compare_summary(row.get("metrics_summary"), summary, f"B6 freeze {key}")
        summaries[role] = summary
    by_label = manifest.get("metrics_by_eval_label")
    if not isinstance(by_label, dict) or set(by_label) != {"eval_1d", "eval_self"}:
        _fail("B6 freeze metrics_by_eval_label drift")
    for role, summary in summaries.items():
        _compare_summary(by_label.get(role), summary, f"B6 freeze {role}")
    return dict(manifest)


def verify_b6_freeze_manifest(
    manifest: Path | Mapping[str, Any],
    *,
    repo_root: Path = QLIB_ROOT,
) -> dict[str, Any]:
    """Verify the selected-only freeze without consulting loser sessions."""

    repo_root = Path(repo_root).expanduser().resolve()
    value = _load_json(Path(manifest), "B6 freeze manifest") if isinstance(manifest, (str, Path)) else dict(manifest)
    return _verify_freeze_mapping(value, repo_root=repo_root)


def _encode_json(value: Mapping[str, Any], *, indent: Optional[int] = None) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=indent,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail(f"promotion payload is not finite JSON: {exc}")


def _build_b6_row(
    *,
    repo_root: Path,
    freeze_path: Path,
    freeze_sha256: str,
    freeze: Mapping[str, Any],
) -> dict[str, Any]:
    artifacts = list(freeze["artifacts"])
    formal = freeze["evaluations"]["formal_fixed_1d"]
    self_eval = freeze["evaluations"]["diagnostic_self"]
    sessions = [row["session"] for row in artifacts]
    return {
        "exp_id": B6_EXP_ID,
        "direction": "baseline",
        "phase": "M",
        "date": date.today().isoformat(),
        "hypothesis": (
            "将model-hyperparam/valid-rankic-search-v1按valid RankIC冻结选出的"
            "rankic-es-lr010五种子组提升为B6-M；固定次日指标为正式基线，"
            "H40同标签指标仅作诊断。"
        ),
        "baseline_ref": B6_REF,
        "promoted_from": SOURCE_EXP_ID,
        "selected_candidate": WINNER,
        "selection_min_count": MIN_COUNT,
        "seeds": list(SEEDS),
        "train_pool": "csi1000",
        "test_pools": list(TEST_POOLS),
        "data_version": FORMAL_DATA_VERSION,
        "self_data_version": SELF_DATA_VERSION,
        "label_kind": "cumulative_return",
        "label_horizon": 40,
        "label": "Ref($close,-41)/Ref($close,-1)-1",
        "purge_trading_days": 41,
        "feature_groups": ["range"],
        "model": "RankICEarlyStoppingDEnsembleModel",
        "training_objective": "H40 CSRankNorm MSE",
        "learn_processors": ["DropnaLabel", "CSRankNorm(label)"],
        "learning_rate": 0.1,
        "epochs": 200,
        "early_stopping_metric": "fixed_next_day_valid_daily_rank_ic",
        "early_stopping_rounds": 20,
        "selection_official_segment": list(VALID_SEGMENT),
        "selection_effective_segment": list(SAFE_VALID_SEGMENT),
        "test_segment": list(TEST_SEGMENT),
        "eval_label": EVAL_LABEL_EXPR,
        "eval_label_role": "fixed_1d",
        "self_eval_label": SELF_LABEL_EXPR,
        "self_eval_label_role": "self",
        "configs": [row["config"] for row in artifacts],
        "config_hashes": [
            {"seed": row["seed"], "path": row["config"], "sha256": row["config_sha256"]}
            for row in artifacts
        ],
        "session_dirs": sessions,
        "models": [row["model"] for row in artifacts],
        "model_hashes": [
            {"seed": row["seed"], "path": row["model"], "sha256": row["model_sha256"]}
            for row in artifacts
        ],
        "session_audit": artifacts,
        "selection_manifest": freeze["selection_source"]["path"],
        "selection_manifest_sha256": freeze["selection_source"]["sha256"],
        "freeze_manifest": _relative(repo_root, freeze_path, "freeze manifest"),
        "freeze_manifest_sha256": freeze_sha256,
        "formal_result": formal["path"],
        "formal_result_sha256": formal["sha256"],
        "self_result": self_eval["path"],
        "self_result_sha256": self_eval["sha256"],
        "result_dirs": [
            *sessions,
            formal["path"],
            self_eval["path"],
            _relative(repo_root, freeze_path, "freeze manifest"),
        ],
        "metrics_summary": freeze["metrics_by_eval_label"]["eval_1d"],
        "metrics_by_eval_label": freeze["metrics_by_eval_label"],
        "evaluation_comparable_to_baseline": True,
        "cleanup_retention_eligible": True,
        "conclusion": "baseline",
        "note": "B6-M研究基线；实盘配置与artifact未在本次promotion中切换。",
    }


def _stage(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return Path(handle.name)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _restore(path: Path, original: Optional[bytes]) -> None:
    if original is None:
        path.unlink(missing_ok=True)
        return
    staged = _stage(path, original)
    os.replace(staged, path)


def _matches_original(path: Path, original: Optional[bytes]) -> bool:
    if original is None:
        return not path.exists()
    return path.is_file() and path.read_bytes() == original


def _publish_payloads(
    payloads: Sequence[tuple[Path, bytes]],
    *,
    expected_originals: Mapping[Path, Optional[bytes]],
    no_replace: set[Path],
) -> None:
    if {path for path, _ in payloads} != set(expected_originals):
        _fail("publication expected-original matrix drift")
    staged: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for path, payload in payloads:
            staged[path] = _stage(path, payload)
        for path, _ in payloads:
            if not _matches_original(path, expected_originals[path]):
                _fail(f"destination changed while staging promotion: {path}")
        for path, _ in payloads:
            if not _matches_original(path, expected_originals[path]):
                _fail(f"destination changed before publication: {path}")
            if path in no_replace:
                os.link(staged[path], path)
                committed.append(path)
                staged[path].unlink()
            else:
                os.replace(staged[path], path)
                committed.append(path)
            staged.pop(path, None)
    except Exception:
        for path in reversed(committed):
            _restore(path, expected_originals[path])
        raise
    finally:
        for temp in staged.values():
            temp.unlink(missing_ok=True)


@contextmanager
def _exclusive_promotion_lock(registry_path: Path):
    lock_id = hashlib.sha256(str(registry_path).encode("utf-8")).hexdigest()[:24]
    lock_path = Path(tempfile.gettempdir()) / f"qlib-b6-promotion-{lock_id}.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _promote_b6_baseline_locked(
    *,
    repo_root: Path = QLIB_ROOT,
    registry_path: Path = DEFAULT_REGISTRY,
    selection_manifest_path: Path = DEFAULT_SELECTION_MANIFEST,
    formal_result_path: Path = DEFAULT_FORMAL_RESULT,
    self_result_path: Path = DEFAULT_SELF_RESULT,
    freeze_path: Path = DEFAULT_FREEZE,
    report_path: Path = DEFAULT_REPORT,
    expected_self_sha256: str = EXPECTED_SELF_SHA256,
) -> dict[str, Any]:
    """Validate and atomically publish the selected-only B6 promotion."""

    repo_root = Path(repo_root).expanduser().resolve()
    registry_path = _inside_repo(repo_root, Path(registry_path), "registry")
    selection_manifest_path = _inside_repo(repo_root, Path(selection_manifest_path), "selection manifest")
    formal_result_path = _inside_repo(repo_root, Path(formal_result_path), "formal result")
    self_result_path = _inside_repo(repo_root, Path(self_result_path), "self result")
    freeze_path = _inside_repo(repo_root, Path(freeze_path), "freeze manifest")
    report_path = _inside_repo(repo_root, Path(report_path), "report")
    if freeze_path.exists():
        raise FileExistsError(f"refusing to overwrite existing freeze manifest: {freeze_path}")

    registry_raw = registry_path.read_bytes()
    report_original = report_path.read_bytes() if report_path.exists() else None
    rows = _registry_rows(registry_raw)
    if any(row.get("exp_id") == B6_EXP_ID for row in rows):
        _fail(f"{B6_EXP_ID} already exists")
    b5 = _single_row(rows, B5_EXP_ID)
    if b5.get("baseline_ref") != B5_REF or b5.get("conclusion") != "baseline":
        _fail("historical B5 anchor protocol drift")
    source = _single_row(rows, SOURCE_EXP_ID)
    _validate_source_row(source)

    source_selection = _resolve_repo_path(repo_root, source.get("selection_manifest"), "source selection manifest")
    if source_selection != selection_manifest_path:
        _fail("promotion selection path differs from source experiment")
    selection_sha = sha256_file(selection_manifest_path)
    if selection_sha != source.get("selection_manifest_sha256"):
        _fail("source selection manifest hash mismatch")
    manifest = _load_json(selection_manifest_path, "selection manifest")
    configs, sessions, provenance = _validate_selection(manifest, source, repo_root=repo_root)

    source_formal = _resolve_repo_path(repo_root, source.get("test_result"), "source formal result")
    if source_formal != formal_result_path:
        _fail("promotion formal result path differs from source experiment")
    formal_sha = sha256_file(formal_result_path)
    if formal_sha != source.get("test_result_sha256"):
        _fail("source formal result hash mismatch")
    formal = _load_json(formal_result_path, "formal fixed-one-day result")
    if formal.get("selection_manifest_sha256") != selection_sha:
        _fail("formal result selection manifest hash mismatch")

    artifacts = _winner_artifacts(
        repo_root=repo_root,
        configs=configs,
        sessions=sessions,
        provenance=provenance,
    )
    selected_config = configs[WINNER][SEEDS[0]]["path"]
    formal_summary = _validate_evaluation(
        formal,
        repo_root=repo_root,
        selected_sessions=sessions,
        selected_config=selected_config,
        role="eval_1d",
    )
    _compare_summary(source.get("metrics_summary"), formal_summary, "source formal metrics_summary")
    source_by_label = source.get("metrics_by_eval_label")
    if not isinstance(source_by_label, dict) or set(source_by_label) != {"eval_1d"}:
        _fail("source metrics_by_eval_label must contain only formal eval_1d")
    _compare_summary(source_by_label["eval_1d"], formal_summary, "source eval_1d summary")

    self_sha = sha256_file(self_result_path)
    if self_sha != expected_self_sha256:
        _fail(
            "diagnostic self result SHA-256 mismatch: "
            f"expected {expected_self_sha256}, got {self_sha}"
        )
    self_result = _load_json(self_result_path, "diagnostic self result")
    self_summary = _validate_evaluation(
        self_result,
        repo_root=repo_root,
        selected_sessions=sessions,
        selected_config=selected_config,
        role="eval_self",
    )

    freeze = _freeze_manifest(
        repo_root=repo_root,
        selection_path=selection_manifest_path,
        selection_sha256=selection_sha,
        formal_path=formal_result_path,
        formal_sha256=formal_sha,
        formal_summary=formal_summary,
        self_path=self_result_path,
        self_sha256=self_sha,
        self_summary=self_summary,
        artifacts=artifacts,
    )
    _verify_freeze_mapping(freeze, repo_root=repo_root)
    freeze_payload = _encode_json(freeze, indent=2) + b"\n"
    freeze_sha = _sha256_bytes(freeze_payload)
    b6_row = _build_b6_row(
        repo_root=repo_root,
        freeze_path=freeze_path,
        freeze_sha256=freeze_sha,
        freeze=freeze,
    )
    b6_payload = _encode_json(b6_row)
    separator = b"" if not registry_raw or registry_raw.endswith((b"\n", b"\r")) else b"\n"
    registry_payload = registry_raw + separator + b6_payload + b"\n"
    report_payload = report_builder.build_html([*rows, b6_row]).encode("utf-8")

    # Re-check the two mutable pre-existing destinations immediately before
    # publication so concurrent edits cannot be overwritten.
    if registry_path.read_bytes() != registry_raw:
        _fail("registry changed during promotion validation")
    if freeze_path.exists():
        raise FileExistsError(f"freeze manifest appeared during promotion: {freeze_path}")
    _publish_payloads(
        [
            (freeze_path, freeze_payload),
            (report_path, report_payload),
            (registry_path, registry_payload),
        ],
        expected_originals={
            freeze_path: None,
            report_path: report_original,
            registry_path: registry_raw,
        },
        no_replace={freeze_path},
    )
    return {"row": b6_row, "freeze_manifest": freeze}


def promote_b6_baseline(
    *,
    repo_root: Path = QLIB_ROOT,
    registry_path: Path = DEFAULT_REGISTRY,
    selection_manifest_path: Path = DEFAULT_SELECTION_MANIFEST,
    formal_result_path: Path = DEFAULT_FORMAL_RESULT,
    self_result_path: Path = DEFAULT_SELF_RESULT,
    freeze_path: Path = DEFAULT_FREEZE,
    report_path: Path = DEFAULT_REPORT,
    expected_self_sha256: str = EXPECTED_SELF_SHA256,
) -> dict[str, Any]:
    """Validate and publish B6 while holding the promotion lock."""

    normalized_root = Path(repo_root).expanduser().resolve()
    lock_registry = Path(registry_path).expanduser()
    if not lock_registry.is_absolute():
        lock_registry = normalized_root / lock_registry
    lock_registry = _inside_repo(normalized_root, lock_registry, "registry")
    with _exclusive_promotion_lock(lock_registry):
        return _promote_b6_baseline_locked(
            repo_root=normalized_root,
            registry_path=lock_registry,
            selection_manifest_path=selection_manifest_path,
            formal_result_path=formal_result_path,
            self_result_path=self_result_path,
            freeze_path=freeze_path,
            report_path=report_path,
            expected_self_sha256=expected_self_sha256,
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote frozen rankic-es-lr010 to B6-M")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--selection-manifest", type=Path, default=DEFAULT_SELECTION_MANIFEST)
    parser.add_argument("--formal-result", type=Path, default=DEFAULT_FORMAL_RESULT)
    parser.add_argument("--self-result", type=Path, default=DEFAULT_SELF_RESULT)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    result = promote_b6_baseline(
        registry_path=args.registry,
        selection_manifest_path=args.selection_manifest,
        formal_result_path=args.formal_result,
        self_result_path=args.self_result,
        freeze_path=args.freeze,
        report_path=args.report,
    )
    print(
        f"promoted {result['row']['promoted_from']} -> {result['row']['exp_id']} "
        f"({len(result['freeze_manifest']['artifacts'])} seeds)"
    )


if __name__ == "__main__":
    main()
