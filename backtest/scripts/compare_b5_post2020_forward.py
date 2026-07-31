"""Compare the frozen B5 stale-window control with its post-2020 treatment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml


PROTOCOL_ID = "post2020-forward-v1"
SEEDS = (42, 1000, 2000, 3000, 4000)
POOLS = ("csi1000", "csi300", "csi500")
METRICS = ("ic_mean", "icir", "rank_ic_mean", "rank_icir")
EVAL_LABEL = "Ref($close, -2)/Ref($close, -1) - 1"
TEST_SEGMENT = ("2024-07-01", "2026-07-16")
CONTROL_ROLE = "same-window-control"
EXPANDED_ROLE = "treatment"


def _fail(message: str) -> None:
    raise ValueError(message)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _reject_nonfinite_constant(value: str) -> None:
    _fail(f"JSON must contain only finite numbers, got {value}")


def _load_json_mapping(path: Path, label: str) -> dict[str, Any]:
    path = Path(path)
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonfinite_constant,
        )
    except json.JSONDecodeError as exc:
        _fail(f"{label} is not valid JSON: {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object: {path}")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        _fail(f"{label} must be finite")
    return number


def _exact_segment(value: Any, expected: tuple[str, str], label: str) -> None:
    if not isinstance(value, (list, tuple)) or tuple(map(str, value)) != expected:
        _fail(f"{label} segment drift: expected {list(expected)!r}, got {value!r}")


def _normalize_seed(value: Any, label: str) -> int:
    if isinstance(value, bool):
        _fail(f"{label} seed is invalid: {value!r}")
    try:
        seed = int(value)
    except (TypeError, ValueError):
        _fail(f"{label} seed is invalid: {value!r}")
    if str(value) not in {str(seed), f"{seed}.0"}:
        _fail(f"{label} seed is invalid: {value!r}")
    return seed


def _validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if manifest.get("schema_version") != 1:
        _fail("manifest schema_version drift")
    if manifest.get("frozen_before_training") is not True:
        _fail("manifest must be frozen before training")
    if manifest.get("protocol_id") != PROTOCOL_ID:
        _fail(f"manifest protocol drift: expected {PROTOCOL_ID!r}")
    if manifest.get("seeds") != list(SEEDS):
        _fail(f"manifest seeds drift: expected {list(SEEDS)!r}")
    if manifest.get("test_pools") != list(POOLS):
        _fail(f"manifest test pool drift: expected {list(POOLS)!r}")
    if manifest.get("eval_label") != EVAL_LABEL:
        _fail("manifest fixed next-day label drift")
    _exact_segment(
        manifest.get("common_test_segment"),
        TEST_SEGMENT,
        "manifest common test",
    )
    data_version = manifest.get("data_version_at_freeze")
    if not isinstance(data_version, str) or not data_version:
        _fail("manifest data_version_at_freeze is missing")

    groups = manifest.get("groups")
    if not isinstance(groups, dict) or set(groups) != {
        "rankic-winner-stale",
        "rankic-winner-post2020",
    }:
        _fail("manifest groups drift")
    by_role: dict[str, str] = {}
    for group, spec in groups.items():
        if not isinstance(spec, dict) or spec.get("role") not in {
            CONTROL_ROLE,
            EXPANDED_ROLE,
        }:
            _fail(f"manifest group role drift: {group}")
        role = str(spec["role"])
        if role in by_role:
            _fail(f"manifest group role duplicated: {role}")
        by_role[role] = group
    if set(by_role) != {CONTROL_ROLE, EXPANDED_ROLE}:
        _fail("manifest control/treatment roles are incomplete")

    policy = manifest.get("conclusion_policy")
    if (
        not isinstance(policy, dict)
        or set(policy) != {"improve", "regress", "inconclusive"}
        or any(
            not isinstance(value, str) or not value.strip() for value in policy.values()
        )
    ):
        _fail("manifest conclusion policy drift")
    return {
        "data_version": data_version,
        "groups_by_role": by_role,
        "policy": dict(policy),
    }


def _resolve_config_path(raw: Any, result_path: Path) -> Optional[Path]:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        _fail(f"result config path is invalid: {raw!r}")
    path = Path(raw).expanduser()
    candidates = (
        [path] if path.is_absolute() else [Path.cwd() / path, result_path.parent / path]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    _fail(f"result config cannot be loaded: {raw}")


def _validate_config_protocol(
    *,
    config_path: Path,
    expected_group: str,
    manifest: Mapping[str, Any],
) -> None:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        _fail(f"result config cannot be loaded: {config_path}: {exc}")
    if not isinstance(config, dict):
        _fail(f"result config must be a mapping: {config_path}")
    model = config.get("model")
    kwargs = model.get("kwargs") if isinstance(model, dict) else None
    if not isinstance(kwargs, dict) or kwargs.get("protocol_id") != PROTOCOL_ID:
        _fail(f"result config protocol drift: {config_path}")
    if config_path.parent.name != expected_group:
        _fail(
            f"result config group drift: expected {expected_group!r}, "
            f"got {config_path.parent.name!r}"
        )
    seed = _normalize_seed(kwargs.get("seed"), f"config {config_path}")
    if seed not in SEEDS:
        _fail(f"result config seed drift: {seed}")
    hashes = manifest.get("config_hashes")
    if hashes is not None:
        try:
            expected_hash = hashes[expected_group][str(seed)]
        except (KeyError, TypeError):
            _fail(f"manifest config hash missing for {expected_group} seed {seed}")
        actual_hash = _sha256(config_path)
        if actual_hash != expected_hash:
            _fail(
                f"result config hash drift for {expected_group} seed {seed}: "
                f"{actual_hash} != {expected_hash}"
            )


def _validate_result_identity(
    document: Mapping[str, Any],
    *,
    source_path: Path,
    expected_group: str,
    manifest: Mapping[str, Any],
) -> None:
    declared_protocol = document.get("protocol_id")
    declared_group = document.get("experiment_group")
    config_path = _resolve_config_path(document.get("config"), source_path)
    if config_path is not None:
        _validate_config_protocol(
            config_path=config_path,
            expected_group=expected_group,
            manifest=manifest,
        )
        if declared_protocol is not None and declared_protocol != PROTOCOL_ID:
            _fail(f"result protocol drift: {source_path}")
        if declared_group is not None and declared_group != expected_group:
            _fail(f"result experiment group drift: {source_path}")
        return
    if declared_protocol != PROTOCOL_ID:
        _fail(f"result protocol is missing or drifted: {source_path}")
    if declared_group != expected_group:
        _fail(f"result experiment group is missing or drifted: {source_path}")


def _validate_sessions(document: Mapping[str, Any], label: str) -> None:
    sessions = document.get("sessions")
    if not isinstance(sessions, list) or len(sessions) != len(SEEDS):
        _fail(f"{label} sessions must contain exactly five fixed seeds")
    observed = []
    for index, row in enumerate(sessions):
        if not isinstance(row, dict):
            _fail(f"{label} session {index} must be an object")
        observed.append(_normalize_seed(row.get("seed"), f"{label} session {index}"))
    if len(set(observed)) != len(SEEDS) or set(observed) != set(SEEDS):
        _fail(
            f"{label} session seed set drift: expected {list(SEEDS)!r}, "
            f"got {observed!r}"
        )


def _validate_pool(
    pool_document: Any,
    *,
    result_label: str,
    pool: str,
) -> dict[str, Any]:
    label = f"{result_label}.{pool}"
    if not isinstance(pool_document, dict):
        _fail(f"{label} must be an object")
    seeds = pool_document.get("seeds")
    expected_seed_keys = {str(seed) for seed in SEEDS}
    if not isinstance(seeds, dict) or set(seeds) != expected_seed_keys:
        _fail(f"{label} seed set drift")

    normalized_seed_metrics: dict[str, dict[str, float]] = {}
    normalized_yearly: dict[str, dict[str, float]] = {}
    canonical_years: Optional[set[str]] = None
    for seed in SEEDS:
        seed_key = str(seed)
        row = seeds[seed_key]
        if not isinstance(row, dict):
            _fail(f"{label}.seeds.{seed_key} must be an object")
        n_days = _finite_number(row.get("n_days"), f"{label}.{seed_key}.n_days")
        if n_days <= 0:
            _fail(f"{label}.{seed_key}.n_days must be positive")
        normalized_seed_metrics[seed_key] = {
            metric: _finite_number(row.get(metric), f"{label}.{seed_key}.{metric}")
            for metric in METRICS
        }

        yearly = row.get("yearly")
        if not isinstance(yearly, dict) or not yearly:
            _fail(f"{label}.{seed_key}.yearly is missing")
        years = set(yearly)
        if canonical_years is None:
            canonical_years = years
        elif years != canonical_years:
            _fail(f"{label} yearly coverage differs between seeds")
        normalized_yearly[seed_key] = {}
        for year, summary in yearly.items():
            if (
                not isinstance(year, str)
                or not year.isdigit()
                or not isinstance(summary, dict)
            ):
                _fail(f"{label}.{seed_key}.yearly contains an invalid year")
            normalized_yearly[seed_key][year] = _finite_number(
                summary.get("rank_ic_mean"),
                f"{label}.{seed_key}.yearly.{year}.rank_ic_mean",
            )

    expected_years = {str(year) for year in range(2024, 2027)}
    if canonical_years != expected_years:
        _fail(
            f"{label} year set drift: expected {sorted(expected_years)!r}, "
            f"got {sorted(canonical_years or set())!r}"
        )

    stored_mean = pool_document.get("seed_mean")
    if not isinstance(stored_mean, dict):
        _fail(f"{label} seed_mean is missing")
    normalized_mean = {
        metric: _finite_number(stored_mean.get(metric), f"{label}.seed_mean.{metric}")
        for metric in METRICS
    }
    for metric in METRICS:
        recomputed = math.fsum(
            normalized_seed_metrics[str(seed)][metric] for seed in SEEDS
        ) / len(SEEDS)
        if not math.isclose(
            normalized_mean[metric], recomputed, rel_tol=1e-12, abs_tol=1e-12
        ):
            _fail(
                f"{label} seed_mean mismatch for {metric}: "
                f"stored {normalized_mean[metric]}, recomputed {recomputed}"
            )

    years = sorted(canonical_years or set())
    yearly_seed_mean = {
        year: math.fsum(normalized_yearly[str(seed)][year] for seed in SEEDS)
        / len(SEEDS)
        for year in years
    }
    return {
        "seed_mean": normalized_mean,
        "seed_metrics": normalized_seed_metrics,
        "yearly_seed_mean": yearly_seed_mean,
        "years": years,
    }


def _validate_result(
    document: Mapping[str, Any],
    *,
    source_path: Path,
    result_label: str,
    expected_group: str,
    manifest: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_result_identity(
        document,
        source_path=source_path,
        expected_group=expected_group,
        manifest=manifest,
    )
    if document.get("eval_segment_name") != "test":
        _fail(f"{result_label} must evaluate the test segment")
    for key in ("eval_segment", "effective_eval_segment", "test_segment"):
        _exact_segment(document.get(key), TEST_SEGMENT, f"{result_label} {key}")
    if document.get("eval_label_role") != "fixed_1d":
        _fail(f"{result_label} label role must be fixed_1d")
    if document.get("eval_label") != EVAL_LABEL:
        _fail(f"{result_label} fixed next-day label drift")
    if document.get("data_version") != contract["data_version"]:
        _fail(
            f"{result_label} data_version drift: expected "
            f"{contract['data_version']!r}, got {document.get('data_version')!r}"
        )
    _validate_sessions(document, result_label)
    pools = document.get("pools")
    if not isinstance(pools, dict) or set(pools) != set(POOLS):
        _fail(f"{result_label} pool set drift: expected {list(POOLS)!r}")
    normalized_pools = {
        pool: _validate_pool(
            pools[pool],
            result_label=result_label,
            pool=pool,
        )
        for pool in POOLS
    }
    year_sets = {tuple(value["years"]) for value in normalized_pools.values()}
    if len(year_sets) != 1:
        _fail(f"{result_label} yearly coverage differs between pools")
    return {
        "group": expected_group,
        "pools": normalized_pools,
        "years": list(next(iter(year_sets))),
    }


def _source(path: Path) -> dict[str, str]:
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _sha256(path)}


def _write_json_exclusive_atomic(path: Path, payload: Mapping[str, Any]) -> None:
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
        ) as handle:
            temp_name = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_name, path)
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite: {path}") from None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def compare_results(
    *,
    manifest_path: Path,
    control_path: Path,
    expanded_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Validate both frozen test artifacts, compare them, and publish one JSON."""

    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite: {output_path}")

    manifest_path = Path(manifest_path)
    control_path = Path(control_path)
    expanded_path = Path(expanded_path)
    manifest = _load_json_mapping(manifest_path, "manifest")
    control_document = _load_json_mapping(control_path, "control result")
    expanded_document = _load_json_mapping(expanded_path, "expanded result")
    contract = _validate_manifest(manifest)
    control = _validate_result(
        control_document,
        source_path=control_path,
        result_label="control",
        expected_group=contract["groups_by_role"][CONTROL_ROLE],
        manifest=manifest,
        contract=contract,
    )
    expanded = _validate_result(
        expanded_document,
        source_path=expanded_path,
        result_label="expanded",
        expected_group=contract["groups_by_role"][EXPANDED_ROLE],
        manifest=manifest,
        contract=contract,
    )
    if control["years"] != expanded["years"]:
        _fail("control and expanded yearly coverage differs")

    seed_mean = {
        "control": {pool: control["pools"][pool]["seed_mean"] for pool in POOLS},
        "expanded": {pool: expanded["pools"][pool]["seed_mean"] for pool in POOLS},
    }
    metric_deltas = {
        pool: {
            metric: (
                seed_mean["expanded"][pool][metric] - seed_mean["control"][pool][metric]
            )
            for metric in METRICS
        }
        for pool in POOLS
    }
    pairwise_diffs = [
        (
            expanded["pools"]["csi1000"]["seed_metrics"][str(seed)]["rank_ic_mean"]
            - control["pools"]["csi1000"]["seed_metrics"][str(seed)]["rank_ic_mean"]
        )
        for seed in SEEDS
    ]
    same_seed = {
        "n": len(SEEDS),
        "wins": sum(delta > 0 for delta in pairwise_diffs),
        "diff_mean": math.fsum(pairwise_diffs) / len(pairwise_diffs),
        "seeds": list(SEEDS),
        "diffs": pairwise_diffs,
    }
    yearly_delta = {
        year: {
            pool: (
                expanded["pools"][pool]["yearly_seed_mean"][year]
                - control["pools"][pool]["yearly_seed_mean"][year]
            )
            for pool in POOLS
        }
        for year in control["years"]
    }

    rank_ic_delta = {pool: metric_deltas[pool]["rank_ic_mean"] for pool in POOLS}
    if all(rank_ic_delta[pool] > 0 for pool in POOLS):
        conclusion = "improve"
    elif rank_ic_delta["csi1000"] <= 0:
        conclusion = "regress"
    else:
        conclusion = "inconclusive"

    output: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol_id": PROTOCOL_ID,
        "groups": {
            "control": control["group"],
            "expanded": expanded["group"],
        },
        "common_test_segment": list(TEST_SEGMENT),
        "eval_label": EVAL_LABEL,
        "eval_label_role": "fixed_1d",
        "data_version": contract["data_version"],
        "seeds": list(SEEDS),
        "test_pools": list(POOLS),
        "sources": {
            "manifest": _source(manifest_path),
            "control": _source(control_path),
            "expanded": _source(expanded_path),
        },
        "seed_mean": seed_mean,
        "metric_deltas": metric_deltas,
        "csi1000_same_seed_rankic": same_seed,
        "yearly_rankic_delta": yearly_delta,
        "conclusion_policy": contract["policy"],
        "conclusion": conclusion,
    }
    _write_json_exclusive_atomic(output_path, output)
    return output


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare frozen stale and post-2020 B5 forward-test IC artifacts"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--expanded", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    result = compare_results(
        manifest_path=args.manifest,
        control_path=args.control,
        expanded_path=args.expanded,
        output_path=args.output,
    )
    print(f"written: {args.output} ({result['conclusion']})")


if __name__ == "__main__":
    main()
