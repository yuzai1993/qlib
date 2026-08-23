"""登记 v4 × 真阶梯 k3h5 + f100 补新票。不晋升 BT 基线。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "backtest" / "experiments" / "registry.jsonl"
EVAL = "backtest/result/phase_s_regime/all_ladder_k3h5f100r/m0h20rankices.json"
PLAIN = "backtest/result/phase_s_regime/all_ladder_k3h5/m0h20rankices.json"
F100 = "backtest/result/phase_s_regime/all_ladder_k3h5f100/m0h20rankices.json"
BT_V3 = "backtest/result/phase_s_regime/all_top5d1h5f100/m0h20es.json"
DETAIL = "backtest/experiments/phase_m_v1_bt_m0h20rankices_ladder_k3h5f100r.html"
EXP_ID = "regime-adapt/m0h20rankices-all-ladder-k3h5f100r-bt"


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


def main() -> None:
    ens = load_ensemble(EVAL)
    metrics = pick(ens)
    plain = pick(load_ensemble(PLAIN))
    f100 = pick(load_ensemble(F100))
    v3 = pick(load_ensemble(BT_V3))
    row = {
        "exp_id": EXP_ID,
        "direction": "phase-m-v1-bt",
        "phase": "S",
        "date": "2026-08-23",
        "state": "completed",
        "report_kind": "phase_m_v1_bt",
        "display_name": "v4 RankIC ES 真阶梯 k3×h5 + f100 补新票",
        "arm": "m0h20rankices",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "hypothesis": (
            "在 v4 × 真阶梯 k3h5 + 掉出前 100 必卖上，按 TopkDropout 的方式补新票："
            "强平几只就从当日 top3 之后顺延补几只还没拿着的票，用强平所得资金买入，"
            "并进当天分层、持满 5 天到期。"
            "对照无 f100 真阶梯、只强平不补人的 f100、以及执行层基线 BT v3；不晋升。"
        ),
        "eval_protocol": (
            "ensemble daily_zscore_mean | CohortLadder topk=3 horizon=5 "
            "force_sell_rank=100 refill_force_sell=true "
            "risk_degree=0.90 account=1e6 | market_cn | "
            "日频ST+成交额+上市60+近60日成交 | 等权全A | 2020-08-03~2026-07-31"
        ),
        "eval_output": EVAL,
        "detail_report": DETAIL,
        "metrics_source": "ensemble",
        "baseline_ref": "baseline/phase-m-v1-bt-v3",
        "note": "只改策略（真阶梯 + f100 + 补新票）+ 复用 v4 合成 pred；官方只跑均值信号一次；不晋升",
        "metrics": metrics,
        "ensemble_session": ens.get("session_dir"),
        "conclusion": (
            "实现按预期生效，未晋升。"
            f"全期累乘年化 {metrics['annualized_return']:+.1%}/夏普 {metrics['sharpe_ratio']:.2f}/"
            f"回撤 {metrics['max_drawdown']:+.1%}/换手 {metrics['annualized_one_way_turnover']:.1f}x，"
            f"2026 {metrics.get('y2026_annualized_return'):+.1%}。"
            f"对照真阶梯 {plain['annualized_return']:+.1%}/{plain['sharpe_ratio']:.2f}/"
            f"{plain['annualized_one_way_turnover']:.1f}x；"
            f"对照只强平不补人 {f100['annualized_return']:+.1%}/{f100['sharpe_ratio']:.2f}/"
            f"{f100['annualized_one_way_turnover']:.1f}x；"
            f"对照 BT v3 {v3['annualized_return']:+.1%}/{v3['sharpe_ratio']:.2f}/"
            f"{v3['annualized_one_way_turnover']:.1f}x。"
            "这是执行层回测，不能和主格 ×238/h 净年化直接相减。"
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
