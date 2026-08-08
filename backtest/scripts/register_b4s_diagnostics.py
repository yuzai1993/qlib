"""Register B4-S account / beta / extended-history diagnostic rows into registry."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from phase_s_protocol import sha256_file  # noqa: E402
from strategy_stability_metrics import summarize_stability  # noqa: E402

REGISTRY = REPO_ROOT / "backtest/experiments/registry.jsonl"


def _load() -> list[dict]:
    return [
        json.loads(line)
        for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write(rows: list[dict]) -> None:
    temporary = REGISTRY.with_name(REGISTRY.name + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n" for item in rows
        ),
        encoding="utf-8",
    )
    temporary.replace(REGISTRY)


def _upsert(rows: list[dict], row: dict) -> list[dict]:
    for index, item in enumerate(rows):
        if item.get("exp_id") == row["exp_id"]:
            rows[index] = row
            return rows
    rows.append(row)
    return rows


def _summary(result_dir: Path) -> dict:
    report = pd.read_csv(
        result_dir / "run_01" / "report_normal.csv", parse_dates=["datetime"]
    ).set_index("datetime")
    return summarize_stability(report)


def register_account(result_dir: Path, account: float) -> dict:
    summary = _summary(result_dir)
    full = summary["full_period"]
    return {
        "exp_id": "strategy-account-diag/b4s-a1m",
        "direction": "strategy-account-diag",
        "phase": "S",
        "state": "complete",
        "date": str(date.today()),
        "conclusion": "diagnostic_no_selection",
        "hypothesis": "B4-S 在 100 万账户下的扣费绝对收益是否与 1000 万锚点量级一致。",
        "baseline_ref": "B4-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "evaluation_mode": "full_history_in_sample",
        "selection_segment": ["2020-01-13", "2026-07-31"],
        "test_segment": ["2020-01-13", "2026-07-31"],
        "account": float(account),
        "strategy": {
            "candidate_id": "topk-t22-d2-h2",
            "topk": 22,
            "n_drop": 2,
            "hold_thresh": 2,
            "risk_degree": 0.90,
        },
        "full_period": full,
        "years": summary.get("years") or {},
        "metrics_summary": {"csi1000_full": full},
        "result_dirs": [str(result_dir)],
        "cleanup_retention_eligible": False,
        "note": "账户规模诊断；不参与选型。",
    }


def register_extended(result_dir: Path, exp_id: str, start: str, end: str) -> dict:
    summary = _summary(result_dir)
    full = summary["full_period"]
    report_html = str(result_dir / "run_01" / "report.html")
    return {
        "exp_id": exp_id,
        "direction": "strategy-extended-history",
        "phase": "S",
        "state": "complete",
        "date": str(date.today()),
        "conclusion": "diagnostic_no_selection",
        "hypothesis": f"B4-S 在扩展历史 [{start},{end}] 上的表现诊断（带图）。",
        "baseline_ref": "B4-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "evaluation_mode": "extended_history_in_sample",
        "selection_segment": [start, end],
        "test_segment": [start, end],
        "account": 10_000_000.0,
        "strategy": {
            "candidate_id": "topk-t22-d2-h2",
            "topk": 22,
            "n_drop": 2,
            "hold_thresh": 2,
            "risk_degree": 0.90,
        },
        "full_period": full,
        "years": summary.get("years") or {},
        "metrics_summary": {"csi1000_full": full},
        "report_html": report_html,
        "result_dirs": [str(result_dir)],
        "cleanup_retention_eligible": False,
        "note": "扩展历史诊断；非默认选型口径。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=("account", "extended"))
    parser.add_argument("--result-dir", required=True, type=Path)
    parser.add_argument("--account", type=float, default=1_000_000)
    parser.add_argument("--exp-id", default="")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="2026-07-31")
    args = parser.parse_args()
    result_dir = args.result_dir.resolve()
    if args.kind == "account":
        row = register_account(result_dir, args.account)
    else:
        if not args.exp_id or not args.start:
            raise SystemExit("extended requires --exp-id and --start")
        row = register_extended(result_dir, args.exp_id, args.start, args.end)
    rows = _upsert(_load(), row)
    _write(rows)
    print(f"registered {row['exp_id']}")


if __name__ == "__main__":
    main()
