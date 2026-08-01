"""Run all frozen Phase S strategies as a non-selecting full-period diagnostic."""

from __future__ import annotations

import argparse
import copy
import json
import math
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import RESULT_ROOT, load_config  # noqa: E402
from phase_s_protocol import BASELINE_CANDIDATE_ID, MODEL_REFS, strategy_grid  # noqa: E402
from report_utils import make_session_dir  # noqa: E402
from run_strategy_sweep import (  # noqa: E402
    _parse_result_dir,
    build_backtest_command,
    build_sweep_config,
    merge_retry_rows,
    verify_prediction_contract,
)
from strategy_stability_metrics import (  # noqa: E402
    IncompletePortfolioError,
    summarize_stability,
)

REQUESTED_METRICS = (
    "annualized_return",
    "sharpe_ratio",
    "calmar_ratio",
    "annualized_volatility",
    "max_drawdown",
    "annualized_one_way_turnover",
)


def build_stability_config(
    base: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    config = build_sweep_config(base, candidate, pool="csi1000", segment="full")
    config["run"]["note"] = f"strategy_stability_{candidate['candidate_id']}"
    config["phase_s"]["diagnostic"] = "full_period_stability"
    return config


def classify_diagnostic_outcome(row: dict[str, Any]) -> None:
    if row.get("status") != "success":
        return
    full = row.get("full_period") or {}
    invalid = []
    for key in REQUESTED_METRICS:
        try:
            value = float(full.get(key))
        except (TypeError, ValueError):
            invalid.append(key)
            continue
        if not math.isfinite(value):
            invalid.append(key)
    if invalid:
        row.update(
            status="invalid",
            error=f"non-finite diagnostic metrics: {', '.join(invalid)}",
        )


def classify_diagnostic_exception(row: dict[str, Any], exc: Exception) -> None:
    row.update(
        status="invalid" if isinstance(exc, IncompletePortfolioError) else "failed",
        error=str(exc),
    )


def _finite_json(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite_json(item) for item in value]
    return value


def build_diagnostic_payload(
    model_ref: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    expected = [candidate["candidate_id"] for candidate in strategy_grid(model_ref)]
    by_id = {row.get("candidate_id"): row for row in rows}
    if set(by_id) != set(expected) or len(rows) != len(expected):
        raise ValueError("diagnostic candidate set differs from frozen grid")
    ordered = [_finite_json(copy.deepcopy(by_id[candidate_id])) for candidate_id in expected]
    if ordered[0]["candidate_id"] != BASELINE_CANDIDATE_ID:
        raise ValueError("B1-S baseline must be the first diagnostic row")
    return {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model_ref": model_ref,
        "pool": "csi1000",
        "segment": "full",
        "period": ["2020-01-13", "2026-07-31"],
        "metric_basis": "after_cost_absolute_return",
        "all_rows": ordered,
    }


def _load_report(result_dir: Path) -> pd.DataFrame:
    report = pd.read_csv(
        result_dir / "run_01" / "report_normal.csv", parse_dates=["datetime"]
    )
    return report.set_index("datetime").sort_index()


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        f"# {payload['model_ref'].upper()} full-period stability diagnostic",
        "",
        "| 候选 | 状态 | 扣费年化 | 夏普 | 卡玛 | 年化波动 | 最大回撤 | 年化单边换手 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["all_rows"]:
        full = row.get("full_period") or {}
        def value(key: str) -> str:
            raw = full.get(key)
            return "—" if raw is None else f"{float(raw):.4f}"
        lines.append(
            f"| {row['candidate_id']} | {row.get('status')} | {value('annualized_return')} | "
            f"{value('sharpe_ratio')} | {value('calmar_ratio')} | "
            f"{value('annualized_volatility')} | {value('max_drawdown')} | "
            f"{value('annualized_one_way_turnover')} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pred", required=True, type=Path)
    parser.add_argument("--prediction-manifest", required=True, type=Path)
    parser.add_argument("--config", required=True)
    parser.add_argument("--model-ref", required=True, choices=MODEL_REFS)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--configs-dir", type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--resume-summary", type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    pred_path = args.pred.expanduser().resolve()
    if not pred_path.is_file():
        raise FileNotFoundError(f"prediction file missing: {pred_path}")
    prediction_entry = verify_prediction_contract(
        pred_path,
        args.prediction_manifest,
        model_ref=args.model_ref,
        pool="csi1000",
        segment="full",
    )
    base = load_config(args.config)
    candidates = strategy_grid(args.model_ref)
    existing_payload = None
    if args.resume_summary:
        existing_payload = json.loads(args.resume_summary.read_text(encoding="utf-8"))
        if existing_payload.get("model_ref") != args.model_ref:
            raise ValueError("resume summary model_ref does not match")
        failed_ids = {
            row["candidate_id"]
            for row in existing_payload.get("all_rows") or []
            if row.get("status") != "success"
        }
        candidates = [row for row in candidates if row["candidate_id"] in failed_ids]
    out_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else make_session_dir(RESULT_ROOT, note=f"strategy_stability_{args.model_ref}")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    configs_dir = (
        args.configs_dir.resolve()
        if args.configs_dir
        else REPO_ROOT / "backtest/configs/strategy-stability" / args.model_ref
    )
    configs_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, candidate in enumerate(candidates, 1):
        candidate_id = candidate["candidate_id"]
        config_path = configs_dir / f"{candidate_id}_csi1000_full.yaml"
        config_path.write_text(
            yaml.safe_dump(
                build_stability_config(base, candidate),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        note = f"strategy_stability_{args.model_ref}_{candidate_id}_csi1000_full"
        command = build_backtest_command(
            Path(sys.executable),
            SCRIPT_DIR / "run_pred_backtest.py",
            pred_path,
            config_path,
            note,
        )
        print(f"[{index}/{len(candidates)}] {candidate_id}", flush=True)
        completed = subprocess.run(
            command, cwd=REPO_ROOT, text=True, capture_output=True
        )
        row = {
            **candidate,
            "status": "failed",
            "returncode": completed.returncode,
            "config": str(config_path),
            "source_pred": str(pred_path),
            "source_pred_sha256": prediction_entry["prediction_sha256"],
        }
        try:
            result_dir = _parse_result_dir(completed.stdout)
            row["result_dir"] = str(result_dir)
            meta = json.loads((result_dir / "meta.json").read_text(encoding="utf-8"))
            if meta.get("source_pred_sha256") != prediction_entry["prediction_sha256"]:
                raise ValueError("session prediction SHA differs from diagnostic manifest")
            row.update(status="success", **summarize_stability(_load_report(result_dir)))
            classify_diagnostic_outcome(row)
        except Exception as exc:
            classify_diagnostic_exception(row, exc)
            if completed.stderr:
                row["error"] += f"\n{completed.stderr[-2000:]}"
        if completed.returncode != 0:
            row.update(status="failed", error=completed.stderr[-2000:] or "backtest subprocess failed")
        rows.append(row)
    if existing_payload is not None:
        rows = merge_retry_rows(existing_payload["all_rows"], rows)
    payload = build_diagnostic_payload(args.model_ref, rows)
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_markdown(out_dir / "COMPARISON.md", payload)
    print(f"结果目录: {out_dir}")


if __name__ == "__main__":
    main()
