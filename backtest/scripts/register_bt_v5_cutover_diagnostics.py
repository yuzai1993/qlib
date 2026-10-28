"""Persist the frozen 300k replay as a diagnostic, keeping official BT v5 intact."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_phase_m_v1_bt_report import write_all

EXP_ID = "diag/bt-v5-300k-current-cost-20261029"
DEST = ROOT / "backtest/experiments/bt_v5_cutover_20261029"
EVAL = ROOT / "backtest/experiments/phase_m_v1_bt_metrics/bt_v5_300k_current_cost.json"


def nav(report):
    wealth = (1 + report["return"] - report["cost"]).cumprod()
    bench = (1 + report["bench"]).cumprod()
    return [
        [str(day.date()), float(wealth.loc[day]), float(bench.loc[day])]
        for day in report.index
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    result = args.result.resolve()
    summary = json.loads((result / "summary.json").read_text())
    if summary["status"] != "completed":
        raise ValueError("diagnostic is not complete")
    report_path = result / "portfolio_daily.csv"
    if hashlib.sha256(report_path.read_bytes()).hexdigest() != summary["report_sha256"]:
        raise ValueError("daily returns SHA mismatch")
    report = pd.read_csv(report_path, index_col=0, parse_dates=True)
    historical, forward = report.loc[:"2026-07-31"], report.loc["2026-08-03":]
    DEST.mkdir(parents=True, exist_ok=True)
    for name in (
        "summary.json",
        "portfolio_daily.csv",
        "config.yaml",
        "bench_ew_all.csv",
        "filter_stats.json",
    ):
        shutil.copyfile(result / name, DEST / name)
    portable_report = str(EVAL.with_suffix(".html").relative_to(ROOT))
    doc = {
        "ensemble": {
            **summary["historical_current_cost"],
            "session_dir": str(result.relative_to(ROOT)),
            "nav_curve": nav(historical),
            "portable_report": portable_report,
        },
        "selection_limitation": "主表仅为2020-08-03～2026-07-31同窗30万元/当前费率近似复算。官方BT v5保持100万元/原费率。该历史窗曾用于早停与策略选型，不是独立样本外；价格撮合仍是日收盘代理。",
        "provenance": summary,
        "diagnostic_periods": [
            {
                "name": "8月以后冻结参数诊断（单列）",
                "start": summary["forward_start"],
                "end": summary["actual_end"],
                "metrics": summary["forward_carried_positions"],
                "nav_curve": nav(forward),
                "note": "承接连续回测7月底持仓和现金，不是实际实盘账本，也不是8月重新投入30万元。固定模型与参数，未用本段调参；短窗以区间收益展示，不将折年收益当长期预期。",
            }
        ],
    }
    EVAL.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    record = {
        "exp_id": EXP_ID,
        "direction": "phase-m-v1-bt",
        "phase": "S",
        "date": "2026-10-29",
        "state": "completed",
        "report_kind": "phase_m_v1_bt",
        "display_name": "BT v5 · 30万元/当前费率复算 + 8月后冻结诊断",
        "baseline_ref": "baseline/phase-m-v1-bt-v5",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "model_ref": "regime-adapt/m0-h20-t5h5-es-v1",
        "metrics_source": "ensemble",
        "evaluation_mode": "frozen_parameter_diagnostic_not_selection",
        "eval_output": str(EVAL.relative_to(ROOT)),
        "portable_eval_output": str(EVAL.relative_to(ROOT)),
        "detail_report": "backtest/experiments/phase_m_v1_bt_v5_cutover_diagnostic.html",
        "config_path": str((DEST / "config.yaml").relative_to(ROOT)),
        "source_summary": str((DEST / "summary.json").relative_to(ROOT)),
        "metrics": summary["historical_current_cost"]["full_period"],
        "forward_metrics": summary["forward_carried_positions"],
        "hypothesis": "固定BT v5模型与策略，检查30万元/当前费率近似口径及7月后区间，不据此调参。",
        "eval_protocol": "五种子daily_zscore_mean | TopkDropout topk5 drop1 hold1 force100 risk0.90 | account300000 open0.0000954 close0.0005954 min5 | 主表2020-08-03～2026-07-31，新增2026-08-03～2026-10-28承接历史持仓单列 | 等权全A",
        "conclusion": "diagnostic_no_selection；未改写官方BT v5指标、模型研究v4或生产实盘；当前费用为固定费率/最低费近似，不含真实竞价滑点和逐交易所精确费用。",
    }
    registry = ROOT / "backtest/experiments/registry.jsonl"
    rows = [
        json.loads(line) for line in registry.read_text().splitlines() if line.strip()
    ]
    matches = [i for i, row in enumerate(rows) if row.get("exp_id") == EXP_ID]
    if len(matches) > 1:
        raise ValueError("duplicate diagnostic identity")
    if matches:
        rows[matches[0]] = record
    else:
        rows.append(record)
    registry.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    paths = write_all()
    print(f"{EXP_ID}: durable metrics saved; {len(paths)} reports generated")


if __name__ == "__main__":
    main()
