"""Verify current B6-M predictions and rebuild the B2-S baseline anchor."""

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
from phase_s_protocol import select_valid_winner, sha256_file  # noqa: E402
from register_phase_s_experiment import (  # noqa: E402
    build_strategy_baseline_promotion,
    load_registry,
    upsert_baseline_anchor_row,
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
    refresh_prediction_manifest(manifest_path)
    rows = load_registry(registry)
    sweep = next(
        (
            row
            for row in rows
            if row.get("exp_id") == "strategy-sweep/b6-m"
        ),
        None,
    )
    if sweep is None:
        raise ValueError("registry row missing: strategy-sweep/b6-m")
    existing = next(
        (row for row in rows if row.get("exp_id") == "baseline/b2-s-on-b6-m"),
        None,
    )
    baseline = build_strategy_baseline_promotion(
        sweep,
        baseline_ref="B2-S v1.0",
        promotion_date=existing.get("date") if existing else None,
    )
    upsert_baseline_anchor_row(registry, baseline)


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
    print("B6-M predictions verified and B2-S baseline anchor refreshed")


if __name__ == "__main__":
    main()
