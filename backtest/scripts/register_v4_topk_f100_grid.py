"""登记 v4 × top15d3 / top5d1 / top3d1 + f100。不晋升 BT 基线。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "backtest" / "experiments" / "registry.jsonl"
BT_V4 = "backtest/result/phase_s_regime/all_ladder_k3h5/m0h20rankices.json"

SPECS = [
    {
        "exp_id": "regime-adapt/m0h20rankices-all-top15d3f100-bt",
        "display_name": "v4 RankIC ES top15d3 + f100",
        "eval": "backtest/result/phase_s_regime/all_top15d3f100/m0h20rankices.json",
        "detail": "backtest/experiments/phase_m_v1_bt_m0h20rankices_top15d3f100.html",
        "config": "TopkDropout topk=15 n_drop=3 hold_thresh=1 force_sell_rank=100",
    },
    {
        "exp_id": "regime-adapt/m0h20rankices-all-top5d1f100-bt",
        "display_name": "v4 RankIC ES top5d1 + f100",
        "eval": "backtest/result/phase_s_regime/all_top5d1f100/m0h20rankices.json",
        "detail": "backtest/experiments/phase_m_v1_bt_m0h20rankices_top5d1f100.html",
        "config": "TopkDropout topk=5 n_drop=1 hold_thresh=1 force_sell_rank=100",
    },
    {
        "exp_id": "regime-adapt/m0h20rankices-all-top3d1f100-bt",
        "display_name": "v4 RankIC ES top3d1 + f100",
        "eval": "backtest/result/phase_s_regime/all_top3d1f100/m0h20rankices.json",
        "detail": "backtest/experiments/phase_m_v1_bt_m0h20rankices_top3d1f100.html",
        "config": "TopkDropout topk=3 n_drop=1 hold_thresh=1 force_sell_rank=100",
    },
]


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


def load_ensemble(rel: str) -> dict:
    path = ROOT / rel
    if not path.is_file():
        raise SystemExit(f"missing {path}")
    doc = json.loads(path.read_text())
    ens = doc.get("ensemble")
    if not ens:
        raise SystemExit(f"{rel} missing ensemble")
    return ens


def fmt(metrics: dict) -> str:
    return (
        f"{metrics['annualized_return']:+.1%}/{metrics['sharpe_ratio']:.2f}/"
        f"{metrics['max_drawdown']:+.1%}/{metrics['annualized_one_way_turnover']:.1f}x，"
        f"2026 {metrics.get('y2026_annualized_return'):+.1%}"
    )


def main() -> None:
    base = pick(load_ensemble(BT_V4))
    rows = [json.loads(line) for line in REG.read_text().splitlines() if line.strip()]
    drop = {spec["exp_id"] for spec in SPECS}
    kept = [row for row in rows if row.get("exp_id") not in drop]
    for spec in SPECS:
        ens = load_ensemble(spec["eval"])
        metrics = pick(ens)
        kept.append(
            {
                "exp_id": spec["exp_id"],
                "direction": "phase-m-v1-bt",
                "phase": "S",
                "date": "2026-08-23",
                "state": "completed",
                "report_kind": "phase_m_v1_bt",
                "display_name": spec["display_name"],
                "arm": "m0h20rankices",
                "seeds": [42, 1000, 2000, 3000, 4000],
                "hypothesis": (
                    "用当前模型基线 v4 的官方合成信号，跑 TopkDropout "
                    f"{spec['config']}。"
                    "对照当前执行层基线 BT v4（同信号真阶梯 k3h5）；本行只作诊断，不晋升。"
                ),
                "eval_protocol": (
                    f"ensemble daily_zscore_mean | {spec['config']} "
                    "risk_degree=0.90 account=1e6 | market_cn | "
                    "日频ST+成交额+上市60+近60日成交 | 等权全A | 2020-08-03~2026-07-31"
                ),
                "eval_output": spec["eval"],
                "detail_report": spec["detail"],
                "metrics_source": "ensemble",
                "baseline_ref": "baseline/phase-m-v1-bt-v4",
                "note": "只改策略 + 复用 v4 合成 pred；官方只跑均值信号一次；不晋升执行层基线",
                "metrics": metrics,
                "ensemble_session": ens.get("session_dir"),
                "conclusion": (
                    "实现按预期生效，未晋升。"
                    f"全期 {fmt(metrics)}。"
                    f"对照 BT v4 {fmt(base)}。"
                    "这是执行层回测，不能和主格 ×238/h 净年化直接相减。"
                ),
            }
        )
        print(f"{spec['exp_id']}: {fmt(metrics)}")
    REG.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in kept) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
