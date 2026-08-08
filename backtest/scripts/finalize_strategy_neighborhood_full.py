"""Register and finalize the B3-S full-history neighborhood experiment."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import load_config  # noqa: E402
from phase_s_protocol import (  # noqa: E402
    EXCHANGE_KWARGS,
    FULL_SEGMENT,
    POOL_BENCHMARKS,
    load_frozen_model,
    sha256_file,
)
from run_strategy_neighborhood_full import (  # noqa: E402
    DEFAULT_ACCOUNT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PREDICTION_MANIFEST,
    EVALUATION_MODE,
    EXP_ID,
    MODEL_REF,
    POOL,
    SELECTION_RULE,
    effective_config_sha256,
    validate_full_prediction_manifest,
)
from strategy_neighborhood_protocol import (  # noqa: E402
    ANN_KEY,
    IR_KEY,
    MDD_KEY,
    TURNOVER_KEY,
    score_valid_candidates,
    strategy_neighborhood_grid,
)

DEFAULT_REGISTRY = REPO_ROOT / "backtest/experiments/registry.jsonl"
DEFAULT_PROTOCOL = DEFAULT_OUTPUT_ROOT / "protocol.json"
DEFAULT_RESULTS = DEFAULT_OUTPUT_ROOT / "full_results.json"
CORRECTION_EXP_ID = f"{EXP_ID}-correction-v1"
DEFAULT_CORRECTION = DEFAULT_OUTPUT_ROOT / "full_baseline_comparison_correction_v1.json"
B3_S_FULL_CANDIDATE_ID = "topk-t20-d2-h10-r095"
NEIGHBORHOOD_EXP_PREFIX = "strategy-neighborhood/b3-s-local-full"


def _correction_exp_id(exp_id: str) -> str:
    return f"{exp_id}-correction-v1"


def _protocol_account(protocol: dict[str, Any]) -> float:
    account = protocol.get("account")
    if not isinstance(account, (int, float)) or not math.isfinite(float(account)) or float(account) <= 0:
        raise ValueError("full-period protocol account must be a positive finite number")
    return float(account)


def _resolve_path(path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate.resolve()


def _path_text(path: Path | str) -> str:
    resolved = _resolve_path(path)
    try:
        return str(resolved.relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(resolved)


def _read_json(path: Path | str) -> dict[str, Any]:
    return json.loads(_resolve_path(path).read_text(encoding="utf-8"))


def _validate_protocol(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    exp_id = str(protocol.get("exp_id") or "")
    if not exp_id.startswith(NEIGHBORHOOD_EXP_PREFIX):
        raise ValueError("full-period protocol exp_id is unsupported")
    _protocol_account(protocol)
    expected = {
        "schema_version": 2,
        "direction": "strategy-neighborhood-b3-s-full",
        "phase": "S",
        "evaluation_mode": EVALUATION_MODE,
        "baseline_ref": "B3-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "model_ref": MODEL_REF,
        "fees": EXCHANGE_KWARGS,
        "benchmark": POOL_BENCHMARKS[POOL],
        "selection_pool": POOL,
        "selection_segment": list(FULL_SEGMENT),
        "selection_rule": SELECTION_RULE,
    }
    for key, value in expected.items():
        if protocol.get(key) != value:
            raise ValueError(f"full-period protocol {key} differs from contract")
    if exp_id == EXP_ID and float(protocol["account"]) != float(DEFAULT_ACCOUNT):
        raise ValueError("default full-period protocol must keep account=10000000")
    grid = protocol.get("strategy_grid") or []
    if grid != strategy_neighborhood_grid():
        raise ValueError("full-period protocol requires the exact 540-candidate grid")
    if len({str(item.get("candidate_id") or "") for item in grid}) != 540:
        raise ValueError("full-period protocol candidate IDs are not unique")
    base_path = protocol.get("base_config")
    base_sha = protocol.get("base_config_sha256")
    if not base_path or not base_sha:
        raise ValueError("full-period protocol lacks base config identity")
    resolved_base = _resolve_path(str(base_path))
    if not resolved_base.is_file() or sha256_file(resolved_base) != base_sha:
        raise ValueError("base config SHA differs from full-period protocol")
    return copy.deepcopy(grid)


def _portable_prediction_artifact(entry: dict[str, Any]) -> dict[str, Any]:
    portable = copy.deepcopy(entry)
    portable["path"] = _path_text(str(portable["path"]))
    portable["model_path"] = _path_text(str(portable["model_path"]))
    for source in portable.get("sources") or []:
        if source.get("path"):
            source["path"] = _path_text(source["path"])
    return portable


def _verify_json_input(payload: dict[str, Any], path: Path, *, label: str) -> str:
    resolved = _resolve_path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} missing: {resolved}")
    if _read_json(resolved) != payload:
        raise ValueError(f"{label} payload differs from file")
    return sha256_file(resolved)


def build_preregistered_row(
    protocol: dict[str, Any],
    prediction_manifest: dict[str, Any],
    *,
    protocol_path: Path,
    prediction_manifest_path: Path,
) -> dict[str, Any]:
    """Freeze all full-period identities before the first candidate is run."""
    grid = _validate_protocol(protocol)
    protocol_sha = _verify_json_input(protocol, protocol_path, label="protocol")
    manifest_sha = _verify_json_input(
        prediction_manifest,
        prediction_manifest_path,
        label="prediction manifest",
    )
    prediction, _ = validate_full_prediction_manifest(
        prediction_manifest, prediction_manifest_path
    )
    prediction = _portable_prediction_artifact(prediction)
    frozen = load_frozen_model(REPO_ROOT, MODEL_REF)
    account = _protocol_account(protocol)
    return {
        "exp_id": protocol["exp_id"],
        "direction": "strategy-neighborhood-b3-s-full",
        "phase": "S",
        "date": str(date.today()),
        "state": "preregistered",
        "conclusion": "preregistered",
        "hypothesis": (
            "B3-S 邻域在 CSI1000 全历史连续区间存在稳定平台；轴向邻域扣费超额 "
            "IR 下分位可降低单点尖峰驱动的选型风险。"
        ),
        "baseline_ref": "B3-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "model_ref": MODEL_REF,
        "model_manifest": _path_text(frozen.manifest_path),
        "model_manifest_sha256": sha256_file(frozen.manifest_path),
        "model_path": _path_text(frozen.model_path),
        "model_sha256": frozen.model_sha256,
        "source_config": _path_text(frozen.source_config),
        "source_config_sha256": sha256_file(frozen.source_config),
        "protocol_path": _path_text(protocol_path),
        "protocol_sha256": protocol_sha,
        "prediction_manifest": _path_text(prediction_manifest_path),
        "prediction_manifest_sha256": manifest_sha,
        "prediction_artifact": prediction,
        "base_config": _path_text(protocol["base_config"]),
        "base_config_sha256": protocol["base_config_sha256"],
        "candidate_count": len(grid),
        "evaluation_mode": EVALUATION_MODE,
        "selection_pool": POOL,
        "selection_segment": list(FULL_SEGMENT),
        "selection_metric": "axial_neighbor_excess_with_cost_ir_p25",
        "selection_rule": copy.deepcopy(protocol.get("selection_rule") or []),
        "account": account,
        "fees": copy.deepcopy(EXCHANGE_KWARGS),
        "benchmark": POOL_BENCHMARKS[POOL],
        "data_version": prediction_manifest.get("data_version"),
        "metrics_summary": {},
        "result_dirs": [],
        "cleanup_retention_eligible": False,
        "note": (
            f"540 组全历史样本内比较与稳健排序规则已冻结（account={int(account)}）。"
        ),
    }


def _verify_preregistered_artifacts(
    preregistered: dict[str, Any],
    protocol: dict[str, Any],
    prediction_manifest_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str], dict[str, Any]]:
    if preregistered.get("exp_id") != protocol.get("exp_id"):
        raise ValueError("finalization requires matching protocol exp_id")
    if preregistered.get("state") != "preregistered":
        raise ValueError("finalization requires a preregistered row")
    grid = _validate_protocol(protocol)

    protocol_path = _resolve_path(str(preregistered.get("protocol_path") or ""))
    expected_protocol_sha = preregistered.get("protocol_sha256")
    if (
        not protocol_path.is_file()
        or sha256_file(protocol_path) != expected_protocol_sha
    ):
        raise ValueError("protocol SHA differs from preregistration")
    if _read_json(protocol_path) != protocol:
        raise ValueError("protocol payload differs from preregistration")

    declared_manifest_path = _resolve_path(
        str(preregistered.get("prediction_manifest") or "")
    )
    if declared_manifest_path != _resolve_path(prediction_manifest_path):
        raise ValueError("prediction manifest path differs from preregistration")
    expected_manifest_sha = preregistered.get("prediction_manifest_sha256")
    if (
        not declared_manifest_path.is_file()
        or sha256_file(declared_manifest_path) != expected_manifest_sha
    ):
        raise ValueError("prediction manifest SHA differs from preregistration")
    manifest = _read_json(declared_manifest_path)
    prediction, _ = validate_full_prediction_manifest(manifest, declared_manifest_path)
    prediction = _portable_prediction_artifact(prediction)
    frozen_prediction = preregistered.get("prediction_artifact") or {}
    if prediction != frozen_prediction:
        raise ValueError("prediction artifact differs from preregistration")

    base_path = _resolve_path(str(protocol.get("base_config") or ""))
    base_sha = protocol.get("base_config_sha256")
    if not base_path.is_file() or sha256_file(base_path) != base_sha:
        raise ValueError("base config SHA differs from preregistration")
    if preregistered.get("base_config_sha256") != base_sha:
        raise ValueError("base config SHA differs from preregistered row")

    frozen = load_frozen_model(REPO_ROOT, MODEL_REF)
    model_contract = {
        "model_manifest_sha256": sha256_file(frozen.manifest_path),
        "model_sha256": frozen.model_sha256,
        "source_config_sha256": sha256_file(frozen.source_config),
    }
    for key, value in model_contract.items():
        if preregistered.get(key) != value:
            raise ValueError(f"{key.replace('_', ' ')} differs from preregistration")
    run_contract = {
        "protocol_sha256": str(expected_protocol_sha),
        "prediction_manifest_sha256": str(expected_manifest_sha),
        "base_config_sha256": str(base_sha),
    }
    return grid, prediction, run_contract, load_config(str(base_path))


def _same_winner(stored: dict[str, Any], recomputed: dict[str, Any]) -> bool:
    keys = (
        "candidate_id",
        "neighbor_count",
        "neighborhood_complete",
        "neighbor_ir_p25",
        IR_KEY,
        ANN_KEY,
        MDD_KEY,
        TURNOVER_KEY,
    )
    for key in keys:
        left = stored.get(key)
        right = recomputed.get(key)
        if isinstance(right, float):
            try:
                if not math.isclose(float(left), right, rel_tol=0.0, abs_tol=1e-12):
                    return False
            except (TypeError, ValueError):
                return False
        elif left != right:
            return False
    return True


def _ranking_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -float(row["neighbor_ir_p25"]),
        -float(row[IR_KEY]),
        -float(row[ANN_KEY]),
        -float(row[MDD_KEY]),
        float(row[TURNOVER_KEY]),
        str(row["candidate_id"]),
    )


def _winner_metrics(winner: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "neighbor_count",
        "neighborhood_complete",
        "neighbor_ir_p25",
        IR_KEY,
        ANN_KEY,
        MDD_KEY,
        TURNOVER_KEY,
        "yearly_ir",
        "absolute_portfolio",
    )
    return {key: copy.deepcopy(winner.get(key)) for key in keys}


def build_complete_row(
    preregistered: dict[str, Any],
    protocol: dict[str, Any],
    results: dict[str, Any],
    *,
    results_path: Path,
    prediction_manifest_path: Path,
) -> dict[str, Any]:
    """Verify all identities, independently rescore, and freeze one completed row."""
    grid, prediction, run_contract, base = _verify_preregistered_artifacts(
        preregistered, protocol, prediction_manifest_path
    )
    exp_id = str(preregistered["exp_id"])
    account = _protocol_account(protocol)
    expected_result_contract = {
        "state": "full_complete",
        "exp_id": exp_id,
        "evaluation_mode": EVALUATION_MODE,
        "model_ref": MODEL_REF,
        "pool": POOL,
        "segment": "full",
        "selection_segment": list(FULL_SEGMENT),
        "protocol_sha256": run_contract["protocol_sha256"],
        "run_contract": run_contract,
    }
    for key, value in expected_result_contract.items():
        if results.get(key) != value:
            label = "protocol SHA" if key == "protocol_sha256" else key
            raise ValueError(f"full results {label} differs from preregistration")

    rows = results.get("all_rows") or []
    expected_ids = {str(candidate["candidate_id"]) for candidate in grid}
    actual_ids = [str(row.get("candidate_id") or "") for row in rows]
    if (
        len(rows) != 540
        or len(set(actual_ids)) != 540
        or set(actual_ids) != expected_ids
    ):
        raise ValueError("full results require exactly 540 unique candidate IDs")
    if any(row.get("status") != "success" for row in rows):
        raise ValueError("full results contain an unsuccessful candidate")

    prediction_sha = prediction["prediction_sha256"]
    by_id = {str(candidate["candidate_id"]): candidate for candidate in grid}
    for row in rows:
        candidate_id = str(row["candidate_id"])
        if row.get("source_pred_sha256") != prediction_sha:
            raise ValueError(
                f"{candidate_id} prediction SHA differs from preregistration"
            )
        expected_config_sha = effective_config_sha256(
            base, by_id[candidate_id], account=account
        )
        if row.get("effective_config_sha256") != expected_config_sha:
            raise ValueError(f"{candidate_id} effective config SHA differs")

    rescored, recomputed_winner = score_valid_candidates(rows, grid)
    if not all(row.get("neighborhood_complete") for row in rescored):
        raise ValueError("full results contain an incomplete neighborhood")
    if not _same_winner(results.get("winner") or {}, recomputed_winner):
        raise ValueError("stored winner differs from recomputed full-period winner")

    result_file = _resolve_path(results_path)
    if not result_file.is_file():
        raise FileNotFoundError(f"full results missing: {result_file}")
    if _read_json(result_file) != results:
        raise ValueError("full results payload differs from checkpoint file")
    ranked = sorted(rescored, key=_ranking_key)
    winner_id = str(recomputed_winner["candidate_id"])
    selected = copy.deepcopy(by_id[winner_id])
    winner_metrics = _winner_metrics(recomputed_winner)

    complete = copy.deepcopy(preregistered)
    complete.update(
        state="complete",
        conclusion="full_history_candidate_complete",
        selected_candidate_id=winner_id,
        selected_strategy=selected,
        full_winner_metrics=winner_metrics,
        robust_top50=[copy.deepcopy(row) for row in ranked[:50]],
        full_result_path=_path_text(result_file),
        full_result_sha256=sha256_file(result_file),
        metrics_summary={
            POOL: {
                "ir": recomputed_winner[IR_KEY],
                "ann": recomputed_winner[ANN_KEY],
                "mdd": recomputed_winner[MDD_KEY],
                "turnover": recomputed_winner[TURNOVER_KEY],
            }
        },
        result_dirs=[],
        cleanup_retention_eligible=False,
        note=(
            "CSI1000 2020-01-13 至 2026-07-31 全历史样本内研究胜者；"
            "未使用独立 holdout，不自动提升 B3-S 或切换实盘。"
        ),
    )
    return complete


def _same_run_metrics(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "candidate_id",
        IR_KEY,
        ANN_KEY,
        MDD_KEY,
        TURNOVER_KEY,
        "yearly_ir",
    )
    return {key: copy.deepcopy(row.get(key)) for key in keys}


def build_full_baseline_comparison_correction(
    completed: dict[str, Any], results: dict[str, Any], *, results_path: Path
) -> dict[str, Any]:
    """Build an append-only audit correction; never rewrite a completed result row."""
    exp_id = str(completed.get("exp_id") or "")
    if (
        not exp_id.startswith(NEIGHBORHOOD_EXP_PREFIX)
        or completed.get("state") != "complete"
    ):
        raise ValueError("correction requires the completed full-period experiment")
    result_file = _resolve_path(results_path)
    if not result_file.is_file() or _read_json(result_file) != results:
        raise ValueError("correction requires the exact stored full results")
    if completed.get("full_result_sha256") != sha256_file(result_file):
        raise ValueError("correction full-result SHA differs from completed row")
    rows = results.get("all_rows") or []
    baseline_matches = [
        row for row in rows if row.get("candidate_id") == B3_S_FULL_CANDIDATE_ID
    ]
    winner_matches = [
        row
        for row in rows
        if row.get("candidate_id") == completed.get("selected_candidate_id")
    ]
    if len(baseline_matches) != 1 or len(winner_matches) != 1:
        raise ValueError("correction requires unique same-run baseline and winner rows")
    baseline = baseline_matches[0]
    winner = winner_matches[0]
    if baseline.get("status") != "success" or winner.get("status") != "success":
        raise ValueError("correction requires successful same-run rows")
    correction_id = _correction_exp_id(exp_id)
    return {
        "schema_version": 1,
        "exp_id": correction_id,
        "phase": "S",
        "state": "correction",
        "correction_of": exp_id,
        "date": str(date.today()),
        "evaluation_mode": EVALUATION_MODE,
        "selection_segment": list(FULL_SEGMENT),
        "baseline_ref": "B3-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "account": completed.get("account"),
        "full_result_path": _path_text(result_file),
        "full_result_sha256": sha256_file(result_file),
        "same_run_baseline": _same_run_metrics(baseline),
        "robust_winner": _same_run_metrics(winner),
        "selection_rationale": "neighbor_ir_p25_not_own_metric",
        "note": (
            "更正记录：同一冻结预测、同一全历史区间的 B3-S 基线与稳健胜者对照；"
            "胜者仅因预登记的轴向邻域 IR P25 规则入选，不自动提升 B3-S。"
        ),
    }


def append_registry_correction(registry: Path, correction: dict[str, Any]) -> None:
    correction_id = str(correction.get("exp_id") or "")
    parent_id = str(correction.get("correction_of") or "")
    if correction_id != _correction_exp_id(parent_id):
        raise ValueError("unsupported correction exp_id")
    rows = load_registry(registry)
    matches = [row for row in rows if row.get("exp_id") == correction_id]
    if matches:
        if len(matches) != 1 or matches[0] != correction:
            raise ValueError("correction history is immutable")
        return
    if len([row for row in rows if row.get("exp_id") == parent_id]) != 1:
        raise ValueError("correction requires exactly one original experiment row")
    with registry.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(correction, ensure_ascii=False, separators=(",", ":")) + "\n"
        )


def load_registry(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def upsert_registry_transition(
    registry: Path,
    row: dict[str, Any],
) -> None:
    exp_id = str(row.get("exp_id") or "")
    if not exp_id.startswith(NEIGHBORHOOD_EXP_PREFIX):
        raise ValueError(f"registry transition only supports full neighborhood: {exp_id}")
    lines = (
        registry.read_text(encoding="utf-8").splitlines(keepends=True)
        if registry.is_file()
        else []
    )
    parsed = [
        (index, json.loads(line)) for index, line in enumerate(lines) if line.strip()
    ]
    matches = [
        (index, current) for index, current in parsed if current.get("exp_id") == exp_id
    ]
    if len(matches) > 1:
        raise ValueError(f"duplicate registry exp_id: {exp_id}")
    serialized = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    if matches:
        index, previous = matches[0]
        if previous.get("state") == "complete":
            raise ValueError(f"completed registry row is immutable: {exp_id}")
        if previous.get("state") != "preregistered" or row.get("state") != "complete":
            raise ValueError(
                "registry transition must be preregistered -> complete; "
                f"found {previous.get('state')!r} -> {row.get('state')!r}"
            )
        lines[index] = serialized
    else:
        if row.get("state") != "preregistered":
            raise ValueError(
                "registry transition must be absent -> preregistered; "
                f"found absent -> {row.get('state')!r}"
            )
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.append(serialized)
    registry.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry.with_name(registry.name + ".tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    temporary.replace(registry)


def _unique_row(rows: Sequence[dict[str, Any]], *, exp_id: str) -> dict[str, Any]:
    matches = [row for row in rows if row.get("exp_id") == exp_id]
    if len(matches) != 1:
        raise ValueError(f"registry requires exactly one {exp_id} row")
    return matches[0]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("preregister", "finalize", "append-correction")
    )
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument(
        "--prediction-manifest", type=Path, default=DEFAULT_PREDICTION_MANIFEST
    )
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--correction", type=Path, default=DEFAULT_CORRECTION)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    if args.mode == "append-correction":
        results = _read_json(args.results)
        exp_id = str(results.get("exp_id") or EXP_ID)
        completed = _unique_row(load_registry(args.registry), exp_id=exp_id)
        correction = build_full_baseline_comparison_correction(
            completed, results, results_path=args.results
        )
        correction_path = _resolve_path(args.correction)
        if correction_path.exists() and _read_json(correction_path) != correction:
            raise ValueError("existing correction artifact is immutable")
        correction_path.parent.mkdir(parents=True, exist_ok=True)
        if not correction_path.exists():
            correction_path.write_text(
                json.dumps(correction, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        append_registry_correction(args.registry, correction)
        print(f"{args.mode}: {correction['exp_id']} -> {args.registry}")
        return
    protocol = _read_json(args.protocol)
    manifest = _read_json(args.prediction_manifest)
    exp_id = str(protocol.get("exp_id") or EXP_ID)
    if args.mode == "preregister":
        row = build_preregistered_row(
            protocol,
            manifest,
            protocol_path=args.protocol,
            prediction_manifest_path=args.prediction_manifest,
        )
        upsert_registry_transition(args.registry, row)
    else:
        preregistered = _unique_row(load_registry(args.registry), exp_id=exp_id)
        results = _read_json(args.results)
        row = build_complete_row(
            preregistered,
            protocol,
            results,
            results_path=args.results,
            prediction_manifest_path=args.prediction_manifest,
        )
        upsert_registry_transition(args.registry, row)
    print(f"{args.mode}: {row['exp_id']} -> {args.registry}")


if __name__ == "__main__":
    main()
