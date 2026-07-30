"""Freeze the B5 valid-only RankIC hyperparameter winner before test."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml

from backtest.scripts.config_loader import RESULT_ROOT


CANDIDATES = (
    "rankic-es-base",
    "rankic-es-l1low",
    "rankic-es-lr010",
    "rankic-es-leaves128",
)
SEEDS = (42, 1000, 2000, 3000, 4000)
TRAIN_SEGMENT = ("2016-01-02", "2020-01-10")
VALID_SEGMENT = ("2020-01-13", "2021-07-15")
SAFE_VALID_SEGMENT = ("2020-01-13", "2021-07-13")
TEST_SEGMENT = ("2021-07-16", "2026-07-16")
EVAL_LABEL_EXPR = "Ref($close, -2)/Ref($close, -1) - 1"
MIN_COUNT = 20
VALID_POOL = "csi1000"
TEST_POOLS = ("csi1000", "csi300", "csi500")
MODEL_CLASS = "RankICEarlyStoppingDEnsembleModel"
MODEL_MODULE = "backtest.models.rankic_early_stop"

_VARIANT_OVERRIDES = {
    "rankic-es-base": {},
    "rankic-es-l1low": {"lambda_l1": 51.425},
    "rankic-es-lr010": {"learning_rate": 0.1},
    "rankic-es-leaves128": {"num_leaves": 128},
}
_BASE_MODEL_KWARGS = {
    "base_model": "gbm",
    "loss": "mse",
    "num_models": 3,
    "enable_sr": True,
    "enable_fs": True,
    "alpha1": 1,
    "alpha2": 1,
    "bins_sr": 10,
    "bins_fs": 5,
    "decay": 0.5,
    "sample_ratios": [0.8, 0.7, 0.6, 0.5, 0.4],
    "sub_weights": [1, 1, 1],
    "epochs": 200,
    "colsample_bytree": 0.8879,
    "learning_rate": 0.2,
    "subsample": 0.8789,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 8,
    "num_leaves": 210,
    "num_threads": 8,
    "early_stopping_rounds": 20,
    "valid_segment": list(VALID_SEGMENT),
    "test_segment": list(TEST_SEGMENT),
}
_TEST_BASENAME = re.compile(r"(^|[_-])test([_.-]|$)", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json_exclusive_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically publish complete JSON while refusing an existing target."""

    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
    temp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as fh:
            temp_name = fh.name
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.link(temp_name, path)
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite: {path}") from None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def _fail(message: str) -> None:
    raise ValueError(message)


def _exact_segment(value: Any, expected: tuple[str, str], label: str) -> None:
    if not isinstance(value, (list, tuple)) or tuple(map(str, value)) != expected:
        _fail(f"{label} segment drift: expected {list(expected)!r}, got {value!r}")


