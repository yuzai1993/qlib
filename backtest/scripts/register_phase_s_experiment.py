"""Preregister and finalize Phase S experiment rows in the canonical registry."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence

from phase_s_protocol import (
    ACCOUNT,
    BASELINE_CANDIDATE_ID,
    EXCHANGE_KWARGS,
    MODEL_REFS,
    POOL_BENCHMARKS,
    TEST_SEGMENT,
    VALID_SEGMENT,
    FrozenModel,
    load_frozen_model,
    select_valid_winner,
    sha256_file,
    strategy_grid,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = REPO_ROOT / "backtest/experiments/registry.jsonl"
STATE_ORDER = {"preregistered": 0, "valid_complete": 1, "test_complete": 2}


def _relative_or_absolute(path: Path, root: Path = REPO_ROOT) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(root.resolve()))
    except ValueError:
        return str(resolved)


def _prediction_artifacts(manifest: dict, model_ref: str) -> list[dict]:
    entries = [
        dict(entry)
        for entry in manifest.get("predictions") or []
        if entry.get("model_ref") == model_ref
    ]
    expected = {
        (pool, segment)
        for pool in POOL_BENCHMARKS
        for segment in ("valid", "test")
    }
    actual = {(entry.get("pool"), entry.get("segment")) for entry in entries}
    if actual != expected:
        raise ValueError(
            f"prediction artifact matrix mismatch for {model_ref}: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    for entry in entries:
        coverage = entry.get("coverage") or {}
        if not entry.get("prediction_sha256") or not coverage.get("index_sha256"):
            raise ValueError(f"prediction artifact incomplete: {entry}")
        path = Path(str(entry.get("path") or "")).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        if not path.is_file():
            raise ValueError(f"prediction artifact missing: {path}")
        if sha256_file(path) != entry["prediction_sha256"]:
            raise ValueError(f"prediction artifact SHA mismatch: {path}")
    return sorted(entries, key=lambda entry: (entry["pool"], entry["segment"]))


def build_preregistered_row(
    frozen: FrozenModel,
    prediction_manifest: dict,
    *,
    protocol_path: str,
) -> dict[str, Any]:
    model_ref = frozen.model_ref
    manifest_ref = str(frozen.manifest.get("baseline_ref") or model_ref.upper())
    hypothesis = (
        "B1-M 次日信号可能受集中持仓和高换手拖累；测试分散度、替换比例、短持有期与渐进调仓。"
        if model_ref == "b1-m"
        else "B6-M H40 排序信号适合更低换手、更长最低持有期和适度分散；测试 TopkDropout 与渐进 SoftTopk。"
    )
    return {
        "exp_id": f"strategy-sweep/{model_ref}",
        "direction": f"strategy-sweep-{model_ref}",
        "phase": "S",
        "date": str(date.today()),
        "state": "preregistered",
        "hypothesis": hypothesis,
        "baseline_ref": "B1-S v1.0",
        "frozen_model_ref": manifest_ref,
        "model_ref": model_ref,
        "model_manifest": _relative_or_absolute(frozen.manifest_path),
        "model_path": _relative_or_absolute(frozen.model_path),
        "model_sha256": frozen.model_sha256,
        "source_config": _relative_or_absolute(frozen.source_config),
        "protocol_path": protocol_path,
        "strategy_grid": strategy_grid(model_ref),
        "selection_pool": "csi1000",
        "selection_segment": list(VALID_SEGMENT),
        "selection_metric": "excess_return_with_cost.information_ratio",
        "selection_rule": [
            "ir desc",
            "annualized_return desc",
            "max_drawdown desc",
            "annualized_one_way_turnover asc",
            "candidate_id asc",
        ],
        "test_pools": list(POOL_BENCHMARKS),
        "test_segment": list(TEST_SEGMENT),
        "test_policy": "freeze_valid_winner_then_baseline_and_winner_once_per_pool",
        "account": ACCOUNT,
        "risk_degree": 0.95,
        "fees": dict(EXCHANGE_KWARGS),
        "benchmarks": dict(POOL_BENCHMARKS),
        "data_version": prediction_manifest.get("data_version"),
        "prediction_artifacts": _prediction_artifacts(
            prediction_manifest, model_ref
        ),
        "result_dirs": [],
        "cleanup_retention_eligible": False,
        "conclusion": "preregistered",
        "note": "test 未打开；候选网格与选型规则已冻结",
    }


def load_registry(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def upsert_registry_row(
    registry: Path,
    row: dict,
    *,
    expected_previous_state: Optional[str],
) -> None:
    if not row.get("exp_id"):
        raise ValueError("registry row requires exp_id")
    rows = load_registry(registry)
    matches = [i for i, current in enumerate(rows) if current.get("exp_id") == row["exp_id"]]
    if len(matches) > 1:
        raise ValueError(f"duplicate registry exp_id: {row['exp_id']}")
    if matches:
        index = matches[0]
        previous = rows[index]
        if previous.get("state") != expected_previous_state:
            raise ValueError(
                f"expected previous state {expected_previous_state!r} for {row['exp_id']}, "
                f"found {previous.get('state')!r}"
            )
        previous_order = STATE_ORDER.get(str(previous.get("state")), -1)
        next_order = STATE_ORDER.get(str(row.get("state")), -1)
        if next_order != previous_order + 1:
            raise ValueError(
                f"non-monotonic state transition: {previous.get('state')} -> {row.get('state')}"
            )
        rows[index] = row
    else:
        if expected_previous_state is not None:
            raise ValueError(
                f"expected previous state {expected_previous_state!r} but row is absent"
            )
        if row.get("state") != "preregistered":
            raise ValueError("new Phase S row must start at preregistered")
        rows.append(row)
    registry.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry.with_name(registry.name + ".tmp")
    temporary.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows),
        encoding="utf-8",
    )
    temporary.replace(registry)


def bind_valid_results(row: dict, result_path: Path) -> dict:
    if row.get("state") != "preregistered":
        raise ValueError("valid results require preregistered state")
    payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
    if (
        payload.get("model_ref") != row.get("model_ref")
        or payload.get("pool") != "csi1000"
        or payload.get("segment") != "valid"
    ):
        raise ValueError("valid result identity mismatch")
    result_rows = payload.get("all_rows") or []
    expected_ids = {candidate["candidate_id"] for candidate in row["strategy_grid"]}
    actual_ids = {candidate.get("candidate_id") for candidate in result_rows}
    if actual_ids != expected_ids:
        raise ValueError(
            f"valid candidate set differs from preregistration: "
            f"missing={sorted(expected_ids - actual_ids)}, "
            f"extra={sorted(actual_ids - expected_ids)}"
        )
    expected_pred_sha = next(
        item["prediction_sha256"]
        for item in row["prediction_artifacts"]
        if item["pool"] == "csi1000" and item["segment"] == "valid"
    )
    if any(item.get("source_pred_sha256") != expected_pred_sha for item in result_rows):
        raise ValueError("valid result prediction SHA differs from preregistration")
    winner = select_valid_winner(result_rows)
    if payload.get("winner", {}).get("candidate_id") not in (None, winner["candidate_id"]):
        raise ValueError("valid result winner differs from canonical selection")
    out = dict(row)
    out.update(
        state="valid_complete",
        selected_candidate_id=winner["candidate_id"],
        selected_strategy=next(
            candidate
            for candidate in row["strategy_grid"]
            if candidate["candidate_id"] == winner["candidate_id"]
        ),
        valid_result_path=_relative_or_absolute(Path(result_path)),
        valid_result_sha256=sha256_file(Path(result_path)),
        valid_results=result_rows,
        valid_winner_metrics={
            "ir": winner["excess_with_cost_information_ratio"],
            "ann": winner["excess_with_cost_annualized_return"],
            "mdd": winner["excess_with_cost_max_drawdown"],
            "annualized_one_way_turnover": winner["annualized_one_way_turnover"],
        },
        result_dirs=[
            candidate["result_dir"]
            for candidate in result_rows
            if candidate.get("result_dir")
        ],
        conclusion="valid_winner_frozen",
        note="valid 胜者已冻结；test 尚未参与选型",
    )
    return out


def bind_test_results(row: dict, result_path: Path) -> dict:
    if row.get("state") != "valid_complete":
        raise ValueError("test results require valid_complete state")
    payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
    if payload.get("model_ref") != row.get("model_ref"):
        raise ValueError("test result model_ref mismatch")
    pools = payload.get("pools") or {}
    if set(pools) != set(POOL_BENCHMARKS):
        raise ValueError("test result pools must be csi1000/csi300/csi500")
    expected_ids = {BASELINE_CANDIDATE_ID, row["selected_candidate_id"]}
    test_results: dict[str, list[dict]] = {}
    result_dirs = list(row.get("result_dirs") or [])
    metrics_summary: dict[str, dict[str, float]] = {}
    for pool, pool_payload in pools.items():
        if pool_payload.get("segment") != "test":
            raise ValueError(f"test segment mismatch for {pool}")
        candidates = pool_payload.get("all_rows") or []
        if {candidate.get("candidate_id") for candidate in candidates} != expected_ids:
            raise ValueError(f"test candidate set mismatch for {pool}")
        expected_pred_sha = next(
            item["prediction_sha256"]
            for item in row["prediction_artifacts"]
            if item["pool"] == pool and item["segment"] == "test"
        )
        if any(item.get("source_pred_sha256") != expected_pred_sha for item in candidates):
            raise ValueError(f"test result prediction SHA differs for {pool}")
        test_results[pool] = candidates
        winner = next(
            candidate
            for candidate in candidates
            if candidate["candidate_id"] == row["selected_candidate_id"]
        )
        metrics_summary[pool] = {
            "ir": winner["excess_with_cost_information_ratio"],
            "ann": winner["excess_with_cost_annualized_return"],
            "mdd": winner["excess_with_cost_max_drawdown"],
        }
        result_dirs.extend(
            candidate["result_dir"]
            for candidate in candidates
            if candidate.get("result_dir")
        )
    out = dict(row)
    out.update(
        state="test_complete",
        test_result_path=_relative_or_absolute(Path(result_path)),
        test_result_sha256=sha256_file(Path(result_path)),
        test_results=test_results,
        metrics_summary=metrics_summary,
        result_dirs=list(dict.fromkeys(result_dirs)),
        cleanup_retention_eligible=True,
        conclusion="complete",
        note="仅按 CSI1000 valid 选型；test 只评估冻结胜者与 B1-S 基线",
    )
    return out


def build_phase_s_baseline_anchor(sweep_row: dict) -> dict[str, Any]:
    """Build the model-specific B1-S anchor required for a completed sweep."""
    model_ref = str(sweep_row.get("model_ref") or "")
    if sweep_row.get("phase") != "S" or model_ref not in MODEL_REFS:
        raise ValueError("baseline anchor requires a completed Phase S sweep row")
    metrics_summary: dict[str, dict[str, float]] = {}
    result_dirs = []
    baseline_results = {}
    for pool in POOL_BENCHMARKS:
        candidates = (sweep_row.get("test_results") or {}).get(pool) or []
        baseline = next(
            (item for item in candidates if item.get("candidate_id") == BASELINE_CANDIDATE_ID),
            None,
        )
        if baseline is None:
            raise ValueError(f"B1-S baseline result missing for {model_ref}/{pool}")
        baseline_results[pool] = baseline
        metrics_summary[pool] = {
            "ir": baseline["excess_with_cost_information_ratio"],
            "ann": baseline["excess_with_cost_annualized_return"],
            "mdd": baseline["excess_with_cost_max_drawdown"],
        }
        if baseline.get("result_dir"):
            result_dirs.append(baseline["result_dir"])
    return {
        "exp_id": f"baseline/b1-s-on-{model_ref}",
        "direction": sweep_row["direction"],
        "phase": "S",
        "date": sweep_row.get("date"),
        "conclusion": "baseline",
        "hypothesis": f"B1-S 在冻结 {model_ref.upper()} 分数上的模型专属策略对照。",
        "baseline_ref": "B1-S v1.0",
        "frozen_model_ref": sweep_row.get("frozen_model_ref"),
        "model_ref": model_ref,
        "model_manifest": sweep_row.get("model_manifest"),
        "model_path": sweep_row.get("model_path"),
        "model_sha256": sweep_row.get("model_sha256"),
        "account": sweep_row.get("account"),
        "fees": sweep_row.get("fees"),
        "strategy": next(
            item for item in sweep_row["strategy_grid"]
            if item["candidate_id"] == BASELINE_CANDIDATE_ID
        ) if sweep_row.get("strategy_grid") else {"candidate_id": BASELINE_CANDIDATE_ID},
        "metrics_summary": metrics_summary,
        "test_results": baseline_results,
        "result_dirs": result_dirs,
        "cleanup_retention_eligible": False,
        "note": "模型专属 B1-S baseline；不得跨 frozen_model_ref 复用数值",
    }


def write_protocol(path: Path) -> None:
    payload = {
        "schema_version": 1,
        "date": str(date.today()),
        "phase": "S",
        "model_source": "backtest/models/baselines/<model-ref>/manifest.json",
        "models": {
            model_ref: {"strategy_grid": strategy_grid(model_ref)}
            for model_ref in MODEL_REFS
        },
        "selection_pool": "csi1000",
        "selection_segment": list(VALID_SEGMENT),
        "selection_rule": [
            "ir desc",
            "annualized_return desc",
            "max_drawdown desc",
            "annualized_one_way_turnover asc",
            "candidate_id asc",
        ],
        "test_pools": list(POOL_BENCHMARKS),
        "test_segment": list(TEST_SEGMENT),
        "account": ACCOUNT,
        "fees": dict(EXCHANGE_KWARGS),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register Phase S experiment state")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    sub = parser.add_subparsers(dest="command", required=True)
    protocol_parser = sub.add_parser("protocol")
    protocol_parser.add_argument("--output", required=True, type=Path)
    pre = sub.add_parser("preregister")
    pre.add_argument("--model-ref", required=True, choices=MODEL_REFS)
    pre.add_argument("--prediction-manifest", required=True, type=Path)
    pre.add_argument("--protocol-path", required=True)
    valid = sub.add_parser("valid")
    valid.add_argument("--model-ref", required=True, choices=MODEL_REFS)
    valid.add_argument("--result", required=True, type=Path)
    test = sub.add_parser("test")
    test.add_argument("--model-ref", required=True, choices=MODEL_REFS)
    test.add_argument("--result", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    if args.command == "protocol":
        write_protocol(args.output)
        print(args.output)
        return
    exp_id = f"strategy-sweep/{args.model_ref}"
    if args.command == "preregister":
        frozen = load_frozen_model(REPO_ROOT, args.model_ref)
        prediction_manifest = json.loads(
            args.prediction_manifest.read_text(encoding="utf-8")
        )
        row = build_preregistered_row(
            frozen,
            prediction_manifest,
            protocol_path=args.protocol_path,
        )
        upsert_registry_row(args.registry, row, expected_previous_state=None)
    else:
        current = next(
            (row for row in load_registry(args.registry) if row.get("exp_id") == exp_id),
            None,
        )
        if current is None:
            raise ValueError(f"registry row missing: {exp_id}")
        if args.command == "valid":
            row = bind_valid_results(current, args.result)
            previous = "preregistered"
        else:
            row = bind_test_results(current, args.result)
            previous = "valid_complete"
        upsert_registry_row(
            args.registry,
            row,
            expected_previous_state=previous,
        )
    print(f"{row['exp_id']} -> {row['state']}")


if __name__ == "__main__":
    main()
