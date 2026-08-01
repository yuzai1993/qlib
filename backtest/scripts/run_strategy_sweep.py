"""Run a preregistered model-aware Phase S strategy sweep on frozen predictions."""

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

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import RESULT_ROOT, load_config  # noqa: E402
from eval_protocol import yearly_ir  # noqa: E402
from phase_s_protocol import (  # noqa: E402
    ACCOUNT,
    BASELINE_CANDIDATE_ID,
    EXCHANGE_KWARGS,
    FULL_SEGMENT,
    MODEL_REFS,
    POOL_BENCHMARKS,
    RISK_DEGREE,
    TEST_SEGMENT,
    VALID_SEGMENT,
    select_valid_winner,
    sha256_file,
    strategy_grid,
)
from report_utils import make_session_dir  # noqa: E402

IR_KEY = "excess_with_cost_information_ratio"
ANN_KEY = "excess_with_cost_annualized_return"
MDD_KEY = "excess_with_cost_max_drawdown"


def classify_strategy_outcome(row: dict[str, Any]) -> None:
    """Treat non-finite strategy metrics as invalid outcomes, not successes."""
    if row.get("status") != "success":
        return
    required = (IR_KEY, ANN_KEY, MDD_KEY, "annualized_one_way_turnover")
    invalid = []
    for key in required:
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            invalid.append(key)
            continue
        if not math.isfinite(value):
            invalid.append(key)
    if invalid:
        row.update(
            status="invalid",
            error=f"non-finite strategy metrics: {', '.join(invalid)}",
        )


def verify_prediction_contract(
    pred_path: Path,
    manifest_path: Path,
    *,
    model_ref: str,
    pool: str,
    segment: str,
) -> dict[str, Any]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    matches = [
        entry
        for entry in manifest.get("predictions") or []
        if (entry.get("model_ref"), entry.get("pool"), entry.get("segment"))
        == (model_ref, pool, segment)
    ]
    if len(matches) != 1:
        raise ValueError("prediction manifest must contain exactly one matching artifact")
    entry = matches[0]
    declared = Path(entry["path"]).expanduser()
    if not declared.is_absolute():
        declared = REPO_ROOT / declared
    if declared.resolve() != pred_path.resolve():
        raise ValueError("prediction path differs from frozen manifest")
    actual_sha = sha256_file(pred_path)
    if actual_sha != entry.get("prediction_sha256"):
        raise ValueError("prediction SHA-256 differs from frozen manifest")
    return dict(entry)


def _segment_bounds(segment: str) -> tuple[str, str]:
    if segment == "valid":
        return VALID_SEGMENT
    if segment == "test":
        return TEST_SEGMENT
    if segment == "full":
        return FULL_SEGMENT
    raise ValueError(f"unsupported Phase S segment: {segment}")


def build_sweep_config(
    base: dict[str, Any],
    candidate: dict[str, Any],
    *,
    pool: str,
    segment: str,
) -> dict[str, Any]:
    """Build one exact pred-only config from the frozen protocol."""
    if pool not in POOL_BENCHMARKS:
        raise ValueError(f"unsupported Phase S pool: {pool}")
    start, end = _segment_bounds(segment)
    config = copy.deepcopy(base)
    config.pop("_config_path", None)
    config["run"] = {
        "mode": "pred_backtest",
        "note": f"phase_s_{candidate['candidate_id']}_{pool}_{segment}",
        "n_runs": 1,
        "generate_figures": False,
    }
    config["phase_s"] = {
        "candidate_id": candidate["candidate_id"],
        "selection_segment": segment,
        "pool": pool,
    }
    config["data"]["instruments"] = pool
    config["data"]["benchmark"] = POOL_BENCHMARKS[pool]
    config["data"]["handler"]["end_time"] = end
    config["segments"]["valid"] = list(VALID_SEGMENT)
    config["segments"]["test"] = [start, end]
    config["backtest"] = {
        "account": ACCOUNT,
        "exchange_kwargs": copy.deepcopy(EXCHANGE_KWARGS),
    }
    if candidate["strategy_class"] == "TopkDropoutStrategy":
        config["strategy"] = {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy.signal_strategy",
            "topk": candidate["topk"],
            "n_drop": candidate["n_drop"],
            "hold_thresh": candidate["hold_thresh"],
            "kwargs": {
                "risk_degree": RISK_DEGREE,
                "only_tradable": False,
                "forbid_all_trade_at_limit": False,
            },
        }
    elif candidate["strategy_class"] == "SoftTopkStrategy":
        config["strategy"] = {
            "class": "SoftTopkStrategy",
            "module_path": "qlib.contrib.strategy.cost_control",
            "topk": candidate["topk"],
            "kwargs": {
                "trade_impact_limit": candidate["trade_impact_limit"],
                "risk_degree": RISK_DEGREE,
            },
        }
    else:
        raise ValueError(f"unsupported strategy class: {candidate['strategy_class']}")
    return config


