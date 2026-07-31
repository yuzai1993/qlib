"""Refresh Phase S audit artifacts after metric/provenance schema hardening."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from eval_protocol import yearly_ir  # noqa: E402
from generate_phase_s_predictions import prediction_index_sha256  # noqa: E402
from phase_s_protocol import MODEL_REFS, select_valid_winner, sha256_file  # noqa: E402
from register_phase_s_experiment import (  # noqa: E402
    bind_test_results,
    bind_valid_results,
    build_phase_s_baseline_anchor,
    build_preregistered_row,
    load_frozen_model,
    load_registry,
)
from run_strategy_sweep import (  # noqa: E402
    ANN_KEY,
    IR_KEY,
    MDD_KEY,
    classify_strategy_outcome,
)

METRIC_KEYS = (IR_KEY, ANN_KEY, MDD_KEY, "annualized_one_way_turnover")


def _finite_json(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite_json(item) for item in value]
    return value


def normalize_result_row(
    row: dict[str, Any], *, yearly: Optional[dict[str, float]]
) -> dict[str, Any]:
    out = copy.deepcopy(row)
    classify_strategy_outcome(out)
    out.pop("yearly_ir", None)
    if yearly is not None and out.get("status") == "success":
        out["yearly_ir"] = dict(yearly)
    return _finite_json(out)


def refresh_comparison(
    payload: dict[str, Any], *, recompute_yearly: bool, prediction_entry: dict[str, Any]
) -> dict[str, Any]:
    rows = []
    for row in payload.get("all_rows") or []:
        row = copy.deepcopy(row)
        result_dir = Path(str(row.get("result_dir") or ""))
        meta_path = result_dir / "meta.json"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("source_pred_sha256") != prediction_entry["prediction_sha256"]:
                raise ValueError(f"session prediction SHA mismatch: {result_dir}")
            row["provenance_verification"] = "session_meta_verified"
        else:
            row["provenance_verification"] = "manifest_backfill_after_valid_cleanup"
        row["source_pred"] = prediction_entry["path"]
        row["source_pred_sha256"] = prediction_entry["prediction_sha256"]
        yearly = None
        if recompute_yearly and row.get("result_dir"):
            report = Path(row["result_dir"]) / "run_01" / "report_normal.csv"
            yearly = {
                str(year): float(value) for year, value in yearly_ir(report).items()
            }
        rows.append(normalize_result_row(row, yearly=yearly))
    successful = [row for row in rows if row.get("status") == "success"]
    out = copy.deepcopy(payload)
    out["all_rows"] = rows
    out["ranked"] = sorted(
        successful,
        key=lambda row: float(row[IR_KEY]),
        reverse=True,
    )
    baseline_id = "topk-t10-d2-h1"
    out["baseline"] = next(row for row in rows if row.get("candidate_id") == baseline_id)
    if payload.get("segment") == "valid":
        out["winner"] = select_valid_winner(rows)
    return _finite_json(out)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def refresh_prediction_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for entry in manifest.get("predictions") or []:
        pred_path = Path(entry["path"])
        if not pred_path.is_absolute():
            pred_path = REPO_ROOT / pred_path
        if sha256_file(pred_path) != entry.get("prediction_sha256"):
            raise ValueError(f"frozen prediction SHA mismatch: {pred_path}")
        frame = pd.read_pickle(pred_path)
        entry.setdefault("coverage", {})["index_sha256"] = prediction_index_sha256(frame.index)
    _write_json(path, manifest)
    return manifest


def refresh_all(root: Path, registry: Path) -> None:
    experiment_root = root / "backtest/experiments/strategy/20260801_b1_b6"
    manifest_path = experiment_root / "prediction_manifest.json"
    manifest = refresh_prediction_manifest(manifest_path)
    rebuilt = [
        row
        for row in load_registry(registry)
        if row.get("exp_id")
        not in {
            "strategy-sweep/b1-m",
            "strategy-sweep/b6-m",
            "baseline/b1-s-on-b1-m",
            "baseline/b1-s-on-b6-m",
        }
    ]
    for model_ref in MODEL_REFS:
        model_root = experiment_root / model_ref
        valid_path = model_root / "valid_results.json"
        valid_payload = refresh_comparison(
            json.loads(valid_path.read_text(encoding="utf-8")),
            recompute_yearly=False,
            prediction_entry=next(
                item for item in manifest["predictions"]
                if item["model_ref"] == model_ref and item["pool"] == "csi1000" and item["segment"] == "valid"
            ),
        )
        _write_json(valid_path, valid_payload)
        attempt_path = model_root / "valid_results_attempt1.json"
        if attempt_path.is_file():
            _write_json(
                attempt_path,
                refresh_comparison(
                    json.loads(attempt_path.read_text(encoding="utf-8")),
                    recompute_yearly=False,
                    prediction_entry=next(
                        item for item in manifest["predictions"]
                        if item["model_ref"] == model_ref and item["pool"] == "csi1000" and item["segment"] == "valid"
                    ),
                ),
            )
        pool_payloads = {}
        for pool in ("csi1000", "csi300", "csi500"):
            pool_path = model_root / f"test_{pool}.json"
            refreshed = refresh_comparison(
                json.loads(pool_path.read_text(encoding="utf-8")),
                recompute_yearly=True,
                prediction_entry=next(
                    item for item in manifest["predictions"]
                    if item["model_ref"] == model_ref and item["pool"] == pool and item["segment"] == "test"
                ),
            )
            _write_json(pool_path, refreshed)
            pool_payloads[pool] = refreshed
        test_path = model_root / "test_results.json"
        _write_json(
            test_path,
            {"schema_version": 1, "model_ref": model_ref, "pools": pool_payloads},
        )

        frozen = load_frozen_model(root, model_ref)
        sweep = build_preregistered_row(
            frozen,
            manifest,
            protocol_path="backtest/experiments/strategy/20260801_b1_b6/protocol.json",
        )
        sweep = bind_valid_results(sweep, valid_path)
        sweep = bind_test_results(sweep, test_path)
        rebuilt.extend([build_phase_s_baseline_anchor(sweep), sweep])

    temporary = registry.with_name(registry.name + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
            for row in rebuilt
        ),
        encoding="utf-8",
    )
    temporary.replace(registry)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--registry", type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    root = args.repo_root.resolve()
    registry = args.registry or root / "backtest/experiments/registry.jsonl"
    refresh_all(root, registry)
    print("Phase S artifacts refreshed with cost-aware yearly IR and enforced provenance")


if __name__ == "__main__":
    main()
