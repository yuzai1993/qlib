"""Refresh a10m stability metrics with alpha/beta and promote B3-S baseline."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from phase_s_protocol import CURRENT_STRATEGY_BASELINE_ID, sha256_file  # noqa: E402
from strategy_stability_metrics import summarize_stability  # noqa: E402

REGISTRY = REPO_ROOT / "backtest/experiments/registry.jsonl"
A10M_RESULTS = (
    REPO_ROOT
    / "backtest/experiments/strategy-stability/20260806_full_period_a10m/b6-m/full_results.json"
)
BASELINE_CONFIG = (
    REPO_ROOT
    / "backtest/configs/baseline-strategy/b3-s/topk-t20-d2-h10_csi1000_full.yaml"
)
A10M_EXP_ID = "strategy-stability-full-period/b6-m-a10m"
B3_EXP_ID = "baseline/b3-s-on-b6-m"
B2_EXP_ID = "baseline/b2-s-on-b6-m"


def _load_registry() -> list[dict]:
    return [
        json.loads(line)
        for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_registry(rows: list[dict]) -> None:
    temporary = REGISTRY.with_name(REGISTRY.name + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n" for item in rows
        ),
        encoding="utf-8",
    )
    temporary.replace(REGISTRY)


def _load_report(result_dir: Path) -> pd.DataFrame:
    report = pd.read_csv(
        result_dir / "run_01" / "report_normal.csv", parse_dates=["datetime"]
    )
    return report.set_index("datetime").sort_index()


def refresh_a10m() -> dict:
    payload = json.loads(A10M_RESULTS.read_text(encoding="utf-8"))
    refreshed = []
    for row in payload["all_rows"]:
        result_dir = Path(str(row["result_dir"]))
        if not result_dir.is_absolute():
            result_dir = REPO_ROOT / result_dir
        summary = summarize_stability(_load_report(result_dir))
        updated = dict(row)
        updated.update(summary)
        updated["status"] = "success"
        refreshed.append(updated)
    payload["all_rows"] = refreshed
    payload["metrics_include"] = ["alpha", "beta", "benchmark_cumulative_return"]
    A10M_RESULTS.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return payload


def upsert_a10m_registry(payload: dict, rows: list[dict]) -> list[dict]:
    for index, row in enumerate(rows):
        if row.get("exp_id") != A10M_EXP_ID:
            continue
        updated = dict(row)
        updated["diagnostic_results"] = payload["all_rows"]
        updated["diagnostic_result_path"] = str(
            A10M_RESULTS.relative_to(REPO_ROOT)
        )
        updated["diagnostic_result_sha256"] = sha256_file(A10M_RESULTS)
        updated["account"] = 10_000_000.0
        rows[index] = updated
        return rows
    raise ValueError(f"missing registry row: {A10M_EXP_ID}")


def build_b3_row(payload: dict, figured_result_dir: Path | None) -> dict:
    winner = next(
        item
        for item in payload["all_rows"]
        if item.get("candidate_id") == CURRENT_STRATEGY_BASELINE_ID
    )
    full = winner["full_period"]
    result_dirs = []
    if figured_result_dir is not None:
        result_dirs.append(str(figured_result_dir))
    elif winner.get("result_dir"):
        result_dirs.append(str(winner["result_dir"]))
    return {
        "exp_id": B3_EXP_ID,
        "direction": "baseline-strategy",
        "phase": "S",
        "state": "baseline",
        "date": str(date.today()),
        "conclusion": "baseline",
        "hypothesis": (
            "B3-S v1.0：在冻结 B6-M 分数与 1000 万账户全历史稳定性表上，"
            "按扣费绝对夏普/年化选出的 topk-t20-d2-h10。"
        ),
        "baseline_ref": "B3-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "model_ref": "b6-m",
        "promoted_from": A10M_EXP_ID,
        "selection_pool": "csi1000",
        "selection_segment": ["2020-01-13", "2026-07-31"],
        "selection_metric": "after_cost_absolute_return_sharpe",
        "test_segment": ["2020-01-13", "2026-07-31"],
        "evaluation_mode": "full_history_in_sample",
        "data_version": "2026-07-31",
        "account": 10_000_000.0,
        "strategy": {
            "candidate_id": "topk-t20-d2-h10",
            "strategy_class": "TopkDropoutStrategy",
            "topk": 20,
            "n_drop": 2,
            "hold_thresh": 10,
            "risk_degree": 0.95,
        },
        "metrics_summary": {
            "csi1000_full": {
                "ann": full["annualized_return"],
                "sharpe": full["sharpe_ratio"],
                "alpha": full["alpha"],
                "beta": full["beta"],
                "benchmark_cum": full["benchmark_cumulative_return"],
                "mdd": full["max_drawdown"],
            }
        },
        "configs": [str(BASELINE_CONFIG.relative_to(REPO_ROOT))],
        "config_sha256": sha256_file(BASELINE_CONFIG),
        "result_dirs": result_dirs,
        "cleanup_retention_eligible": True,
        "note": (
            "用户确认将 1000 万账户全历史稳定性最优候选提升为 B3-S；"
            "同步 CSI1000 研究实盘配置。"
        ),
    }


def main() -> None:
    figured = None
    if len(sys.argv) > 1:
        figured = Path(sys.argv[1]).expanduser().resolve()
    payload = refresh_a10m()
    rows = _load_registry()
    rows = upsert_a10m_registry(payload, rows)
    for index, row in enumerate(rows):
        if row.get("exp_id") == B2_EXP_ID:
            demoted = dict(row)
            demoted["cleanup_retention_eligible"] = False
            demoted["note"] = (
                str(demoted.get("note") or "")
                + "；已被 B3-S 取代，仅作历史对照。"
            ).strip("；")
            rows[index] = demoted
    b3 = build_b3_row(payload, figured)
    matches = [i for i, row in enumerate(rows) if row.get("exp_id") == B3_EXP_ID]
    if matches:
        rows[matches[0]] = b3
    else:
        rows.append(b3)
    _write_registry(rows)
    print(f"refreshed: {A10M_RESULTS}")
    print(f"promoted: {B3_EXP_ID}")
    winner = next(
        item
        for item in payload["all_rows"]
        if item["candidate_id"] == CURRENT_STRATEGY_BASELINE_ID
    )
    full = winner["full_period"]
    print(
        "B3-S metrics:",
        {k: full[k] for k in ("annualized_return", "sharpe_ratio", "alpha", "beta", "benchmark_cumulative_return")},
    )


if __name__ == "__main__":
    main()
