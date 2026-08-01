"""Register the non-selecting CSI1000 full-period strategy diagnostic."""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence

from phase_s_protocol import (
    ACCOUNT,
    BASELINE_CANDIDATE_ID,
    EXCHANGE_KWARGS,
    FULL_SEGMENT,
    MODEL_REFS,
    POOL_BENCHMARKS,
    RISK_DEGREE,
    FrozenModel,
    load_frozen_model,
    sha256_file,
    strategy_grid,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = REPO_ROOT / "backtest/experiments/registry.jsonl"
METRICS = (
    "annualized_return",
    "sharpe_ratio",
    "calmar_ratio",
    "annualized_volatility",
    "max_drawdown",
    "annualized_one_way_turnover",
)


def _relative_or_absolute(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(resolved)


def load_registry(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _full_prediction(manifest: dict, model_ref: str) -> dict:
    matches = [
        dict(item)
        for item in manifest.get("predictions") or []
        if (item.get("model_ref"), item.get("pool"), item.get("segment"))
        == (model_ref, "csi1000", "full")
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one full CSI1000 prediction for {model_ref}")
    entry = matches[0]
    coverage = entry.get("coverage") or {}
    if [coverage.get("start"), coverage.get("end")] != list(FULL_SEGMENT):
        raise ValueError("full prediction coverage differs from frozen period")
    if len(str(coverage.get("index_sha256") or "")) != 64:
        raise ValueError("full prediction index SHA is missing")
    path = Path(str(entry.get("path") or "")).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_file() or sha256_file(path) != entry.get("prediction_sha256"):
        raise ValueError(f"full prediction artifact mismatch: {path}")
    return entry


def build_preregistered_row(
    frozen: FrozenModel, prediction_manifest: dict, *, protocol_path: str
) -> dict[str, Any]:
    model_ref = frozen.model_ref
    prediction = _full_prediction(prediction_manifest, model_ref)
    return {
        "exp_id": f"strategy-stability-full-period/{model_ref}",
        "direction": "strategy-stability-full-period",
        "phase": "S",
        "date": str(date.today()),
        "state": "preregistered",
        "conclusion": "preregistered",
        "hypothesis": "在统一连续区间复核既有策略的长期绝对收益与自然年稳定性，不用于选型。",
        "baseline_ref": "B1-S v1.0",
        "frozen_model_ref": str(frozen.manifest.get("baseline_ref") or model_ref.upper()),
        "model_ref": model_ref,
        "model_manifest": _relative_or_absolute(frozen.manifest_path),
        "model_path": _relative_or_absolute(frozen.model_path),
        "model_sha256": frozen.model_sha256,
        "source_config": _relative_or_absolute(frozen.source_config),
        "protocol_path": protocol_path,
        "pool": "csi1000",
        "benchmark": POOL_BENCHMARKS["csi1000"],
        "segment": "full",
        "period": list(FULL_SEGMENT),
        "strategy_grid": strategy_grid(model_ref),
        "metric_basis": "after_cost_absolute_return",
        "metrics": list(METRICS),
        "account": ACCOUNT,
        "risk_degree": RISK_DEGREE,
        "fees": dict(EXCHANGE_KWARGS),
        "data_version": prediction_manifest.get("data_version"),
        "prediction_artifacts": [prediction],
        "result_dirs": [],
        "cleanup_retention_eligible": False,
        "note": "全周期回看诊断；不产生胜者，不改变实盘配置。",
    }


def _contains_forbidden_metric(value: Any) -> bool:
    if isinstance(value, dict):
        return any("information_ratio" in str(key).lower() or _contains_forbidden_metric(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_metric(item) for item in value)
    return False


def bind_results(row: dict, result_path: Path) -> dict:
    if row.get("state") != "preregistered":
        raise ValueError("diagnostic results require preregistered state")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    identity = (payload.get("model_ref"), payload.get("pool"), payload.get("segment"))
    if identity != (row.get("model_ref"), "csi1000", "full") or payload.get("period") != list(FULL_SEGMENT):
        raise ValueError("diagnostic result identity mismatch")
    results = payload.get("all_rows") or []
    expected = [item["candidate_id"] for item in row["strategy_grid"]]
    actual = [item.get("candidate_id") for item in results]
    if actual != expected or actual[0] != BASELINE_CANDIDATE_ID:
        raise ValueError("diagnostic candidate order differs from preregistration")
    prediction_sha = row["prediction_artifacts"][0]["prediction_sha256"]
    if any(item.get("source_pred_sha256") != prediction_sha for item in results):
        raise ValueError("diagnostic prediction SHA differs from preregistration")
    if _contains_forbidden_metric(results):
        raise ValueError("relative information metric is forbidden in this diagnostic")
    for item in results:
        if item.get("status") not in {"success", "failed", "invalid"}:
            raise ValueError("unknown diagnostic candidate status")
        if item.get("status") == "success":
            metrics = item.get("full_period") or {}
            for key in METRICS:
                try:
                    value = float(metrics[key])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"missing diagnostic metric {key}") from exc
                if not math.isfinite(value):
                    raise ValueError(f"non-finite diagnostic metric {key}")
    out = dict(row)
    out.update(
        state="complete",
        conclusion="diagnostic_no_selection",
        diagnostic_result_path=_relative_or_absolute(result_path),
        diagnostic_result_sha256=sha256_file(result_path),
        diagnostic_results=results,
        result_dirs=list(dict.fromkeys(item["result_dir"] for item in results if item.get("result_dir"))),
        cleanup_retention_eligible=False,
        note="全周期稳定性诊断完成；未选型，未改变实盘配置。",
    )
    return out


def upsert_diagnostic_row(
    registry: Path, row: dict, *, expected_previous_state: Optional[str]
) -> None:
    rows = load_registry(registry)
    matches = [index for index, item in enumerate(rows) if item.get("exp_id") == row.get("exp_id")]
    if len(matches) > 1:
        raise ValueError(f"duplicate registry exp_id: {row.get('exp_id')}")
    if matches:
        previous = rows[matches[0]]
        if previous.get("state") != expected_previous_state:
            raise ValueError("diagnostic registry state mismatch")
        if not (expected_previous_state == "preregistered" and row.get("state") == "complete"):
            raise ValueError("invalid diagnostic state transition")
        rows[matches[0]] = row
    else:
        if expected_previous_state is not None or row.get("state") != "preregistered":
            raise ValueError("new diagnostic row must be preregistered")
        rows.append(row)
    registry.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry.with_name(registry.name + ".tmp")
    temporary.write_text("".join(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n" for item in rows), encoding="utf-8")
    temporary.replace(registry)


def write_protocol(path: Path) -> None:
    payload = {
        "schema_version": 1,
        "date": str(date.today()),
        "phase": "S",
        "purpose": "retrospective_stability_diagnostic_no_selection",
        "models": {model_ref: {"strategy_grid": strategy_grid(model_ref)} for model_ref in MODEL_REFS},
        "pool": "csi1000",
        "benchmark": POOL_BENCHMARKS["csi1000"],
        "period": list(FULL_SEGMENT),
        "continuous_portfolio": True,
        "natural_year_breakdown": {"partial_years": [2020, 2026]},
        "metric_basis": "after_cost_absolute_return",
        "metrics": list(METRICS),
        "sharpe_risk_free_rate": 0.0,
        "account": ACCOUNT,
        "risk_degree": RISK_DEGREE,
        "fees": dict(EXCHANGE_KWARGS),
        "selection": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    sub = parser.add_subparsers(dest="command", required=True)
    protocol = sub.add_parser("protocol")
    protocol.add_argument("--output", type=Path, required=True)
    pre = sub.add_parser("preregister")
    pre.add_argument("--model-ref", choices=MODEL_REFS, required=True)
    pre.add_argument("--prediction-manifest", type=Path, required=True)
    pre.add_argument("--protocol-path", required=True)
    final = sub.add_parser("finalize")
    final.add_argument("--model-ref", choices=MODEL_REFS, required=True)
    final.add_argument("--result", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    if args.command == "protocol":
        write_protocol(args.output)
        print(args.output)
        return
    exp_id = f"strategy-stability-full-period/{args.model_ref}"
    if args.command == "preregister":
        frozen = load_frozen_model(REPO_ROOT, args.model_ref)
        manifest = json.loads(args.prediction_manifest.read_text(encoding="utf-8"))
        row = build_preregistered_row(frozen, manifest, protocol_path=args.protocol_path)
        previous = None
    else:
        row = next((item for item in load_registry(args.registry) if item.get("exp_id") == exp_id), None)
        if row is None:
            raise ValueError(f"registry row missing: {exp_id}")
        row = bind_results(row, args.result)
        previous = "preregistered"
    upsert_diagnostic_row(args.registry, row, expected_previous_state=previous)
    print(f"{row['exp_id']} -> {row['state']}")


if __name__ == "__main__":
    main()
