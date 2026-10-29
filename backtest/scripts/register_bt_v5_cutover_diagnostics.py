"""Persist the frozen 300k replay as a diagnostic, keeping official BT v5 intact."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_phase_m_v1_bt_report import write_all


def diagnostic_identity(summary):
    risk = summary.get("risk_degree")
    if risk not in (0.9, 1.0):
        raise ValueError("diagnostic must record risk_degree 0.9 or 1.0 explicitly")
    is_risk1 = risk == 1.0
    return {
        "exp_id": f"diag/bt-v5-300k-{'risk1-' if is_risk1 else ''}current-cost-20261029",
        "destination": ROOT
        / f"backtest/experiments/bt_v5_cutover_{'risk1_' if is_risk1 else ''}20261029",
        "eval_output": ROOT
        / f"backtest/experiments/phase_m_v1_bt_metrics/bt_v5_300k_{'risk1_' if is_risk1 else ''}current_cost.json",
        "detail_report": f"backtest/experiments/phase_m_v1_bt_v5_cutover_{'risk1_' if is_risk1 else ''}diagnostic.html",
    }


def nav(report):
    wealth = (1 + report["return"] - report["cost"]).cumprod()
    bench = (1 + report["bench"]).cumprod()
    return [
        [str(day.date()), float(wealth.loc[day]), float(bench.loc[day])]
        for day in report.index
    ]


def save_record(record):
    registry = ROOT / "backtest/experiments/registry.jsonl"
    lines = registry.read_text().splitlines(keepends=True)
    matches = [
        i
        for i, line in enumerate(lines)
        if line.strip() and json.loads(line).get("exp_id") == record["exp_id"]
    ]
    if len(matches) > 1:
        raise ValueError("duplicate diagnostic identity")
    if matches:
        lines[matches[0]] = json.dumps(record, ensure_ascii=False) + "\n"
    else:
        lines.append(json.dumps(record, ensure_ascii=False) + "\n")
    registry.write_text("".join(lines))


def register_failure(result, summary, *, attempt):
    if summary["status"] != "failed" or attempt < 1:
        raise ValueError("failed summary and positive failure attempt are required")
    identity = diagnostic_identity(summary)
    dest = identity["destination"] / f"failed_attempt_{attempt}"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(result / "summary.json", dest / "summary.json")
    output = identity["eval_output"].with_stem(
        identity["eval_output"].stem + f"_failed_{attempt}"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    relative_output = str(output.relative_to(ROOT))
    limitation = (
        f"失败尝试{attempt}；尚无回测指标，不参与比较。原因：{summary['error']}"
    )
    doc = {
        "ensemble": {
            "full_period": {},
            "years": {},
            "regimes": {},
            "session_dir": str(result.relative_to(ROOT)),
            "portable_report": str(output.with_suffix(".html").relative_to(ROOT)),
        },
        "selection_limitation": limitation,
        "provenance": summary,
    }
    output.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    record = {
        "exp_id": identity["exp_id"] + f"-failed-{attempt}",
        "direction": "phase-m-v1-bt",
        "phase": "S",
        "date": "2026-10-29",
        "state": "failed",
        "report_kind": "phase_m_v1_bt",
        "display_name": f"BT v5 · risk={summary['risk_degree']:.2f} · 诊断失败尝试{attempt}",
        "baseline_ref": "baseline/phase-m-v1-bt-v5",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "model_ref": "regime-adapt/m0-h20-t5h5-es-v1",
        "metrics_source": "ensemble",
        "evaluation_mode": "frozen_parameter_diagnostic_not_selection",
        "risk_degree": summary["risk_degree"],
        "data_version": summary["requested_end"],
        "eval_output": relative_output,
        "detail_report": identity["detail_report"].replace(
            "_diagnostic.html", f"_failed_{attempt}.html"
        ),
        "source_summary": str((dest / "summary.json").relative_to(ROOT)),
        "config_path": "backtest/configs/alla_v2_top5d1f100_parity.yaml",
        "result_dirs": [str(result.relative_to(ROOT))],
        "metrics": {},
        "hypothesis": summary["hypothesis"],
        "conclusion": limitation,
    }
    save_record(record)
    paths = write_all()
    print(f"{record['exp_id']}: failure registered; {len(paths)} reports generated")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--failure-attempt", type=int)
    args = parser.parse_args()
    result = args.result.resolve()
    summary = json.loads((result / "summary.json").read_text())
    if args.failure_attempt is not None:
        register_failure(result, summary, attempt=args.failure_attempt)
        return
    identity = diagnostic_identity(summary)
    exp_id, dest, eval_output = (
        identity[key] for key in ("exp_id", "destination", "eval_output")
    )
    risk = summary["risk_degree"]
    if summary["status"] != "completed":
        raise ValueError("diagnostic is not complete")
    cfg = yaml.safe_load((result / "config.yaml").read_text())
    from run_bt_v5_cutover_diagnostics import validate_frozen_config

    validate_frozen_config(cfg, risk_degree=risk)
    if (
        hashlib.sha256((result / "config.yaml").read_bytes()).hexdigest()
        != summary["config_sha256"]
    ):
        raise ValueError("diagnostic config SHA mismatch")
    report_path = result / "portfolio_daily.csv"
    if hashlib.sha256(report_path.read_bytes()).hexdigest() != summary["report_sha256"]:
        raise ValueError("daily returns SHA mismatch")
    report = pd.read_csv(report_path, index_col=0, parse_dates=True)
    historical, forward = report.loc[:"2026-07-31"], report.loc["2026-08-03":]
    dest.mkdir(parents=True, exist_ok=True)
    for name in (
        "summary.json",
        "portfolio_daily.csv",
        "config.yaml",
        "bench_ew_all.csv",
        "filter_stats.json",
    ):
        shutil.copyfile(result / name, dest / name)
    portable_report = str(eval_output.with_suffix(".html").relative_to(ROOT))
    doc = {
        "ensemble": {
            **summary["historical_current_cost"],
            "session_dir": str(result.relative_to(ROOT)),
            "nav_curve": nav(historical),
            "portable_report": portable_report,
        },
        "selection_limitation": f"主表仅为2020-08-03～2026-07-31同窗30万元/risk={risk:.2f}/当前费率近似复算。官方BT v5保持risk0.90/100万元/原费率。该历史窗曾用于早停与策略选型，不是独立样本外；价格撮合仍是日收盘代理。",
        "provenance": summary,
        "diagnostic_periods": [
            {
                "name": f"8月以后冻结参数risk={risk:.2f}诊断（单列）",
                "start": summary["forward_start"],
                "end": summary["actual_end"],
                "metrics": summary["forward_carried_positions"],
                "nav_curve": nav(forward),
                "note": "承接连续回测7月底持仓和现金，不是实际实盘账本，也不是8月重新投入30万元。固定模型与参数，未用本段调参；短窗以区间收益展示，不将折年收益当长期预期。",
            }
        ],
    }
    eval_output.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    record = {
        "exp_id": exp_id,
        "direction": "phase-m-v1-bt",
        "phase": "S",
        "date": "2026-10-29",
        "state": "completed",
        "report_kind": "phase_m_v1_bt",
        "display_name": f"BT v5 · risk={risk:.2f} · 30万元/当前费率复算 + 8月后冻结诊断",
        "baseline_ref": "baseline/phase-m-v1-bt-v5",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "model_ref": "regime-adapt/m0-h20-t5h5-es-v1",
        "metrics_source": "ensemble",
        "evaluation_mode": "frozen_parameter_diagnostic_not_selection",
        "risk_degree": risk,
        "data_version": summary["actual_end"],
        "eval_output": str(eval_output.relative_to(ROOT)),
        "portable_eval_output": str(eval_output.relative_to(ROOT)),
        "detail_report": identity["detail_report"],
        "config_path": str((dest / "config.yaml").relative_to(ROOT)),
        "source_summary": str((dest / "summary.json").relative_to(ROOT)),
        "result_dirs": [str(result.relative_to(ROOT))],
        "metrics": summary["historical_current_cost"]["full_period"],
        "forward_metrics": summary["forward_carried_positions"],
        "hypothesis": summary["hypothesis"],
        "eval_protocol": f"五种子daily_zscore_mean | TopkDropout topk5 drop1 hold1 force100 risk{risk:.2f} | account300000 open0.0000954 close0.0005954 min5 | 主表2020-08-03～2026-07-31，新增2026-08-03～2026-10-28承接历史持仓单列 | 等权全A",
        "conclusion": "diagnostic_no_selection；未改写官方BT v5指标、模型研究v4或生产实盘；当前费用为固定费率/最低费近似，不含真实竞价滑点和逐交易所精确费用。",
    }
    save_record(record)
    paths = write_all()
    print(f"{exp_id}: durable metrics saved; {len(paths)} reports generated")


if __name__ == "__main__":
    main()