def build_backtest_command(
    python: Path,
    script: Path,
    pred: Path,
    config: Path,
    note: str,
) -> list[str]:
    return [
        str(python),
        str(script),
        "--pred",
        str(pred),
        "--config",
        str(config),
        "--note",
        note,
        "--skip-pred-copy",
    ]


def _parse_result_dir(stdout: str) -> Path:
    for line in reversed(stdout.splitlines()):
        if line.startswith("结果目录:"):
            return Path(line.split(":", 1)[1].strip())
    raise RuntimeError(f"run_pred_backtest.py did not report a result directory:\n{stdout}")


def _params_text(row: dict[str, Any]) -> str:
    if row["strategy_class"] == "TopkDropoutStrategy":
        return (
            f"topk={row['topk']}, n_drop={row['n_drop']}, "
            f"hold_thresh={row['hold_thresh']}"
        )
    return (
        f"topk={row['topk']}, "
        f"trade_impact_limit={row['trade_impact_limit']:.8f}"
    )


def merge_retry_rows(
    existing_rows: list[dict[str, Any]], retry_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace retried candidates while retaining prior failed-attempt evidence."""
    retries = {row["candidate_id"]: copy.deepcopy(row) for row in retry_rows}
    merged: list[dict[str, Any]] = []
    for existing in existing_rows:
        candidate_id = existing["candidate_id"]
        if candidate_id not in retries:
            merged.append(copy.deepcopy(existing))
            continue
        replacement = retries.pop(candidate_id)
        attempts = copy.deepcopy(existing.get("previous_attempts", []))
        attempts.append(
            {
                key: copy.deepcopy(value)
                for key, value in existing.items()
                if key in {"status", "error", "result_dir", "returncode", "config"}
            }
        )
        replacement["previous_attempts"] = attempts
        merged.append(replacement)
    merged.extend(retries.values())
    return merged


def write_comparison(
    out_dir: Path,
    rows: list[dict[str, Any]],
    *,
    model_ref: str,
    pool: str,
    segment: str,
) -> dict[str, Any]:
    successful = [row for row in rows if row.get("status") == "success"]
    baseline = next(
        (row for row in successful if row["candidate_id"] == BASELINE_CANDIDATE_ID),
        None,
    )
    if baseline is None:
        raise RuntimeError(f"baseline candidate failed or is missing for {model_ref}/{pool}/{segment}")
    ranked = sorted(
        successful,
        key=lambda row: float(row.get(IR_KEY, float("-inf"))),
        reverse=True,
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model_ref": model_ref,
        "pool": pool,
        "segment": segment,
        "baseline": baseline,
        "ranked": ranked,
        "all_rows": rows,
    }
    if segment == "valid":
        payload["winner"] = select_valid_winner(rows)
    (out_dir / "comparison.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        f"# {model_ref} {pool} {segment} Phase S",
        "",
        f"- 基线: `{BASELINE_CANDIDATE_ID}`",
    ]
    if payload.get("winner"):
        lines.append(f"- valid 胜者: `{payload['winner']['candidate_id']}`")
    lines.extend(
        [
            "",
            "| 排名 | 候选 | 参数 | IR | 年化超额 | 最大回撤 | 年化单边换手 | 结果目录 |",
            "|---:|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for index, row in enumerate(ranked, 1):
        lines.append(
            f"| {index} | {row['candidate_id']} | {_params_text(row)} | "
            f"{float(row[IR_KEY]):.4f} | {float(row[ANN_KEY]):.2%} | "
            f"{float(row[MDD_KEY]):.2%} | "
            f"{float(row['annualized_one_way_turnover']):.4f} | "
            f"`{row['result_dir']}` |"
        )
    failures = [row for row in rows if row.get("status") != "success"]
    if failures:
        lines.extend(["", "## 失败候选", ""])
        lines.extend(
            f"- `{row['candidate_id']}`: {row.get('error', 'unknown error')}"
            for row in failures
        )
    (out_dir / "COMPARISON.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase S frozen-prediction strategy sweep")
    parser.add_argument("--pred", required=True, type=Path)
    parser.add_argument("--prediction-manifest", required=True, type=Path)
    parser.add_argument("--config", required=True)
    parser.add_argument("--model-ref", required=True, choices=MODEL_REFS)
    parser.add_argument("--pool", choices=tuple(POOL_BENCHMARKS), default="csi1000")
    parser.add_argument("--segment", choices=("valid", "test"), default="valid")
    parser.add_argument("--candidate-id", action="append")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--configs-dir", type=Path)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument(
        "--resume-summary",
        type=Path,
        help="retry failed rows from an existing comparison and preserve attempt history",
    )
    args = parser.parse_args(argv)
    if args.segment == "test" and not args.candidate_id:
        parser.error("test requires frozen --candidate-id values")
    return args


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    pred_path = args.pred.expanduser().resolve()
    if not pred_path.is_file():
        raise FileNotFoundError(f"prediction file missing: {pred_path}")
    prediction_entry = verify_prediction_contract(
        pred_path,
        args.prediction_manifest,
        model_ref=args.model_ref,
        pool=args.pool,
        segment=args.segment,
    )
    base = load_config(args.config)
    all_candidates = strategy_grid(args.model_ref)
    by_id = {row["candidate_id"]: row for row in all_candidates}
    existing_payload = None
    if args.resume_summary:
        existing_payload = json.loads(args.resume_summary.read_text(encoding="utf-8"))
        if existing_payload.get("model_ref") != args.model_ref:
            raise ValueError("resume summary model_ref does not match")
    candidate_ids = args.candidate_id
    if not candidate_ids and existing_payload is not None:
        candidate_ids = [
            row["candidate_id"]
            for row in existing_payload["all_rows"]
            if row.get("status") != "success"
        ]
    candidate_ids = candidate_ids or [row["candidate_id"] for row in all_candidates]
    unknown = sorted(set(candidate_ids) - set(by_id))
    if unknown:
        raise ValueError(f"unknown candidate IDs for {args.model_ref}: {unknown}")
    candidates = [by_id[candidate_id] for candidate_id in candidate_ids]

    out_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else make_session_dir(
            RESULT_ROOT,
            note=f"phase_s_{args.model_ref}_{args.pool}_{args.segment}",
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    configs_dir = (
        args.configs_dir.resolve()
        if args.configs_dir
        else REPO_ROOT / "backtest/configs/strategy-sweep" / args.model_ref
    )
    configs_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    for index, candidate in enumerate(candidates, 1):
        candidate_id = candidate["candidate_id"]
        config_path = configs_dir / f"{candidate_id}_{args.pool}_{args.segment}.yaml"
        config_path.write_text(
            yaml.safe_dump(
                build_sweep_config(
                    base,
                    candidate,
                    pool=args.pool,
                    segment=args.segment,
                ),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        note = f"phase_s_{args.model_ref}_{candidate_id}_{args.pool}_{args.segment}"
        command = build_backtest_command(
            Path(sys.executable),
            SCRIPT_DIR / "run_pred_backtest.py",
            pred_path,
            config_path,
            note,
        )
        print(f"[{index}/{len(candidates)}] {candidate_id}", flush=True)
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        row = dict(candidate)
        row["source_pred"] = str(pred_path)
        row["source_pred_sha256"] = prediction_entry["prediction_sha256"]
        row["returncode"] = completed.returncode
        row["config"] = str(config_path)
        try:
            result_dir = _parse_result_dir(completed.stdout)
            row["result_dir"] = str(result_dir)
            metrics_path = result_dir / "run_01" / "metrics.json"
            row.update(json.loads(metrics_path.read_text(encoding="utf-8")))
            classify_strategy_outcome(row)
            if row.get("status") == "success":
                yearly = yearly_ir(result_dir / "run_01" / "report_normal.csv")
                row["yearly_ir"] = {str(year): float(value) for year, value in yearly.items()}
        except Exception as exc:
            row.update(status="failed", error=f"{exc}\n{completed.stderr[-2000:]}")
        if completed.returncode != 0:
            row.update(
                status="failed",
                error=completed.stderr[-2000:] or row.get("error", "backtest subprocess failed"),
            )
        rows.append(row)

    if existing_payload is not None:
        rows = merge_retry_rows(existing_payload["all_rows"], rows)
    payload = write_comparison(
        out_dir,
        rows,
        model_ref=args.model_ref,
        pool=args.pool,
        segment=args.segment,
    )
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"结果目录: {out_dir}")
    if payload.get("winner"):
        print(f"valid 胜者: {payload['winner']['candidate_id']}")


if __name__ == "__main__":
    main()
