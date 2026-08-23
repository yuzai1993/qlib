"""登记 v4 × 真阶梯 k3h5 执行层回测。不晋升 BT 基线。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "backtest" / "experiments" / "registry.jsonl"
EVAL = "backtest/result/phase_s_regime/all_ladder_k3h5/m0h20rankices.json"
DETAIL = "backtest/experiments/phase_m_v1_bt_m0h20rankices_ladder_k3h5.html"
EXP_ID = "regime-adapt/m0h20rankices-all-ladder-k3h5-bt"


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
    row = {
        "exp_id": EXP_ID,
        "direction": "phase-m-v1-bt",
        "phase": "S",
        "date": "2026-08-22",
        "state": "completed",
        "report_kind": "phase_m_v1_bt",
        "display_name": "v4 RankIC ES 真阶梯 k3×h5",
        "arm": "m0h20rankices",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "hypothesis": (
            "用当前模型基线 v4 的官方合成信号，跑主格 top3×h5 的执行层等价物："
            "CohortLadderStrategy topk=3 horizon=5。"
            "每日买当日 top3，持满 5 天到期无条件卖，允许同一票多层持有。"
            "对照当前执行层基线 BT v3；本行只作诊断，不晋升。"
        ),
        "eval_protocol": (
            "ensemble daily_zscore_mean | CohortLadder topk=3 horizon=5 "
            "risk_degree=0.90 account=1e6 | market_cn | "
            "日频ST+成交额+上市60+近60日成交 | 等权全A | 2020-08-03~2026-07-31"
        ),
        "eval_output": EVAL,
        "detail_report": DETAIL,
        "metrics_source": "ensemble",
        "baseline_ref": "baseline/phase-m-v1-bt-v3",
        "note": "只改策略+换到 v4 信号；官方只跑均值信号一次；不晋升执行层基线",
        "metrics": metrics,
        "ensemble_session": ens.get("session_dir"),
        "conclusion": (
            "实现按预期生效，未晋升。"
            f"全期累乘年化 {metrics['annualized_return']:+.1%}/夏普 {metrics['sharpe_ratio']:.2f}/"
            f"回撤 {metrics['max_drawdown']:+.1%}/换手 {metrics['annualized_one_way_turnover']:.1f}x，"
            f"2026 {metrics.get('y2026_annualized_return'):+.1%}。"
            "对照 BT v3（v2 模型 × top5d1+hold5+f100：+30.1%/1.06/−34.7%/58.1x，2026 +4.6%），"
            "真阶梯换手更低、回撤略浅，但 CAGR/夏普/Alpha 都低于当前执行层锚点。"
            "这是主格 top3×h5 的执行层等价物，不能和 ×238/h 净年化直接相减。"
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
