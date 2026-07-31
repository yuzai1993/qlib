"""Register the frozen paired B5 post-2020 forward experiment."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

QLIB_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QLIB_ROOT))

from backtest.scripts import build_experiment_report  # noqa: E402


DEFAULT_REGISTRY = BACKTEST_ROOT / "experiments" / "registry.jsonl"
DEFAULT_REPORT = BACKTEST_ROOT / "experiments" / "report.html"

PROTOCOL_ID = "post2020-forward-v1"
SEEDS = (42, 1000, 2000, 3000, 4000)
POOLS = ("csi1000", "csi300", "csi500")
METRICS = ("ic_mean", "icir", "rank_ic_mean", "rank_icir")
YEARS = ("2024", "2025", "2026")
EVAL_LABEL = "Ref($close, -2)/Ref($close, -1) - 1"
VALID_SEGMENT = ("2023-01-03", "2024-06-28")
EFFECTIVE_VALID_SEGMENT = ("2023-01-03", "2024-06-26")
TEST_SEGMENT = ("2024-07-01", "2026-07-16")
BASELINE_REF = "B5 v1.0"
CONTROL_GROUP = "rankic-winner-stale"
EXPANDED_GROUP = "rankic-winner-post2020"
CONTROL_EXP_ID = f"train-recency/{CONTROL_GROUP}"
EXPANDED_EXP_ID = f"train-recency/{EXPANDED_GROUP}"
CONTROL_HYPOTHESIS = (
    "固定 rankic-es-lr010 winner 的全部模型、特征、标签和早停超参，"
    "训练截止保持在 2020-01-10 不变；作为同一 forward holdout 的 stale-window "
    "control，用于衡量不加入 post-2020 训练样本时的 RankIC。"
)
EXPANDED_HYPOTHESIS = (
    "固定 rankic-es-lr010 winner 的全部模型、特征、标签和早停超参，只把训练"
    "截止从 2020-01-10 延长到 2022-12-30；更多且更近期的训练样本若能缓解分布"
    "漂移，应使共同 forward test 上三池固定次日 RankIC 同时严格提高。"
)
GROUP_HYPOTHESES = {
    CONTROL_GROUP: CONTROL_HYPOTHESIS,
    EXPANDED_GROUP: EXPANDED_HYPOTHESIS,
}
GROUP_CONTRACTS = {
    CONTROL_GROUP: {
        "role": "same-window-control",
        "train_segment": ("2016-01-02", "2020-01-10"),
        "effective_h40_train_end": "2019-11-13",
    },
    EXPANDED_GROUP: {
        "role": "treatment",
        "train_segment": ("2016-01-02", "2022-12-30"),
        "effective_h40_train_end": "2022-11-03",
    },
}


def _fail(message: str) -> None:
    raise ValueError(message)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _reject_nonfinite(value: str) -> None:
    _fail(f"JSON must contain finite numbers, got {value}")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    path = Path(path)
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonfinite,
        )
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"{label} cannot be loaded: {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object: {path}")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} must be finite")
    number = float(value)
    if not math.isfinite(number):
        _fail(f"{label} must be finite")
    return number


def _sample_std(values: Sequence[float]) -> float:
    if len(values) != len(SEEDS):
        _fail("RankIC sample standard deviation requires five seeds")
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _same_number(left: Any, right: Any, label: str) -> None:
    actual = _finite(left, label)
    expected = _finite(right, f"expected {label}")
    if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
        _fail(f"{label} drift: expected {expected}, got {actual}")


def _exact_segment(value: Any, expected: Sequence[str], label: str) -> None:
    if not isinstance(value, (list, tuple)) or tuple(map(str, value)) != tuple(
        expected
    ):
        _fail(f"{label} segment drift: expected {list(expected)!r}, got {value!r}")


def _normalize_seed(value: Any, label: str) -> int:
    if isinstance(value, bool):
        _fail(f"{label} seed is invalid")
    try:
        seed = int(value)
    except (TypeError, ValueError):
        _fail(f"{label} seed is invalid")
    if str(value) not in {str(seed), f"{seed}.0"}:
        _fail(f"{label} seed is invalid")
    return seed


def _relative(path: Path, repo_root: Path, label: str) -> str:
    path = Path(path).expanduser().resolve()
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        _fail(f"{label} must be inside repository: {path}")


def _resolve_eval_session(raw: Any, *, repo_root: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        _fail(f"{label} eval session path is invalid: {raw!r}")
    result_root = (repo_root / "backtest" / "result").resolve()
    path = Path(raw).expanduser()
    if path.is_absolute():
        candidate = path
    elif len(path.parts) == 1:
        candidate = result_root / path
    else:
        candidate = repo_root / path
    if candidate.parent != result_root or candidate.is_symlink():
        _fail(f"{label} eval session path is unknown or outside result root: {raw}")
    resolved = candidate.resolve()
    if resolved != candidate or resolved.parent != result_root or not resolved.is_dir():
        _fail(f"{label} eval session path is unknown or outside result root: {raw}")
    return resolved


def _parse_frozen_date(value: Any) -> str:
    if not isinstance(value, str):
        _fail("manifest frozen_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail("manifest frozen_at must be an ISO timestamp")
    return parsed.date().isoformat()


def _expected_config_path(repo_root: Path, group: str, seed: int) -> Path:
    return (
        repo_root
        / "backtest"
        / "configs"
        / "train-recency"
        / group
        / f"tr_{group.replace('-', '_')}_s{seed}.yaml"
    ).resolve()


def _validate_manifest(
    manifest: Mapping[str, Any],
    *,
    repo_root: Path,
) -> dict[str, Any]:
    if manifest.get("schema_version") != 1:
        _fail("manifest schema_version drift")
    if manifest.get("protocol_id") != PROTOCOL_ID:
        _fail("manifest protocol drift")
    if manifest.get("frozen_before_training") is not True:
        _fail("manifest must be frozen before training")
    if manifest.get("evaluation_comparable_to_baseline") is not False:
        _fail("manifest evaluation_comparable_to_baseline must be false")
    if manifest.get("cleanup_retention_eligible") is not False:
        _fail("manifest cleanup_retention_eligible must be false")
    if manifest.get("seeds") != list(SEEDS):
        _fail("manifest must contain the fixed five seeds")
    if manifest.get("train_pool") != "csi1000":
        _fail("manifest train pool drift")
    if manifest.get("test_pools") != list(POOLS):
        _fail("manifest three test pools drift")
    if manifest.get("eval_label") != EVAL_LABEL or manifest.get("eval_min_count") != 20:
        _fail("manifest fixed_1d evaluation protocol drift")
    if manifest.get("early_stopping_metric") != "fixed_next_day_valid_daily_rank_ic":
        _fail("manifest early stopping metric drift")
    if manifest.get("early_stopping_rounds") != 20:
        _fail("manifest early stopping rounds drift")
    _exact_segment(
        manifest.get("common_valid_segment"), VALID_SEGMENT, "manifest valid"
    )
    _exact_segment(
        manifest.get("effective_h1_valid_segment"),
        EFFECTIVE_VALID_SEGMENT,
        "manifest effective valid",
    )
    _exact_segment(manifest.get("common_test_segment"), TEST_SEGMENT, "manifest test")

    groups = manifest.get("groups")
    if not isinstance(groups, dict) or set(groups) != set(GROUP_CONTRACTS):
        _fail("manifest group matrix drift")
    for group, expected in GROUP_CONTRACTS.items():
        value = groups[group]
        if not isinstance(value, dict) or value.get("role") != expected["role"]:
            _fail(f"manifest group role drift: {group}")
        _exact_segment(
            value.get("train_segment"),
            expected["train_segment"],
            f"manifest {group} train",
        )
        if value.get("effective_h40_train_end") != expected["effective_h40_train_end"]:
            _fail(f"manifest {group} effective H40 train end drift")

    policy = manifest.get("conclusion_policy")
    if (
        not isinstance(policy, dict)
        or set(policy) != {"improve", "regress", "inconclusive"}
        or any(
            not isinstance(value, str) or not value.strip() for value in policy.values()
        )
    ):
        _fail("manifest conclusion policy drift")
    hypothesis = manifest.get("hypothesis")
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        _fail("manifest hypothesis is missing")
    data_version = manifest.get("data_version_at_freeze")
    if not isinstance(data_version, str) or not data_version:
        _fail("manifest data_version_at_freeze is missing")

    config_hashes = manifest.get("config_hashes")
    if not isinstance(config_hashes, dict) or set(config_hashes) != set(
        GROUP_CONTRACTS
    ):
        _fail("manifest config hash groups drift")
    configs: dict[str, dict[int, Path]] = {}
    normalized_hashes: dict[str, dict[str, str]] = {}
    for group in GROUP_CONTRACTS:
        by_seed = config_hashes[group]
        if not isinstance(by_seed, dict) or set(by_seed) != {
            str(seed) for seed in SEEDS
        }:
            _fail(f"manifest {group} config hash matrix must contain five seeds")
        configs[group] = {}
        normalized_hashes[group] = {}
        for seed in SEEDS:
            path = _expected_config_path(repo_root, group, seed)
            if not path.is_file():
                _fail(f"frozen config missing: {path}")
            actual = _sha256(path)
            expected = by_seed[str(seed)]
            if actual != expected:
                _fail(f"frozen config hash drift: {group} seed {seed}")
            configs[group][seed] = path
            normalized_hashes[group][str(seed)] = actual
    return {
        "date": _parse_frozen_date(manifest.get("frozen_at")),
        "data_version": data_version,
        "hypothesis": hypothesis,
        "policy": copy.deepcopy(policy),
        "configs": configs,
        "config_hashes": normalized_hashes,
    }


def _validate_source_chain(
    comparison: Mapping[str, Any],
    *,
    manifest_path: Path,
    control_path: Path,
    expanded_path: Path,
) -> None:
    sources = comparison.get("sources")
    expected_paths = {
        "manifest": manifest_path.resolve(),
        "control": control_path.resolve(),
        "expanded": expanded_path.resolve(),
    }
    if not isinstance(sources, dict) or set(sources) != set(expected_paths):
        _fail("comparison must contain exactly three source SHA-256 records")
    for key, expected_path in expected_paths.items():
        record = sources[key]
        if not isinstance(record, dict):
            _fail(f"comparison {key} source SHA-256 record is invalid")
        try:
            recorded_path = Path(str(record.get("path"))).expanduser().resolve()
        except (TypeError, ValueError):
            _fail(f"comparison {key} source path is invalid")
        if recorded_path != expected_path:
            _fail(f"comparison {key} source path drift")
        actual = _sha256(expected_path)
        if record.get("sha256") != actual:
            _fail(f"comparison {key} source SHA-256 drift")


def _validate_eval_identity(
    result: Mapping[str, Any],
    *,
    group: str,
    contract: Mapping[str, Any],
) -> None:
    raw_config = result.get("config")
    if not isinstance(raw_config, str):
        _fail(f"{group} eval config path is missing")
    config_path = Path(raw_config).expanduser().resolve()
    expected_configs = set(contract["configs"][group].values())
    if config_path not in expected_configs:
        _fail(f"{group} eval config path is not frozen")
    if result.get("protocol_id") not in (None, PROTOCOL_ID):
        _fail(f"{group} eval protocol drift")
    if result.get("experiment_group") not in (None, group):
        _fail(f"{group} eval group drift")


def _validate_eval(
    result: Mapping[str, Any],
    *,
    group: str,
    contract: Mapping[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    _validate_eval_identity(result, group=group, contract=contract)
    if result.get("eval_segment_name") != "test":
        _fail(f"{group} eval must use test segment")
    for key, label in (
        ("eval_segment", "official test"),
        ("effective_eval_segment", "effective test"),
        ("test_segment", "test"),
    ):
        _exact_segment(result.get(key), TEST_SEGMENT, f"{group} {label}")
    if (
        result.get("eval_label_role") != "fixed_1d"
        or result.get("eval_label") != EVAL_LABEL
    ):
        _fail(f"{group} eval must use fixed_1d label")
    if result.get("data_version") != contract["data_version"]:
        _fail(f"{group} eval data_version drift")

    sessions = result.get("sessions")
    if not isinstance(sessions, list) or len(sessions) != len(SEEDS):
        _fail(f"{group} eval must contain exact five seeds")
    session_map: dict[int, Path] = {}
    for index, row in enumerate(sessions):
        if not isinstance(row, dict):
            _fail(f"{group} eval session {index} is invalid")
        seed = _normalize_seed(row.get("seed"), f"{group} eval session {index}")
        raw_session = row.get("session")
        if seed in session_map or not isinstance(raw_session, str):
            _fail(f"{group} eval must contain unique five seeds")
        session_map[seed] = _resolve_eval_session(
            raw_session,
            repo_root=repo_root,
            label=f"{group} seed {seed}",
        )
    if set(session_map) != set(SEEDS):
        _fail(f"{group} eval must contain exact five seeds")

    pools = result.get("pools")
    if not isinstance(pools, dict) or set(pools) != set(POOLS):
        _fail(f"{group} eval must contain exact three pools")
    seed_mean: dict[str, dict[str, float]] = {}
    seed_rankic: dict[int, float] = {}
    yearly: dict[str, dict[str, float]] = {year: {} for year in YEARS}
    for pool in POOLS:
        payload = pools[pool]
        if not isinstance(payload, dict):
            _fail(f"{group}/{pool} eval payload is invalid")
        seeds = payload.get("seeds")
        if not isinstance(seeds, dict) or set(seeds) != {str(seed) for seed in SEEDS}:
            _fail(f"{group}/{pool} eval must contain exact five seeds")
        metric_values = {metric: [] for metric in METRICS}
        yearly_values = {year: [] for year in YEARS}
        for seed in SEEDS:
            row = seeds[str(seed)]
            if (
                not isinstance(row, dict)
                or _finite(row.get("n_days"), f"{group}/{pool}/{seed} n_days") <= 0
            ):
                _fail(f"{group}/{pool}/{seed} seed metrics are invalid")
            for metric in METRICS:
                metric_values[metric].append(
                    _finite(row.get(metric), f"{group}/{pool}/{seed} {metric}")
                )
            if pool == "csi1000":
                seed_rankic[seed] = metric_values["rank_ic_mean"][-1]
            by_year = row.get("yearly")
            if not isinstance(by_year, dict) or set(by_year) != set(YEARS):
                _fail(f"{group}/{pool}/{seed} yearly coverage drift")
            for year in YEARS:
                summary = by_year[year]
                if not isinstance(summary, dict):
                    _fail(f"{group}/{pool}/{seed}/{year} yearly summary invalid")
                yearly_values[year].append(
                    _finite(
                        summary.get("rank_ic_mean"),
                        f"{group}/{pool}/{seed}/{year} rank_ic_mean",
                    )
                )
        stored = payload.get("seed_mean")
        if not isinstance(stored, dict):
            _fail(f"{group}/{pool} seed_mean is missing")
        seed_mean[pool] = {}
        for metric, values in metric_values.items():
            expected = math.fsum(values) / len(values)
            actual = _finite(stored.get(metric), f"{group}/{pool} seed_mean {metric}")
            if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
                _fail(f"{group}/{pool} seed_mean drift for {metric}")
            seed_mean[pool][metric] = actual
        expected_rank_ic_mean_std = _sample_std(metric_values["rank_ic_mean"])
        actual_rank_ic_mean_std = _finite(
            stored.get("rank_ic_mean_std"),
            f"{group}/{pool} seed_mean rank_ic_mean_std",
        )
        if not math.isclose(
            actual_rank_ic_mean_std,
            expected_rank_ic_mean_std,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            _fail(f"{group}/{pool} seed_mean drift for rank_ic_mean_std")
        seed_mean[pool]["rank_ic_mean_std"] = actual_rank_ic_mean_std
        for year in YEARS:
            yearly[year][pool] = math.fsum(yearly_values[year]) / len(SEEDS)
    return {
        "session_map": session_map,
        "seed_mean": seed_mean,
        "seed_rankic": seed_rankic,
        "yearly": yearly,
    }


def _validate_seed_mean_block(
    value: Any,
    expected: Mapping[str, Mapping[str, float]],
    label: str,
) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict) or set(value) != set(POOLS):
        _fail(f"comparison {label} must contain exact three pools")
    output = {}
    for pool in POOLS:
        stored = value[pool]
        if not isinstance(stored, dict) or not set(METRICS).issubset(stored):
            _fail(f"comparison {label}/{pool} metrics are incomplete")
        output[pool] = {}
        for metric in METRICS:
            try:
                _same_number(
                    stored[metric],
                    expected[pool][metric],
                    f"comparison {label} {pool}/{metric}",
                )
            except ValueError:
                _fail(f"comparison {label} drift: {pool}/{metric}")
            output[pool][metric] = float(stored[metric])
    return output


def _validate_comparison(
    comparison: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    contract: Mapping[str, Any],
    control: Mapping[str, Any],
    expanded: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        comparison.get("schema_version") != 1
        or comparison.get("protocol_id") != PROTOCOL_ID
    ):
        _fail("comparison protocol drift")
    if comparison.get("groups") != {
        "control": CONTROL_GROUP,
        "expanded": EXPANDED_GROUP,
    }:
        _fail("comparison group drift")
    _exact_segment(
        comparison.get("common_test_segment"), TEST_SEGMENT, "comparison test"
    )
    if (
        comparison.get("eval_label") != EVAL_LABEL
        or comparison.get("eval_label_role") != "fixed_1d"
    ):
        _fail("comparison fixed_1d label drift")
    if comparison.get("data_version") != contract["data_version"]:
        _fail("comparison data_version drift")
    if comparison.get("seeds") != list(SEEDS):
        _fail("comparison must contain fixed five seeds")
    if comparison.get("test_pools") != list(POOLS):
        _fail("comparison three pool order drift")
    if comparison.get("conclusion_policy") != manifest.get("conclusion_policy"):
        _fail("comparison conclusion policy drift")

    seed_mean_block = comparison.get("seed_mean")
    if not isinstance(seed_mean_block, dict) or set(seed_mean_block) != {
        "control",
        "expanded",
    }:
        _fail("comparison seed_mean groups drift")
    control_mean = _validate_seed_mean_block(
        seed_mean_block["control"], control["seed_mean"], "control seed_mean"
    )
    expanded_mean = _validate_seed_mean_block(
        seed_mean_block["expanded"], expanded["seed_mean"], "expanded seed_mean"
    )

    deltas = comparison.get("metric_deltas")
    if not isinstance(deltas, dict) or set(deltas) != set(POOLS):
        _fail("comparison metric_deltas pool drift")
    normalized_deltas = {}
    for pool in POOLS:
        if not isinstance(deltas[pool], dict) or set(deltas[pool]) != set(METRICS):
            _fail(f"comparison metric_deltas drift: {pool}")
        normalized_deltas[pool] = {}
        for metric in METRICS:
            expected = expanded_mean[pool][metric] - control_mean[pool][metric]
            _same_number(
                deltas[pool].get(metric),
                expected,
                f"comparison metric_deltas {pool}/{metric}",
            )
            normalized_deltas[pool][metric] = float(deltas[pool][metric])

    expected_diffs = [
        expanded["seed_rankic"][seed] - control["seed_rankic"][seed] for seed in SEEDS
    ]
    pairwise = comparison.get("csi1000_same_seed_rankic")
    if (
        not isinstance(pairwise, dict)
        or pairwise.get("n") != len(SEEDS)
        or pairwise.get("seeds") != list(SEEDS)
        or pairwise.get("wins") != sum(value > 0 for value in expected_diffs)
        or not isinstance(pairwise.get("diffs"), list)
        or len(pairwise["diffs"]) != len(SEEDS)
    ):
        _fail("comparison pairwise CSI1000 RankIC drift")
    for index, expected in enumerate(expected_diffs):
        _same_number(
            pairwise["diffs"][index],
            expected,
            f"comparison pairwise diff {SEEDS[index]}",
        )
    _same_number(
        pairwise.get("diff_mean"),
        math.fsum(expected_diffs) / len(SEEDS),
        "comparison pairwise diff_mean",
    )

    yearly = comparison.get("yearly_rankic_delta")
    if not isinstance(yearly, dict) or set(yearly) != set(YEARS):
        _fail("comparison yearly RankIC delta coverage drift")
    normalized_yearly = {}
    for year in YEARS:
        if not isinstance(yearly[year], dict) or set(yearly[year]) != set(POOLS):
            _fail(f"comparison yearly RankIC delta pool drift: {year}")
        normalized_yearly[year] = {}
        for pool in POOLS:
            expected = expanded["yearly"][year][pool] - control["yearly"][year][pool]
            _same_number(
                yearly[year][pool],
                expected,
                f"comparison yearly RankIC delta {year}/{pool}",
            )
            normalized_yearly[year][pool] = float(yearly[year][pool])

    rankic_delta = {pool: normalized_deltas[pool]["rank_ic_mean"] for pool in POOLS}
    if all(rankic_delta[pool] > 0 for pool in POOLS):
        conclusion = "improve"
    elif rankic_delta["csi1000"] <= 0:
        conclusion = "regress"
    else:
        conclusion = "inconclusive"
    if comparison.get("conclusion") != conclusion:
        _fail("comparison conclusion drift")
    return {
        "control_mean": {
            pool: {
                **control_mean[pool],
                "rank_ic_mean_std": control["seed_mean"][pool]["rank_ic_mean_std"],
            }
            for pool in POOLS
        },
        "expanded_mean": {
            pool: {
                **expanded_mean[pool],
                "rank_ic_mean_std": expanded["seed_mean"][pool]["rank_ic_mean_std"],
            }
            for pool in POOLS
        },
        "deltas": normalized_deltas,
        "pairwise": copy.deepcopy(pairwise),
        "yearly": normalized_yearly,
        "conclusion": conclusion,
    }


def _validate_sessions(
    paths: Sequence[Path],
    *,
    group: str,
    eval_sessions: Mapping[int, Path],
    contract: Mapping[str, Any],
    repo_root: Path,
) -> list[dict[str, Any]]:
    if len(paths) != len(SEEDS):
        _fail(f"{group} must provide exactly five session directories")
    result_root = (repo_root / "backtest" / "result").resolve()
    expected_by_path = {
        config.resolve(): seed for seed, config in contract["configs"][group].items()
    }
    audits: dict[int, dict[str, Any]] = {}
    seen_paths: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        if path in seen_paths or path.parent != result_root or not path.is_dir():
            _fail(
                f"{group} session must be a unique direct child of result root: {path}"
            )
        seen_paths.add(path)
        meta_path = path / "meta.json"
        meta = _load_json(meta_path, f"{group} session meta")
        raw_config = meta.get("config_path")
        if not isinstance(raw_config, str):
            _fail(f"{group} session config path is missing: {path}")
        config_path = Path(raw_config).expanduser().resolve()
        if config_path not in expected_by_path:
            _fail(f"{group} session config path is not frozen: {path}")
        seed = expected_by_path[config_path]
        expected_note = f"tr_{group.replace('-', '_')}_s{seed}"
        if meta.get("session_name") != path.name or meta.get("note") != expected_note:
            _fail(f"{group} session identity drift: {path}")
        if meta.get("mode") != "train_only" or meta.get("n_runs") != 1:
            _fail(f"{group} session must be train_only with one run: {path}")
        if (
            meta.get("market") != "csi1000"
            or meta.get("handler") != "Alpha158Technical"
        ):
            _fail(f"{group} session training pool/handler drift: {path}")
        if meta.get("generate_figures") is not False:
            _fail(f"{group} session generate_figures drift: {path}")
        segments = meta.get("segments")
        if not isinstance(segments, dict):
            _fail(f"{group} session segments missing: {path}")
        _exact_segment(
            segments.get("train"),
            GROUP_CONTRACTS[group]["train_segment"],
            f"{group} session train",
        )
        _exact_segment(segments.get("valid"), VALID_SEGMENT, f"{group} session valid")
        _exact_segment(segments.get("test"), TEST_SEGMENT, f"{group} session test")
        runs = meta.get("runs")
        if not isinstance(runs, list) or len(runs) != 1:
            _fail(f"{group} session must contain one success run: {path}")
        run = runs[0]
        if (
            not isinstance(run, dict)
            or run.get("run") != 1
            or run.get("status") != "success"
        ):
            _fail(f"{group} session run must be success: {path}")
        if any(
            not run.get(key)
            for key in (
                "train_experiment_name",
                "train_experiment_id",
                "train_recorder_id",
            )
        ) or any(
            run.get(key) is not None
            for key in (
                "backtest_experiment_name",
                "backtest_experiment_id",
                "backtest_recorder_id",
            )
        ):
            _fail(f"{group} session success provenance is incomplete: {path}")
        if seed in audits:
            _fail(f"{group} sessions duplicate seed {seed}")
        audits[seed] = {
            "seed": seed,
            "session": _relative(path, repo_root, f"{group} session"),
            "meta_path": _relative(meta_path, repo_root, f"{group} meta"),
            "meta_sha256": _sha256(meta_path),
            "config": _relative(config_path, repo_root, f"{group} config"),
            "config_sha256": contract["config_hashes"][group][str(seed)],
            "train_experiment_name": str(run["train_experiment_name"]),
            "train_experiment_id": str(run["train_experiment_id"]),
            "train_recorder_id": str(run["train_recorder_id"]),
        }
    if set(audits) != set(SEEDS):
        _fail(f"{group} sessions must cover the fixed five seeds")
    actual_eval = {seed: path.resolve() for seed, path in eval_sessions.items()}
    actual_passed = {
        seed: (repo_root / audits[seed]["session"]).resolve() for seed in SEEDS
    }
    if actual_eval != actual_passed:
        _fail(f"{group} session directories differ from eval five seeds")
    return [audits[seed] for seed in SEEDS]


def _decode_registry(raw: bytes) -> list[dict[str, Any]]:
    rows = []
    for line_number, raw_line in enumerate(raw.splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(
                raw_line.decode("utf-8"), parse_constant=_reject_nonfinite
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail(f"registry line {line_number} is not valid UTF-8 JSON: {exc}")
        if not isinstance(value, dict):
            _fail(f"registry line {line_number} must be a JSON object")
        rows.append(value)
    return rows


def _validate_baseline_anchor(rows: Sequence[Mapping[str, Any]]) -> None:
    anchors = [row for row in rows if row.get("exp_id") == "baseline/b5-m"]
    if len(anchors) != 1:
        _fail("registry must contain exactly one B5 registry anchor")
    anchor = anchors[0]
    if (
        anchor.get("direction") != "baseline"
        or anchor.get("phase") != "M"
        or anchor.get("baseline_ref") != BASELINE_REF
        or anchor.get("conclusion") != "baseline"
    ):
        _fail("B5 registry anchor protocol drift")


def _encode_row(row: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            row,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail(f"registry row is not finite JSON: {exc}")


def _atomic_replace(path: Path, payload: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
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
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def _build_rows(
    *,
    manifest: Mapping[str, Any],
    contract: Mapping[str, Any],
    comparison: Mapping[str, Any],
    comparison_values: Mapping[str, Any],
    manifest_path: Path,
    control_eval_path: Path,
    expanded_eval_path: Path,
    comparison_path: Path,
    control_audits: Sequence[Mapping[str, Any]],
    expanded_audits: Sequence[Mapping[str, Any]],
    repo_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    common_sources = {
        "protocol_manifest": _relative(manifest_path, repo_root, "manifest"),
        "protocol_manifest_sha256": _sha256(manifest_path),
        "comparison": _relative(comparison_path, repo_root, "comparison"),
        "comparison_sha256": _sha256(comparison_path),
        "comparison_sources": {
            key: {
                "path": _relative(
                    Path(comparison["sources"][key]["path"]),
                    repo_root,
                    f"comparison {key} source",
                ),
                "sha256": comparison["sources"][key]["sha256"],
            }
            for key in ("manifest", "control", "expanded")
        },
    }

    def common(group: str, audits: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        config_paths = [audit["config"] for audit in audits]
        session_dirs = [audit["session"] for audit in audits]
        return {
            "exp_id": f"train-recency/{group}",
            "direction": "train-recency",
            "phase": "M",
            "date": contract["date"],
            "hypothesis": GROUP_HYPOTHESES[group],
            "baseline_ref": BASELINE_REF,
            "protocol_id": PROTOCOL_ID,
            "group_role": GROUP_CONTRACTS[group]["role"],
            "seeds": list(SEEDS),
            "train_pool": "csi1000",
            "train_segment": list(GROUP_CONTRACTS[group]["train_segment"]),
            "effective_h40_train_end": GROUP_CONTRACTS[group][
                "effective_h40_train_end"
            ],
            "valid_segment": list(VALID_SEGMENT),
            "effective_h1_valid_segment": list(EFFECTIVE_VALID_SEGMENT),
            "test_segment": list(TEST_SEGMENT),
            "test_pools": list(POOLS),
            "data_version": contract["data_version"],
            "label_kind": "cumulative_return",
            "label_horizon": 40,
            "label": "Ref($close,-41)/Ref($close,-1)-1",
            "purge_trading_days": 41,
            "feature_groups": ["range"],
            "model": "RankICEarlyStoppingDEnsembleModel",
            "training_objective": manifest["training_objective"],
            "early_stopping_metric": manifest["early_stopping_metric"],
            "early_stopping_rounds": manifest["early_stopping_rounds"],
            "eval_label": EVAL_LABEL,
            "eval_label_role": "fixed_1d",
            "eval_min_count": 20,
            "evaluation_comparable_to_baseline": False,
            "cleanup_retention_eligible": False,
            "configs": config_paths,
            "config_hashes": [
                {
                    "seed": audit["seed"],
                    "path": audit["config"],
                    "sha256": audit["config_sha256"],
                }
                for audit in audits
            ],
            "session_dirs": session_dirs,
            "session_audit": [dict(audit) for audit in audits],
            **copy.deepcopy(common_sources),
        }

    control_row = common(CONTROL_GROUP, control_audits)
    control_eval_relative = _relative(control_eval_path, repo_root, "control eval")
    control_row.update(
        {
            "eval_result": control_eval_relative,
            "eval_result_sha256": _sha256(control_eval_path),
            "result_dirs": control_row["session_dirs"]
            + [control_eval_relative, common_sources["comparison"]],
            "metrics_summary": copy.deepcopy(comparison_values["control_mean"]),
            "metrics_by_eval_label": {
                "eval_1d": copy.deepcopy(comparison_values["control_mean"])
            },
            "conclusion": "control",
            "note": (
                "同一 forward holdout 的 stale-window control；仅作为 expanded "
                "直接对照，不与 B5 原测试窗口横向判优。"
            ),
        }
    )

    expanded_row = common(EXPANDED_GROUP, expanded_audits)
    expanded_eval_relative = _relative(expanded_eval_path, repo_root, "expanded eval")
    conclusion = comparison_values["conclusion"]
    expanded_row.update(
        {
            "eval_result": expanded_eval_relative,
            "eval_result_sha256": _sha256(expanded_eval_path),
            "result_dirs": expanded_row["session_dirs"]
            + [expanded_eval_relative, common_sources["comparison"]],
            "metrics_summary": copy.deepcopy(comparison_values["expanded_mean"]),
            "metrics_by_eval_label": {
                "eval_1d": copy.deepcopy(comparison_values["expanded_mean"])
            },
            "direct_control_ref": CONTROL_EXP_ID,
            "metric_deltas": copy.deepcopy(comparison_values["deltas"]),
            "pairwise_csi1000_rankic_vs_control": copy.deepcopy(
                comparison_values["pairwise"]
            ),
            "yearly_rank_ic_delta_vs_control": copy.deepcopy(
                comparison_values["yearly"]
            ),
            "conclusion_policy": copy.deepcopy(contract["policy"]),
            "conclusion": conclusion,
            "note": (
                f"预登记 forward 成对结论={conclusion}；只对照 "
                f"{CONTROL_EXP_ID}，不得与 B5 原测试窗口直接判优。"
            ),
        }
    )
    return control_row, expanded_row


def register_results(
    *,
    manifest_path: Path,
    control_eval_path: Path,
    expanded_eval_path: Path,
    comparison_path: Path,
    control_sessions: Sequence[Path],
    expanded_sessions: Sequence[Path],
    registry_path: Path,
    report_path: Optional[Path] = None,
    repo_root: Path = QLIB_ROOT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the frozen chain and atomically append control then treatment."""

    repo_root = Path(repo_root).expanduser().resolve()
    registry_path = Path(registry_path)
    raw_registry = registry_path.read_bytes() if registry_path.exists() else b""
    existing_rows = _decode_registry(raw_registry)
    existing_ids = {row.get("exp_id") for row in existing_rows}
    duplicates = [
        exp_id for exp_id in (CONTROL_EXP_ID, EXPANDED_EXP_ID) if exp_id in existing_ids
    ]
    if duplicates:
        _fail(f"experiment already exists: {', '.join(duplicates)}")
    _validate_baseline_anchor(existing_rows)
    if (
        report_path is not None
        and Path(report_path).resolve() == registry_path.resolve()
    ):
        _fail("report output must differ from registry")

    manifest_path = Path(manifest_path)
    control_eval_path = Path(control_eval_path)
    expanded_eval_path = Path(expanded_eval_path)
    comparison_path = Path(comparison_path)
    manifest = _load_json(manifest_path, "forward manifest")
    control_document = _load_json(control_eval_path, "control eval")
    expanded_document = _load_json(expanded_eval_path, "expanded eval")
    comparison = _load_json(comparison_path, "combined comparison")
    _validate_source_chain(
        comparison,
        manifest_path=manifest_path,
        control_path=control_eval_path,
        expanded_path=expanded_eval_path,
    )
    contract = _validate_manifest(manifest, repo_root=repo_root)
    control = _validate_eval(
        control_document,
        group=CONTROL_GROUP,
        contract=contract,
        repo_root=repo_root,
    )
    expanded = _validate_eval(
        expanded_document,
        group=EXPANDED_GROUP,
        contract=contract,
        repo_root=repo_root,
    )
    comparison_values = _validate_comparison(
        comparison,
        manifest=manifest,
        contract=contract,
        control=control,
        expanded=expanded,
    )
    control_audits = _validate_sessions(
        control_sessions,
        group=CONTROL_GROUP,
        eval_sessions=control["session_map"],
        contract=contract,
        repo_root=repo_root,
    )
    expanded_audits = _validate_sessions(
        expanded_sessions,
        group=EXPANDED_GROUP,
        eval_sessions=expanded["session_map"],
        contract=contract,
        repo_root=repo_root,
    )
    control_row, expanded_row = _build_rows(
        manifest=manifest,
        contract=contract,
        comparison=comparison,
        comparison_values=comparison_values,
        manifest_path=manifest_path,
        control_eval_path=control_eval_path,
        expanded_eval_path=expanded_eval_path,
        comparison_path=comparison_path,
        control_audits=control_audits,
        expanded_audits=expanded_audits,
        repo_root=repo_root,
    )
    report_payload = None
    if report_path is not None:
        report_payload = build_experiment_report.build_html(
            [*existing_rows, control_row, expanded_row]
        ).encode("utf-8")
    separator = (
        b"" if not raw_registry or raw_registry.endswith((b"\n", b"\r")) else b"\n"
    )
    registry_payload = (
        raw_registry
        + separator
        + _encode_row(control_row)
        + b"\n"
        + _encode_row(expanded_row)
        + b"\n"
    )
    _atomic_replace(registry_path, registry_payload)
    if report_path is not None and report_payload is not None:
        _atomic_replace(Path(report_path), report_payload)
    return control_row, expanded_row


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register the frozen B5 post-2020 forward pair"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--control-eval", type=Path, required=True)
    parser.add_argument("--expanded-eval", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--control-sessions", type=Path, nargs=5, required=True)
    parser.add_argument("--expanded-sessions", type=Path, nargs=5, required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--repo-root", type=Path, default=QLIB_ROOT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    control, expanded = register_results(
        manifest_path=args.manifest,
        control_eval_path=args.control_eval,
        expanded_eval_path=args.expanded_eval,
        comparison_path=args.comparison,
        control_sessions=args.control_sessions,
        expanded_sessions=args.expanded_sessions,
        registry_path=args.registry,
        report_path=args.report_output,
        repo_root=args.repo_root,
    )
    print(f"registered: {control['exp_id']}, {expanded['exp_id']}")


if __name__ == "__main__":
    main()
