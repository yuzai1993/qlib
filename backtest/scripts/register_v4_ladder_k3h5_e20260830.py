"""登记 v4 × 真阶梯 k3h5 延长窗到 2026-08-30。不晋升、不改写官方 BT v4。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "backtest" / "experiments" / "registry.jsonl"
EVAL = "backtest/result/phase_s_regime/all_ladder_k3h5_e20260830/m0h20rankices.json"
OFFICIAL = "backtest/result/phase_s_regime/all_ladder_k3h5/m0h20rankices.json"
DETAIL = "backtest/experiments/phase_m_v1_bt_m0h20rankices_ladder_k3h5_e20260830.html"
EXP_ID = "regime-adapt/m0h20rankices-all-ladder-k3h5-e20260830-bt"


def pick(summary: dict) -> dict:
    fp = summary["full_period"]
    keys = [
        "annualized_return",
        "sharpe_ratio",
        "alpha",
        "beta",
        "max_drawdown",
        "calmar_ratio",
        "annualized_volatility",
        "annualized_one_way_turnover",
        "n_days",
        "annualized_return_arith",
    ]
    out = {k: fp[k] for k in keys if k in fp}
    y2026 = (summary.get("years") or {}).get("2026") or {}
    out["y2026_annualized_return"] = y2026.get("annualized_return")
    out["y2026_annualized_return_arith"] = y2026.get("annualized_return_arith")
    out["y2026_n_days"] = y2026.get("n_days")
    out["y2026_end"] = y2026.get("end")
    return out


def main() -> None:
    path = ROOT / EVAL
    if not path.is_file():
        raise SystemExit(f"missing {path}")
    doc = json.loads(path.read_text())
    ens = doc.get("ensemble")
    if not ens:
        raise SystemExit(f"{EVAL} missing ensemble")
    metrics = pick(ens)
    official = json.loads((ROOT / OFFICIAL).read_text())
    off_m = pick(official["ensemble"])
    row = {
        "exp_id": EXP_ID,
        "direction": "phase-m-v1-bt",
        "phase": "S",
        "date": "2026-08-30",
        "state": "completed",
        "report_kind": "phase_m_v1_bt",
        "display_name": "v4 RankIC ES 真阶梯 k3×h5 延长至 2026-08-30",
        "arm": "m0h20rankices",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "hypothesis": (
            "把 BT v4 官方窗从 2026-07-31 延长到 2026-08-30"
            "（实际最后交易日 2026-08-28），复用 v4 RankIC ES 官方合成信号与真阶梯 k3×h5，"
            "看多出约一个月后的全期/2026 年化与回撤。"
            "对照官方 BT v4（截止 2026-07-31）。本行只作诊断，不晋升、不改写官方窗。"
        ),
        "eval_protocol": (
            "ensemble daily_zscore_mean | CohortLadder topk=3 horizon=5 "
            "risk_degree=0.90 account=1e6 | market_cn | "
            "日频ST+成交额+上市60+近60日成交 | 等权全A | 2020-08-03~2026-08-30"
        ),
        "eval_output": EVAL,
        "detail_report": DETAIL,
        "metrics_source": "ensemble",
        "baseline_ref": "baseline/phase-m-v1-bt-v4",
        "evaluation_mode": "extended_window_diagnostic",
        "note": "只延长回测窗；官方只跑均值信号一次；不晋升、不改写 BT v4 官方窗",
        "metrics": metrics,
        "ensemble_session": ens.get("session_dir"),
        "conclusion": (
            "延长窗按预期跑完，未晋升。"
            f"全期累乘年化 {metrics['annualized_return']:+.1%}/夏普 {metrics['sharpe_ratio']:.2f}/"
            f"回撤 {metrics['max_drawdown']:+.1%}/换手 {metrics['annualized_one_way_turnover']:.1f}x，"
            f"n_days={metrics['n_days']}，2026 {metrics.get('y2026_annualized_return'):+.1%}"
            f"（{metrics.get('y2026_n_days')} 日，截止 {metrics.get('y2026_end')}）。"
            f"对照官方 BT v4（截止 2026-07-31）：全期 {off_m['annualized_return']:+.1%}/"
            f"{off_m['sharpe_ratio']:.2f}/{off_m['max_drawdown']:+.1%}，"
            f"2026 {off_m.get('y2026_annualized_return'):+.1%}（{off_m.get('y2026_n_days')} 日）。"
            "多出的 20 个交易日把 2026 年化从约 +3.8% 抬到约 +49.9%；"
            "这是延长窗诊断，不能和官方窗数字直接当同一口径，也不能和主格 ×238/h 净年化相减。"
        ),
    }
    lines = [line for line in REG.read_text().splitlines() if line.strip()]
    kept = [line for line in lines if json.loads(line)["exp_id"] != EXP_ID]
    kept.append(json.dumps(row, ensure_ascii=False))
    REG.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(
        f"{EXP_ID}: 累乘年化 {metrics['annualized_return']:+.1%} "
        f"夏普 {metrics['sharpe_ratio']:.2f} "
        f"回撤 {metrics['max_drawdown']:+.1%} "
        f"2026 {metrics.get('y2026_annualized_return')}"
    )


if __name__ == "__main__":
    main()
