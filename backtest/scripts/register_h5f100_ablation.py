"""登记 hold5 / f100 2×2 消融。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "backtest" / "experiments" / "registry.jsonl"
RES = ROOT / "backtest" / "result" / "phase_s_regime"

PROTO = (
    "ensemble daily_zscore_mean | {cfg} risk_degree=0.90 account=1e6 | market_cn | "
    "日频ST+成交额+上市60+近60日成交 | 等权全A | 2020-08-03~2026-07-31"
)

SPECS = [
    {
        "exp_id": "regime-adapt/m0h20es-all-top5d1h5-bt",
        "display_name": "M0 H20 ES top5d1 hold5（无强制卖出）",
        "result": "all_top5d1h5",
        "detail_report": "backtest/experiments/phase_m_v1_bt_m0h20es_h5.html",
        "session": "backtest/result/20260822_091251_phase_s_m0h20es_all_top5d1h5_ensemble",
        "config": "TopkDropout topk=5 n_drop=1 hold_thresh=5",
        "hypothesis": (
            "BT v3（hold5 + 掉出前100必卖）相对 BT v2（top5d1）多出约 7pp CAGR。"
            "本臂只开 hold_thresh=5、不开 force_sell_rank，检验最短持仓本身是否就是收益来源。"
            "若 hold5 单独接近组合，则强制卖出只是配角；若 hold5 单独接近 top5d1，则收益来自强制卖出。"
        ),
        "conclusion": (
            "hold5 单独已经够用，甚至优于组合。CAGR +31.3%/夏普 1.05/回撤 −33.4%/换手 45.8，"
            "对 top5d1（+23.1%/0.86/−39.2%/49.3）+8.2pp，对组合 h5f100（+30.1%/1.06/−34.7%/58.1）"
            "还高 1.2pp。2022 算术 +40.3% 是四格里最好的一年，但 2020 翻负（算术 −4.9%、CAGR −8.0%），"
            "说明最短持仓会把早期衰退票锁满 5 天。未晋升。"
        ),
    },
    {
        "exp_id": "regime-adapt/m0h20es-all-top5d1f100-bt",
        "display_name": "M0 H20 ES top5d1 + 掉出前100必卖（hold=1）",
        "result": "all_top5d1f100",
        "detail_report": "backtest/experiments/phase_m_v1_bt_m0h20es_f100.html",
        "session": "backtest/result/20260822_091415_phase_s_m0h20es_all_top5d1f100_ensemble",
        "config": "TopkDropout topk=5 n_drop=1 hold_thresh=1 force_sell_rank=100",
        "hypothesis": (
            "与 hold5 单臂对称：只开 force_sell_rank=100、hold_thresh 保持 1。"
            "若本臂接近或超过组合，则 BT v3 的超额主要来自「掉出前100必卖」而不是最短持仓。"
        ),
        "conclusion": (
            "四格最强，且与 hold5 负交互。CAGR +34.8%/夏普 1.18/回撤 −33.0%/Alpha +24.7%，"
            "对 top5d1 +11.7pp，对组合 +4.7pp。代价是单边年换手 73.3（top5d1 49.3、组合 58.1）。"
            "2025 CAGR +123.9%（算术 +83.8%）主导全期，2026 CAGR +26.3% 也高于组合的 +4.6%。"
            "2×2 交互 = 组合增量 − hold5增量 − f100增量 = −12.9pp：两个规则叠在一起互相拆台——"
            "hold5 把排名 6~100 的衰退票锁 5 天，f100 只救掉出前 100 的；同时 hold5 又挡住"
            "f100 那套更积极的 n_drop 轮换。BT v3 的超额不是两因子相加，而是各因子都有效、"
            "叠完只剩较弱的那个。未晋升（当前基线仍是组合 BT v3）。"
        ),
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
    y2026 = summary["years"].get("2026") or {}
    out["y2026_annualized_return"] = y2026.get("annualized_return")
    out["y2026_annualized_return_arith"] = y2026.get("annualized_return_arith")
    return out


def main() -> None:
    ids = {s["exp_id"] for s in SPECS}
    rows = [json.loads(line) for line in REG.read_text().splitlines() if line.strip()]
    kept = [r for r in rows if r.get("exp_id") not in ids]
    for spec in SPECS:
        summary = json.loads((RES / spec["result"] / "m0h20es.json").read_text())["ensemble"]
        kept.append(
            {
                "exp_id": spec["exp_id"],
                "direction": "phase-m-v1-bt",
                "phase": "S",
                "date": "2026-08-22",
                "state": "completed",
                "report_kind": "phase_m_v1_bt",
                "display_name": spec["display_name"],
                "arm": "m0h20es",
                "seeds": [42, 1000, 2000, 3000, 4000],
                "hypothesis": spec["hypothesis"],
                "eval_protocol": PROTO.format(cfg=spec["config"]),
                "eval_output": f"backtest/result/phase_s_regime/{spec['result']}/m0h20es.json",
                "detail_report": spec["detail_report"],
                "metrics_source": "ensemble",
                "baseline_ref": "baseline/phase-m-v1-bt-v3",
                "note": "2×2 消融；信号与 BT v2/v3 同一条 ensemble pred；只改 hold_thresh / force_sell_rank",
                "metrics": pick(summary),
                "ensemble_session": spec["session"],
                "conclusion": spec["conclusion"],
            }
        )
    REG.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + "\n", encoding="utf-8"
    )
    print(f"registry 共 {len(kept)} 条")
    for spec in SPECS:
        m = pick(json.loads((RES / spec["result"] / "m0h20es.json").read_text())["ensemble"])
        print(
            f"{spec['exp_id']}: 累乘年化 {m['annualized_return']:+.1%} "
            f"夏普 {m['sharpe_ratio']:.2f} 回撤 {m['max_drawdown']:+.1%} "
            f"2026 {m['y2026_annualized_return']:+.1%}"
        )


if __name__ == "__main__":
    main()
