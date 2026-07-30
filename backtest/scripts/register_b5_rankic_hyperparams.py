"""Pre-register and finalize the frozen B5 valid-RankIC hyperparameter search."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

QLIB_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QLIB_ROOT))

from backtest.scripts.eval_frozen_b5_rankic import (  # noqa: E402
    validate_test_result,
    verify_manifest,
)
from backtest.scripts.freeze_b5_rankic_selection import (  # noqa: E402
    CANDIDATES,
    EVAL_LABEL_EXPR,
    MIN_COUNT,
    SAFE_VALID_SEGMENT,
    SEEDS,
    TEST_POOLS,
    TEST_SEGMENT,
    VALID_SEGMENT,
    sha256_file,
)


EXP_ID = "model-hyperparam/valid-rankic-search-v1"
BASELINE_EXP_ID = "baseline/b5-m"
BASELINE_REF = "B5 v1.0"
REGISTRY = BACKTEST_ROOT / "experiments" / "registry.jsonl"
IC_DIR = BACKTEST_ROOT / "experiments" / "ic"
MANIFEST = BACKTEST_ROOT / "experiments" / "b5_rankic_hyperparam_selection.json"
TEST_RESULT = IC_DIR / "mh_valid_rankic_selected_test_1d.json"
BASELINE_RESULT = IC_DIR / "ls_rank_norm_test_1d.json"
METRIC_KEYS = ("ic_mean", "icir", "rank_ic_mean", "rank_icir")

HYPOTHESIS = (
    "在B5的CSI1000、Alpha158+range、H40累计收益、DropnaLabel+CSRankNorm与"
    "DoubleEnsemble训练口径不变的前提下，在四个固定候选中仅按五种子"
    "CSI1000 valid RankIC选择一个胜者（RankICIR、candidate_id依次并列裁决），"
    "冻结选择后只对胜者进行一次三池test评估；固定次日valid RankIC仅用于早停。"
)

CONCLUSION_POLICY = {
    "improve": "csi1000/csi300/csi500三池test RankIC均严格高于B5",
    "regress": "csi1000 test RankIC不高于B5",
    "inconclusive": "csi1000提高但至少一个迁移池RankIC不高于B5",
}


def _fail(message: str) -> None:
    raise ValueError(message)


def _canonical_date(value: Any, label: str = "data_version") -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be a canonical YYYY-MM-DD date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail(f"{label} must be a valid canonical YYYY-MM-DD date: {value!r}")
    if parsed.isoformat() != value:
        _fail(f"{label} must be a valid canonical YYYY-MM-DD date: {value!r}")
    return value


def _config_paths() -> list[str]:
    return [
        (
            "backtest/configs/model-hyperparam/"
            f"{candidate}/mh_{candidate.replace('-', '_')}_s{seed}.yaml"
        )
        for candidate in CANDIDATES
        for seed in SEEDS
    ]


def build_pending_row(data_version: str) -> dict[str, Any]:
    """Build the immutable pre-test protocol row."""

    data_version = _canonical_date(data_version)
    return {
        "exp_id": EXP_ID,
        "direction": "model-hyperparam",
        "phase": "M",
        "date": date.today().isoformat(),
        "hypothesis": HYPOTHESIS,
        "baseline_ref": BASELINE_REF,
        "seeds": list(SEEDS),
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
        "selection_candidates": list(CANDIDATES),
        "config_count": len(CANDIDATES) * len(SEEDS),
        "configs": _config_paths(),
        "selection_segment": "valid",
        "selection_official_segment": list(VALID_SEGMENT),
        "selection_effective_segment": list(SAFE_VALID_SEGMENT),
        "selection_label": EVAL_LABEL_EXPR,
        "selection_label_role": "fixed_1d",
        "selection_min_count": MIN_COUNT,
        "selection_metric": "csi1000.valid.rank_ic_mean",
        "selection_tie_breaker": ["rank_icir", "candidate_id"],
        "primary_test_pool": "csi1000",
        "test_pools": list(TEST_POOLS),
        "test_segment": list(TEST_SEGMENT),
        "test_label": EVAL_LABEL_EXPR,
        "test_label_role": "fixed_1d",
        "test_min_count": MIN_COUNT,
        "test_policy": "freeze_valid_winner_then_test_once",
        "conclusion_policy": dict(CONCLUSION_POLICY),
        "data_version": data_version,
        "conclusion": "pending",
        "note": "预登记：未查看本搜索任何test指标，实盘B1配置与artifact不变。",
    }


def _decode_json_line(raw_line: bytes, line_number: int) -> Optional[dict]:
    body = raw_line.rstrip(b"\r\n")
    if not body.strip():
        return None
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"registry line {line_number} is not valid UTF-8 JSON: {exc}")
    if not isinstance(value, dict):
        _fail(f"registry line {line_number} must be a JSON object")
    return value


def _registry_entries(raw: bytes) -> list[tuple[int, dict]]:
    entries = []
    for index, raw_line in enumerate(raw.splitlines(keepends=True)):
        value = _decode_json_line(raw_line, index + 1)
        if value is not None:
            entries.append((index, value))
    return entries


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


def _encode_row(row: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            row,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail(f"registry row is not finite JSON: {exc}")


def register_pending(registry_path: Path, data_version: str) -> dict[str, Any]:
    """Atomically append one pending row without altering existing bytes."""

    registry_path = Path(registry_path)
    raw = registry_path.read_bytes() if registry_path.exists() else b""
    entries = _registry_entries(raw)
    if any(row.get("exp_id") == EXP_ID for _, row in entries):
        _fail(f"experiment {EXP_ID} already exists")
    row = build_pending_row(data_version)
    separator = b"" if not raw or raw.endswith((b"\n", b"\r")) else b"\n"
    _atomic_replace(registry_path, raw + separator + _encode_row(row) + b"\n")
    return row


def _load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
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


def _exact_segment(value: Any, expected: Sequence[str], label: str) -> None:
    if not isinstance(value, (list, tuple)) or list(map(str, value)) != list(expected):
        _fail(f"{label} segment drift")


def _validate_seed_pool(pool: Any, label: str) -> dict[str, Any]:
    if not isinstance(pool, dict):
        _fail(f"{label} pool payload missing")
    seeds = pool.get("seeds")
    expected_seed_keys = {str(seed) for seed in SEEDS}
    if not isinstance(seeds, dict) or set(seeds) != expected_seed_keys:
        _fail(f"{label} must contain exact five seeds")
    values = {key: [] for key in METRIC_KEYS}
    for seed in SEEDS:
        row = seeds[str(seed)]
        if not isinstance(row, dict) or int(row.get("n_days", 0)) <= 0:
            _fail(f"{label}/{seed} seed metrics missing")
        for key in METRIC_KEYS:
            values[key].append(_finite(row.get(key), f"{label}/{seed} {key}"))
    seed_mean = pool.get("seed_mean")
    if not isinstance(seed_mean, dict):
        _fail(f"{label} seed_mean missing")
    normalized = {}
    for key, per_seed in values.items():
        expected = sum(per_seed) / len(per_seed)
        actual = _finite(seed_mean.get(key), f"{label} seed_mean {key}")
        if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-15):
            _fail(f"{label} seed_mean differs from exact five seeds")
        normalized[key] = actual
    return normalized


def _baseline_anchor(entries: Sequence[tuple[int, dict]]) -> dict:
    matches = [row for _, row in entries if row.get("exp_id") == BASELINE_EXP_ID]
    if len(matches) != 1:
        _fail("registry must contain exactly one B5 registry anchor")
    anchor = matches[0]
    if (
        anchor.get("baseline_ref") != BASELINE_REF
        or anchor.get("conclusion") != "baseline"
        or anchor.get("seeds") != list(SEEDS)
    ):
        _fail("B5 registry anchor protocol drift")
    return anchor


def _validate_baseline_result(
    result: Mapping[str, Any],
    anchor: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    if (
        result.get("eval_segment_name") != "test"
        or result.get("eval_label") != EVAL_LABEL_EXPR
        or result.get("eval_label_role") != "fixed_1d"
    ):
        _fail("B5 registry anchor artifact is not exact test/fixed_1d")
    for key in ("eval_segment", "effective_eval_segment", "test_segment"):
        _exact_segment(result.get(key), TEST_SEGMENT, f"B5 {key}")
    sessions = result.get("sessions")
    if (
        not isinstance(sessions, list)
        or len(sessions) != len(SEEDS)
        or [row.get("seed") for row in sessions if isinstance(row, dict)]
        != list(SEEDS)
    ):
        _fail("B5 registry anchor artifact must contain exact five seeds")
    pools = result.get("pools")
    if not isinstance(pools, dict) or set(pools) != set(TEST_POOLS):
        _fail("B5 registry anchor artifact must contain exact three pools")
    summaries = {
        pool: _validate_seed_pool(pools[pool], f"B5 registry anchor/{pool}")
        for pool in TEST_POOLS
    }
    anchor_summary = anchor.get("metrics_summary")
    if not isinstance(anchor_summary, dict):
        _fail("B5 registry anchor metrics_summary missing")
    for pool in TEST_POOLS:
        stored = anchor_summary.get(pool)
        if not isinstance(stored, dict):
            _fail(f"B5 registry anchor metrics missing: {pool}")
        for key in METRIC_KEYS:
            value = _finite(stored.get(key), f"B5 registry anchor/{pool} {key}")
            if not math.isclose(
                value,
                summaries[pool][key],
                rel_tol=1e-12,
                abs_tol=1e-15,
            ):
                _fail(f"B5 registry anchor differs from baseline artifact: {pool}/{key}")
    if result.get("data_version") != anchor.get("data_version"):
        _fail("B5 registry anchor data_version differs from baseline artifact")
    return summaries


def _validate_pending(row: Mapping[str, Any]) -> None:
    if row.get("conclusion") != "pending":
        _fail(f"{EXP_ID} is not pending")
    data_version = _canonical_date(row.get("data_version"))
    expected = build_pending_row(data_version)
    if set(row) != set(expected):
        _fail("pending registry row fields differ from pre-registration")
    for key, value in expected.items():
        if key == "date":
            _canonical_date(row.get("date"), "pending date")
        elif row.get(key) != value:
            _fail(f"pending registry protocol drift: {key}")


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        _fail(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(f"{label} must be an ISO timestamp")
    return parsed


def _validate_frozen_contract(
    manifest: Mapping[str, Any],
    test_result: Mapping[str, Any],
    pending: Mapping[str, Any],
) -> None:
    if manifest.get("data_version") != pending.get("data_version"):
        _fail("selection manifest data_version differs from pending registry")
    if (
        manifest.get("selection_segment") != "valid"
        or manifest.get("selection_metric") != pending.get("selection_metric")
        or manifest.get("tie_breaker") != pending.get("selection_tie_breaker")
        or manifest.get("eval_label") != EVAL_LABEL_EXPR
        or manifest.get("eval_label_role") != "fixed_1d"
        or manifest.get("min_count") != MIN_COUNT
    ):
        _fail("selection manifest protocol differs from pre-registration")
    _exact_segment(
        manifest.get("official_valid_segment"),
        VALID_SEGMENT,
        "selection official valid",
    )
    _exact_segment(
        manifest.get("effective_valid_segment"),
        SAFE_VALID_SEGMENT,
        "selection effective valid",
    )
    candidates = manifest.get("candidates")
    valid_hashes = manifest.get("valid_result_hashes")
    if (
        not isinstance(candidates, dict)
        or set(candidates) != set(CANDIDATES)
        or not isinstance(valid_hashes, dict)
        or set(valid_hashes) != set(CANDIDATES)
    ):
        _fail("selection manifest must contain four valid candidates/artifacts")
    config_hashes = manifest.get("config_hashes")
    if not isinstance(config_hashes, list) or len(config_hashes) != 20:
        _fail("selection manifest must contain exactly 20 config hashes")
    config_keys = {
        (row.get("candidate"), row.get("seed"))
        for row in config_hashes
        if isinstance(row, dict)
    }
    if config_keys != {
        (candidate, seed) for candidate in CANDIDATES for seed in SEEDS
    }:
        _fail("selection manifest 20 config candidate/seed matrix drift")
    winner = manifest.get("selected_candidate")
    sessions = manifest.get("selected_sessions")
    if winner not in CANDIDATES:
        _fail("selection manifest winner invalid")
    if (
        not isinstance(sessions, list)
        or len(sessions) != len(SEEDS)
        or [row.get("seed") for row in sessions if isinstance(row, dict)]
        != list(SEEDS)
    ):
        _fail("selection manifest must contain five selected winner sessions")
    if candidates[winner].get("sessions") != sessions:
        _fail("selection manifest selected winner sessions mismatch")
    if test_result.get("data_version") != pending.get("data_version"):
        _fail("test result data_version differs from pending registry")
    if test_result.get("selected_candidate") != winner:
        _fail("test result selected winner differs from selection manifest")
    if test_result.get("sessions") != sessions:
        _fail("test result selected sessions differ from frozen winner")
    if _parse_timestamp(test_result.get("generated_at"), "test generated_at") < (
        _parse_timestamp(manifest.get("generated_at"), "manifest generated_at")
    ):
        _fail("test artifact timestamp must not precede frozen manifest")


def _test_metrics(test_result: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    pools = test_result.get("pools")
    if not isinstance(pools, dict) or list(pools) != list(TEST_POOLS):
        _fail("test result must contain official three pools in fixed order")
    return {
        pool: _validate_seed_pool(pools[pool], f"winner/{pool}")
        for pool in TEST_POOLS
    }


def _pairwise_rankic(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_seeds = candidate.get("seeds")
    baseline_seeds = baseline.get("seeds")
    expected = {str(seed) for seed in SEEDS}
    if (
        not isinstance(candidate_seeds, dict)
        or set(candidate_seeds) != expected
        or not isinstance(baseline_seeds, dict)
        or set(baseline_seeds) != expected
    ):
        _fail("pairwise CSI1000 RankIC requires exact five seeds")
    diffs = [
        _finite(
            candidate_seeds[str(seed)].get("rank_ic_mean"),
            f"winner csi1000/{seed} rank_ic_mean",
        )
        - _finite(
            baseline_seeds[str(seed)].get("rank_ic_mean"),
            f"B5 csi1000/{seed} rank_ic_mean",
        )
        for seed in SEEDS
    ]
    return {
        "n": len(SEEDS),
        "wins": sum(diff > 0 for diff in diffs),
        "diff_mean": sum(diffs) / len(diffs),
        "diffs": diffs,
        "seeds": list(SEEDS),
    }


def _conclusion(
    metrics: Mapping[str, Mapping[str, float]],
    baseline: Mapping[str, Mapping[str, float]],
) -> str:
    if all(
        metrics[pool]["rank_ic_mean"] > baseline[pool]["rank_ic_mean"]
        for pool in TEST_POOLS
    ):
        return "improve"
    if metrics["csi1000"]["rank_ic_mean"] <= baseline["csi1000"]["rank_ic_mean"]:
        return "regress"
    return "inconclusive"


def _display_path(path: Path) -> str:
    path = Path(path).expanduser().resolve()
    try:
        return str(path.relative_to(QLIB_ROOT))
    except ValueError:
        return str(path)


def _final_row(
    pending: Mapping[str, Any],
    manifest: Mapping[str, Any],
    test_result: Mapping[str, Any],
    baseline_result: Mapping[str, Any],
    baseline_summary: Mapping[str, Mapping[str, float]],
    *,
    manifest_path: Path,
    test_result_path: Path,
    baseline_result_path: Path,
) -> dict[str, Any]:
    metrics = _test_metrics(test_result)
    pairwise = _pairwise_rankic(
        test_result["pools"]["csi1000"],
        baseline_result["pools"]["csi1000"],
    )
    conclusion = _conclusion(metrics, baseline_summary)
    winner = manifest["selected_candidate"]
    valid_summary = {}
    for candidate in CANDIDATES:
        row = manifest["candidates"][candidate]
        valid_summary[candidate] = {
            "rank_ic_mean": _finite(
                row.get("rank_ic_mean"), f"{candidate} valid rank_ic_mean"
            ),
            "rank_icir": _finite(
                row.get("rank_icir"), f"{candidate} valid rank_icir"
            ),
        }
    winner_dirs = [row["session"] for row in manifest["selected_sessions"]]
    valid_artifacts = {
        candidate: dict(manifest["valid_result_hashes"][candidate])
        for candidate in CANDIDATES
    }
    manifest_sha = sha256_file(manifest_path)
    test_sha = sha256_file(test_result_path)
    note = (
        f"valid仅选中{winner}；冻结后一次test结论={conclusion}。"
        f"CSI1000 RankIC {metrics['csi1000']['rank_ic_mean']:.6f}"
        f"（B5 {baseline_summary['csi1000']['rank_ic_mean']:.6f}），"
        f"逐种子pairwise {pairwise['wins']}/5；"
        f"判定规则：{CONCLUSION_POLICY[conclusion]}。实盘B1未改动。"
    )
    return {
        **pending,
        "selected_candidate": winner,
        "candidate_valid_summary": valid_summary,
        "winner_result_dirs": winner_dirs,
        "result_dirs": winner_dirs
        + [_display_path(manifest_path), _display_path(test_result_path)],
        "metrics_summary": metrics,
        "metrics_by_eval_label": {"eval_1d": metrics},
        "pairwise_csi1000_rankic_vs_b5": pairwise,
        "selection_manifest": _display_path(manifest_path),
        "selection_manifest_sha256": manifest_sha,
        "test_result": _display_path(test_result_path),
        "test_result_sha256": test_sha,
        "audit_artifacts": {
            "selection_manifest": _display_path(manifest_path),
            "selection_manifest_sha256": manifest_sha,
            "test_result": _display_path(test_result_path),
            "test_result_sha256": test_sha,
            "baseline_result": _display_path(baseline_result_path),
            "baseline_result_sha256": sha256_file(baseline_result_path),
            "valid_results": valid_artifacts,
            "config_hashes": [dict(row) for row in manifest["config_hashes"]],
        },
        "conclusion": conclusion,
        "result_note": note,
    }


def finalize_registry(
    *,
    registry_path: Path,
    manifest_path: Path,
    test_result_path: Path,
    baseline_result_path: Path,
) -> dict[str, Any]:
    """Verify the frozen chain and replace the sole pending row in place."""

    registry_path = Path(registry_path)
    raw = registry_path.read_bytes()
    raw_lines = raw.splitlines(keepends=True)
    entries = _registry_entries(raw)
    target_rows = [
        (line_index, row)
        for line_index, row in entries
        if row.get("exp_id") == EXP_ID
    ]
    if len(target_rows) != 1 or target_rows[0][1].get("conclusion") != "pending":
        _fail(f"registry must contain exactly one pending {EXP_ID} row")
    target_index, pending = target_rows[0]
    _validate_pending(pending)
    anchor = _baseline_anchor(entries)

    manifest_path = Path(manifest_path)
    test_result_path = Path(test_result_path)
    baseline_result_path = Path(baseline_result_path)
    manifest = _load_json(manifest_path, "selection manifest")
    test_result = _load_json(test_result_path, "frozen test result")
    baseline_result = _load_json(baseline_result_path, "B5 baseline result")

    expected_manifest_sha = sha256_file(manifest_path)
    if test_result.get("selection_manifest_sha256") != expected_manifest_sha:
        _fail("test result selection manifest SHA-256 mismatch")
    verified_manifest = verify_manifest(manifest)
    if verified_manifest != manifest:
        _fail("verified manifest differs from supplied selection manifest")
    _validate_frozen_contract(verified_manifest, test_result, pending)
    validate_test_result(test_result, verified_manifest)
    baseline_summary = _validate_baseline_result(baseline_result, anchor)

    final = _final_row(
        pending,
        verified_manifest,
        test_result,
        baseline_result,
        baseline_summary,
        manifest_path=manifest_path,
        test_result_path=test_result_path,
        baseline_result_path=baseline_result_path,
    )
    old_line = raw_lines[target_index]
    if old_line.endswith(b"\r\n"):
        ending = b"\r\n"
    elif old_line.endswith(b"\n"):
        ending = b"\n"
    elif old_line.endswith(b"\r"):
        ending = b"\r"
    else:
        ending = b""
    raw_lines[target_index] = _encode_row(final) + ending
    _atomic_replace(registry_path, b"".join(raw_lines))
    return final


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-register or finalize the B5 valid-RankIC search"
    )
    parser.add_argument("--stage", choices=("pending", "final"), required=True)
    parser.add_argument("--data-version")
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--test-result", type=Path, default=TEST_RESULT)
    parser.add_argument("--baseline-result", type=Path, default=BASELINE_RESULT)
    args = parser.parse_args(argv)
    if args.stage == "pending" and not args.data_version:
        parser.error("--data-version is required for --stage pending")
    if args.stage == "final" and args.data_version is not None:
        parser.error("--data-version is only valid for --stage pending")
    return args


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    if args.stage == "pending":
        row = register_pending(args.registry, args.data_version)
    else:
        row = finalize_registry(
            registry_path=args.registry,
            manifest_path=args.manifest,
            test_result_path=args.test_result,
            baseline_result_path=args.baseline_result,
        )
    print(f"{args.stage}: {row['exp_id']} conclusion={row['conclusion']}")


if __name__ == "__main__":
    main()