def _load_yaml_mapping(path: Path) -> dict:
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        _fail(f"config cannot be loaded: {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"config must be a mapping: {path}")
    return value


def validate_candidate_config(path: Path, candidate: str, seed: int) -> dict:
    """Validate one generated YAML against the frozen B5 candidate contract."""

    path = Path(path).resolve()
    expected_name = f"mh_{candidate.replace('-', '_')}_s{seed}.yaml"
    if candidate not in CANDIDATES:
        _fail(f"config candidate is not declared: {candidate}")
    if path.parent.name != candidate or path.name != expected_name:
        _fail(
            f"config path is not bound to candidate/seed: {path} "
            f"(expected .../{candidate}/{expected_name})"
        )
    cfg = _load_yaml_mapping(path)
    run = cfg.get("run")
    if not isinstance(run, dict):
        _fail(f"config run section missing: {path}")
    expected_note = f"mh_{candidate.replace('-', '_')}_s{seed}"
    if (
        run.get("mode") != "train_only"
        or run.get("note") != expected_note
        or int(run.get("n_runs", 0)) != 1
    ):
        _fail(f"config run binding drift: {path}")

    data = cfg.get("data")
    if not isinstance(data, dict) or data.get("instruments") != VALID_POOL:
        _fail(f"config training pool drift: {path}")
    handler = data.get("handler")
    if (
        not isinstance(handler, dict)
        or handler.get("class") != "Alpha158Technical"
        or handler.get("module_path") != "backtest.features.technical"
        or handler.get("fit_start_time") != TRAIN_SEGMENT[0]
        or handler.get("fit_end_time") != TRAIN_SEGMENT[1]
        or handler.get("label")
        != [["Ref($close, -41)/Ref($close, -1)-1"], ["LABEL0"]]
        or handler.get("feature_groups") != ["range"]
        or handler.get("learn_processors")
        != [
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ]
    ):
        _fail(f"config B5 handler drift: {path}")

    segments = cfg.get("segments")
    if not isinstance(segments, dict):
        _fail(f"config segments missing: {path}")
    _exact_segment(segments.get("train"), TRAIN_SEGMENT, f"config {path} train")
    _exact_segment(segments.get("valid"), VALID_SEGMENT, f"config {path} valid")
    _exact_segment(segments.get("test"), TEST_SEGMENT, f"config {path} test")

    model = cfg.get("model")
    if (
        not isinstance(model, dict)
        or model.get("class") != MODEL_CLASS
        or model.get("module_path") != MODEL_MODULE
    ):
        _fail(f"config model class drift: {path}")
    expected_kwargs = dict(_BASE_MODEL_KWARGS)
    expected_kwargs["seed"] = seed
    expected_kwargs.update(_VARIANT_OVERRIDES[candidate])
    if model.get("kwargs") != expected_kwargs:
        _fail(f"config model kwargs drift or non-declared override: {path}")
    return cfg


def validate_config_set(
    candidate: str,
    config_paths: Sequence[Path],
) -> dict[int, Path]:
    if len(config_paths) != len(SEEDS):
        _fail(f"config set for {candidate} must contain exactly five configs")
    by_seed: dict[int, Path] = {}
    for raw_path in config_paths:
        path = Path(raw_path).resolve()
        cfg = _load_yaml_mapping(path)
        raw_seed = (cfg.get("model") or {}).get("kwargs", {}).get("seed")
        try:
            seed = int(raw_seed)
        except (TypeError, ValueError):
            _fail(f"config seed invalid: {path}")
        if seed in by_seed:
            _fail(f"config seed duplicate for {candidate}: {seed}")
        validate_candidate_config(path, candidate, seed)
        by_seed[seed] = path
    if set(by_seed) != set(SEEDS):
        _fail(
            f"config seeds for {candidate} differ from fixed seeds: "
            f"{sorted(by_seed)}"
        )
    return by_seed


def _resolve_session(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(RESULT_ROOT) / path
    path = path.resolve()
    root = Path(RESULT_ROOT).resolve()
    if path.parent != root or not path.is_dir():
        _fail(f"session must be a direct child of result root: {raw}")
    return path


def validate_session(
    raw_session: str,
    *,
    seed: int,
    candidate: str,
    config_path: Path,
) -> str:
    session = _resolve_session(raw_session)
    meta_path = session / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"session meta invalid: {session}: {exc}")
    expected_note = f"mh_{candidate.replace('-', '_')}_s{seed}"
    try:
        meta_config = Path(meta["config_path"]).expanduser().resolve()
    except (KeyError, TypeError):
        _fail(f"session config binding missing: {session}")
    runs = meta.get("runs")
    if (
        meta.get("session_name") != session.name
        or meta.get("note") != expected_note
        or meta.get("mode") != "train_only"
        or int(meta.get("n_runs", 0)) != 1
        or meta.get("market") != VALID_POOL
        or meta_config != Path(config_path).resolve()
        or not isinstance(runs, list)
        or len(runs) != 1
    ):
        _fail(f"session metadata/config/seed binding drift: {session}")
    segments = meta.get("segments")
    if not isinstance(segments, dict):
        _fail(f"session segments missing: {session}")
    _exact_segment(segments.get("train"), TRAIN_SEGMENT, f"session {session} train")
    _exact_segment(segments.get("valid"), VALID_SEGMENT, f"session {session} valid")
    _exact_segment(segments.get("test"), TEST_SEGMENT, f"session {session} test")
    run = runs[0]
    if (
        run.get("run") != 1
        or run.get("status") != "success"
        or not run.get("train_experiment_id")
        or not run.get("train_recorder_id")
        or run.get("backtest_experiment_id") is not None
        or run.get("backtest_recorder_id") is not None
    ):
        _fail(f"session must contain exactly one successful train-only run: {session}")
    return str(session)


def normalize_and_validate_sessions(
    sessions: Sequence[tuple[str, Any]],
    *,
    candidate: str,
    configs_by_seed: Mapping[int, Path],
) -> list[tuple[str, int]]:
    if len(sessions) != len(SEEDS):
        _fail(f"session seed set for {candidate} must contain exactly five rows")
    by_seed: dict[int, str] = {}
    seen_paths: set[str] = set()
    for raw_session, raw_seed in sessions:
        try:
            seed = int(raw_seed)
        except (TypeError, ValueError):
            _fail(f"session seed invalid for {candidate}: {raw_seed!r}")
        if seed in by_seed:
            _fail(f"session seed duplicate for {candidate}: {seed}")
        if seed not in configs_by_seed:
            _fail(f"session seed is not fixed for {candidate}: {seed}")
        session = validate_session(
            raw_session,
            seed=seed,
            candidate=candidate,
            config_path=configs_by_seed[seed],
        )
        if session in seen_paths:
            _fail(f"session path duplicate for {candidate}: {session}")
        seen_paths.add(session)
        by_seed[seed] = session
    if set(by_seed) != set(SEEDS):
        _fail(f"session seeds differ from fixed seeds for {candidate}: {sorted(by_seed)}")
    return [(by_seed[seed], seed) for seed in SEEDS]


def _finite_metric(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        _fail(f"{label} must be a finite number")
    if not math.isfinite(number):
        _fail(f"{label} must be finite")
    return number


def _check_seed_mean(actual: Any, expected: float, label: str) -> None:
    number = _finite_metric(actual, label)
    if not math.isclose(number, expected, rel_tol=1e-12, abs_tol=1e-15):
        _fail(f"{label} does not match recomputed seed_mean")


def validate_valid_result(
    candidate: str,
    result: Mapping[str, Any],
    *,
    artifact_path: Path,
    configs_by_seed: Mapping[int, Path],
) -> dict[str, Any]:
    artifact_path = Path(artifact_path).resolve()
    if _TEST_BASENAME.search(artifact_path.name):
        _fail(f"valid artifact basename must not identify test data: {artifact_path.name}")
    if any(str(key).lower().startswith("test") for key in result):
        _fail("valid artifact contains a top-level test key")
    if result.get("candidate") not in (None, candidate):
        _fail(f"valid artifact candidate binding drift: {candidate}")
    if result.get("eval_segment_name") != "valid":
        _fail(f"valid artifact segment name drift: {candidate}")
    _exact_segment(result.get("eval_segment"), VALID_SEGMENT, "official valid")
    _exact_segment(
        result.get("effective_eval_segment"),
        SAFE_VALID_SEGMENT,
        "effective valid",
    )
    if (
        result.get("eval_label") != EVAL_LABEL_EXPR
        or result.get("eval_label_role") != "fixed_1d"
    ):
        _fail(f"valid artifact label protocol drift: {candidate}")
    if result.get("min_count") != MIN_COUNT:
        _fail(f"valid artifact min_count drift: {candidate}")
    if result.get("config") is None:
        _fail(f"valid artifact config binding missing: {candidate}")
    if Path(str(result["config"])).expanduser().resolve() != configs_by_seed[SEEDS[0]]:
        _fail(f"valid artifact config binding drift: {candidate}")

    pools = result.get("pools")
    if not isinstance(pools, dict) or list(pools) != [VALID_POOL]:
        _fail(f"valid artifact pool set must be exactly [{VALID_POOL!r}]")
    pool = pools[VALID_POOL]
    if not isinstance(pool, dict):
        _fail(f"valid artifact pool payload missing: {candidate}")
    seed_rows = pool.get("seeds")
    if not isinstance(seed_rows, dict) or set(seed_rows) != {str(s) for s in SEEDS}:
        _fail(f"valid artifact metric seed set differs from fixed seeds: {candidate}")

    raw_sessions = result.get("sessions")
    if not isinstance(raw_sessions, list):
        _fail(f"valid artifact session rows missing: {candidate}")
    session_pairs = []
    for row in raw_sessions:
        if not isinstance(row, dict) or set(row) != {"session", "seed"}:
            _fail(f"valid artifact session row invalid: {candidate}")
        session_pairs.append((row["session"], row["seed"]))
    sessions = normalize_and_validate_sessions(
        session_pairs,
        candidate=candidate,
        configs_by_seed=configs_by_seed,
    )

    rank_values = []
    ir_values = []
    for seed in SEEDS:
        row = seed_rows[str(seed)]
        if not isinstance(row, dict):
            _fail(f"valid artifact metrics missing for seed {seed}")
        rank_values.append(
            _finite_metric(
                row.get("rank_ic_mean"),
                f"{candidate}/{seed} rank_ic_mean",
            )
        )
        ir_values.append(
            _finite_metric(
                row.get("rank_icir"),
                f"{candidate}/{seed} rank_icir",
            )
        )
    rank_mean = sum(rank_values) / len(rank_values)
    rank_icir = sum(ir_values) / len(ir_values)
    seed_mean = pool.get("seed_mean")
    if not isinstance(seed_mean, dict):
        _fail(f"valid artifact seed_mean missing: {candidate}")
    _check_seed_mean(
        seed_mean.get("rank_ic_mean"),
        rank_mean,
        f"{candidate} seed_mean rank_ic_mean",
    )
    _check_seed_mean(
        seed_mean.get("rank_icir"),
        rank_icir,
        f"{candidate} seed_mean rank_icir",
    )
    return {
        "rank_ic_mean": rank_mean,
        "rank_icir": rank_icir,
        "seeds": list(SEEDS),
        "sessions": [
            {"session": session, "seed": seed} for session, seed in sessions
        ],
    }


def select_candidate(
    valid_results: Mapping[str, Mapping[str, Any]],
    config_paths: Mapping[str, Sequence[Path]],
    valid_result_paths: Mapping[str, Path],
    *,
    generated_at: Optional[str] = None,
) -> dict:
    """Recompute the four candidates and return an auditable frozen manifest."""

    expected = set(CANDIDATES)
    for label, keys in (
        ("valid result", set(valid_results)),
        ("config", set(config_paths)),
        ("valid artifact", set(valid_result_paths)),
    ):
        if keys != expected:
            _fail(f"{label} candidates differ from declared set: {sorted(keys)}")

    candidate_rows: dict[str, dict] = {}
    config_hashes: list[dict] = []
    valid_hashes: dict[str, dict] = {}
    for candidate in CANDIDATES:
        configs_by_seed = validate_config_set(candidate, config_paths[candidate])
        artifact_path = Path(valid_result_paths[candidate]).resolve()
        try:
            artifact_result = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(f"valid artifact cannot be loaded: {artifact_path}: {exc}")
        metrics = validate_valid_result(
            candidate,
            valid_results[candidate],
            artifact_path=artifact_path,
            configs_by_seed=configs_by_seed,
        )
        if artifact_result != valid_results[candidate]:
            _fail(f"valid artifact content differs from supplied result: {candidate}")
        per_candidate_configs = []
        for seed in SEEDS:
            path = configs_by_seed[seed]
            row = {
                "candidate": candidate,
                "seed": seed,
                "path": str(path),
                "sha256": sha256_file(path),
            }
            config_hashes.append(row)
            per_candidate_configs.append(row)
        valid_hashes[candidate] = {
            "path": str(artifact_path),
            "sha256": sha256_file(artifact_path),
        }
        candidate_rows[candidate] = {
            **metrics,
            "valid_artifact": valid_hashes[candidate],
            "configs": per_candidate_configs,
        }

    ordered = sorted(
        CANDIDATES,
        key=lambda candidate: (
            -candidate_rows[candidate]["rank_ic_mean"],
            -candidate_rows[candidate]["rank_icir"],
            candidate,
        ),
    )
    winner = ordered[0]
    return {
        "schema_version": 1,
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
        "frozen_before_test": True,
        "test_metrics_opened": False,
        "selection_segment": "valid",
        "selection_metric": "csi1000.valid.rank_ic_mean",
        "tie_breaker": ["rank_icir", "candidate_id"],
        "eval_label": EVAL_LABEL_EXPR,
        "eval_label_role": "fixed_1d",
        "min_count": MIN_COUNT,
        "official_valid_segment": list(VALID_SEGMENT),
        "effective_valid_segment": list(SAFE_VALID_SEGMENT),
        "seeds": list(SEEDS),
        "candidate_order": ordered,
        "candidates": candidate_rows,
        "selected_candidate": winner,
        "selected_seeds": list(SEEDS),
        "selected_sessions": candidate_rows[winner]["sessions"],
        "valid_result_hashes": valid_hashes,
        "config_hashes": config_hashes,
    }


def freeze_selection(
    *,
    valid_results: Mapping[str, Mapping[str, Any]],
    config_paths: Mapping[str, Sequence[Path]],
    valid_result_paths: Mapping[str, Path],
    output: Path,
) -> dict:
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    manifest = select_candidate(valid_results, config_paths, valid_result_paths)
    write_json_exclusive_atomic(output, manifest)
    return manifest


def _parse_candidate_paths(values: Sequence[str], label: str) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for raw in values:
        candidate, separator, path = raw.partition("=")
        if not separator or candidate not in CANDIDATES or not path:
            _fail(f"{label} must use CANDIDATE=PATH for declared candidates: {raw}")
        if candidate in parsed:
            _fail(f"duplicate {label} candidate: {candidate}")
        parsed[candidate] = Path(path)
    if set(parsed) != set(CANDIDATES):
        _fail(f"{label} must include all four candidates")
    return parsed


def _default_config_paths() -> dict[str, list[Path]]:
    root = Path(__file__).resolve().parents[1] / "configs" / "model-hyperparam"
    return {
        candidate: [
            root
            / candidate
            / f"mh_{candidate.replace('-', '_')}_s{seed}.yaml"
            for seed in SEEDS
        ]
        for candidate in CANDIDATES
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze the four-candidate B5 valid RankIC selection"
    )
    parser.add_argument(
        "--valid-results",
        nargs=4,
        required=True,
        metavar="CANDIDATE=PATH",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    artifact_paths = _parse_candidate_paths(args.valid_results, "valid result")
    valid_results = {
        candidate: json.loads(path.read_text(encoding="utf-8"))
        for candidate, path in artifact_paths.items()
    }
    manifest = freeze_selection(
        valid_results=valid_results,
        config_paths=_default_config_paths(),
        valid_result_paths=artifact_paths,
        output=args.output,
    )
    print(
        f"written: {args.output} "
        f"(selected {manifest['selected_candidate']})"
    )


if __name__ == "__main__":
    main()
