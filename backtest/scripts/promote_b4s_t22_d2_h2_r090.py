"""Promote topk-t22-d2-h2 (risk 0.90) to B4-S baseline after neighborhood P25 review."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from phase_s_protocol import sha256_file  # noqa: E402
from strategy_neighborhood_protocol import (  # noqa: E402
    score_valid_candidates,
    strategy_neighborhood_grid,
)
from strategy_stability_metrics import summarize_stability  # noqa: E402

REGISTRY = REPO_ROOT / "backtest/experiments/registry.jsonl"
BASELINE_CONFIG = (
    REPO_ROOT / "backtest/configs/baseline-strategy/b4-s/topk-t22-d2-h2_csi1000_full.yaml"
)
NEIGHBORHOOD_RESULTS = (
    REPO_ROOT
    / "backtest/experiments/strategy-neighborhood/20260807_b3s_local_full/full_results.json"
)
CANDIDATE_ID = "topk-t22-d2-h2"
NEIGHBORHOOD_ID = "topk-t22-d2-h2-r090"
B4_EXP_ID = "baseline/b4-s-on-b6-m"
B3_EXP_ID = "baseline/b3-s-on-b6-m"
MIN_P25 = 0.0  # gate is presence + user review; value must be finite and logged


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


def _neighborhood_row() -> dict:
    payload = json.loads(NEIGHBORHOOD_RESULTS.read_text(encoding="utf-8"))
    scored, _ = score_valid_candidates(payload["all_rows"], strategy_neighborhood_grid())
    row = next(item for item in scored if item["candidate_id"] == NEIGHBORHOOD_ID)
    p25 = float(row["neighbor_ir_p25"])
    if not (p25 == p25) or p25 < MIN_P25:
        raise ValueError(f"neighbor_ir_p25 gate failed for {NEIGHBORHOOD_ID}: {p25}")
    return row


def build_b4_row(figured_result_dir: Path, neighborhood: dict) -> dict:
    summary = summarize_stability(_load_report(figured_result_dir))
    full = summary["full_period"]
    return {
        "exp_id": B4_EXP_ID,
        "direction": "baseline-strategy",
        "phase": "S",
        "state": "baseline",
        "date": str(date.today()),
        "conclusion": "baseline",
        "hypothesis": (
            "B4-S v1.0：在 B3-S 邻域 540 网格上按邻域 IR P25 审查后，"
            "用户选定 topk-t22-d2-h2-r090 提升为研究策略基线。"
        ),
        "baseline_ref": "B4-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "model_ref": "b6-m",
        "promoted_from": "strategy-neighborhood/b3-s-local-full-v1",
        "selection_pool": "csi1000",
        "selection_segment": ["2020-01-13", "2026-07-31"],
        "selection_metric": "axial_neighbor_excess_with_cost_ir_p25",
        "test_segment": ["2020-01-13", "2026-07-31"],
        "evaluation_mode": "full_history_in_sample",
        "data_version": "2026-07-31",
        "account": 10_000_000.0,
        "strategy": {
            "candidate_id": CANDIDATE_ID,
            "neighborhood_candidate_id": NEIGHBORHOOD_ID,
            "strategy_class": "TopkDropoutStrategy",
            "topk": 22,
            "n_drop": 2,
            "hold_thresh": 2,
            "risk_degree": 0.90,
        },
        "neighbor_ir_p25": float(neighborhood["neighbor_ir_p25"]),
        "neighbor_count": int(neighborhood["neighbor_count"]),
        "metrics_summary": {
            "csi1000_full": {
                "ann": full.get("annualized_return"),
                "sharpe": full.get("sharpe_ratio"),
                "alpha": full.get("alpha"),
                "beta": full.get("beta"),
                "benchmark_cum": full.get("benchmark_cumulative_return"),
                "mdd": full.get("max_drawdown"),
                "calmar": full.get("calmar_ratio"),
                "vol": full.get("annualized_volatility"),
                "turnover": full.get("annualized_one_way_turnover"),
                "neighbor_ir_p25": float(neighborhood["neighbor_ir_p25"]),
            }
        },
        "full_period": full,
        "years": summary.get("years") or {},
        "positive_complete_years": summary.get("positive_complete_years"),
        "complete_year_sharpe_median": summary.get("complete_year_sharpe_median"),
        "worst_complete_year_max_drawdown": summary.get(
            "worst_complete_year_max_drawdown"
        ),
        "configs": [str(BASELINE_CONFIG.relative_to(REPO_ROOT))],
        "config_sha256": sha256_file(BASELINE_CONFIG),
        "result_dirs": [str(figured_result_dir)],
        "cleanup_retention_eligible": True,
        "note": (
            "晋升前已审查邻域 IR P25="
            f"{float(neighborhood['neighbor_ir_p25']):.4f}；"
            "同步 CSI1000 研究实盘配置。"
        ),
    }


def demote_b3(rows: list[dict]) -> list[dict]:
    for index, row in enumerate(rows):
        if row.get("exp_id") != B3_EXP_ID:
            continue
        updated = dict(row)
        updated["cleanup_retention_eligible"] = False
        note = str(updated.get("note") or "")
        tag = "；已被 B4-S 取代，仅作历史对照。"
        if tag not in note:
            updated["note"] = note + tag
        rows[index] = updated
        return rows
    return rows


def upsert_b4(rows: list[dict], b4: dict) -> list[dict]:
    for index, row in enumerate(rows):
        if row.get("exp_id") == B4_EXP_ID:
            rows[index] = b4
            return rows
    rows.append(b4)
    return rows


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: promote_b4s_t22_d2_h2_r090.py <figured_result_dir>"
        )
    figured = Path(sys.argv[1]).resolve()
    if not (figured / "run_01" / "report_normal.csv").exists():
        raise FileNotFoundError(figured)
    neighborhood = _neighborhood_row()
    print(
        f"P25 gate ok: {NEIGHBORHOOD_ID} neighbor_ir_p25="
        f"{neighborhood['neighbor_ir_p25']:.6f}"
    )
    b4 = build_b4_row(figured, neighborhood)
    rows = demote_b3(_load_registry())
    rows = upsert_b4(rows, b4)
    _write_registry(rows)
    print(f"promoted {B4_EXP_ID} -> {REGISTRY}")


if __name__ == "__main__":
    main()
