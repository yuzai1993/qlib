"""把 top25/d5/h5 阶梯回测两个变体登记进 registry.jsonl。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "backtest" / "experiments" / "registry.jsonl"
RES = ROOT / "backtest" / "result" / "phase_s_regime"

LADDER_WHY = (
    "评估口径 ann = mean(p)×238/h 等价于「k·h 个等额仓位、每日 k 进 k 出、每只持满 h 天」"
    "的阶梯组合算术年化，k=5/h=5 即 25 槽、每日 5 进 5 出。"
)

SPECS = [
    {
        "exp_id": "regime-adapt/m0h20es-all-top25d5h5-bt",
        "display_name": "M0 H20 ES top25 drop5 hold5（主格阶梯等价）",
        "result": "all_top25d5h5",
        "detail_report": "backtest/experiments/phase_m_v1_bt_m0h20es_top25d5h5.html",
        "session": "backtest/result/20260821_234251_phase_s_m0h20es_all_top25d5h5_ensemble",
        "config": "TopkDropout topk=25 n_drop=5 hold_thresh=5",
        "hypothesis": (
            LADDER_WHY
            + " 因此把执行层从 top5d1 换成 25 槽阶梯，应当在结构上对齐主格 top5×h5，"
            "把 2025/2026 的评估-回测缺口压下去（尤其 2026 的单票 20% 重仓风险摊薄到 4%）。"
        ),
        "conclusion": (
            "假设不成立。全期 +24.6%/夏普 0.99/回撤 −34.1%，比 BT v2（+23.1%/0.86/−39.2%）稳，"
            "但 2026 从 +13.6% 掉到 −1.5%；算术口径 2026 只有 +2.0%，对主格 k5h5 的 +37.9% 差 36pp，"
            "是四个执行方案里最大的。直接原因（diag_entry_rank 实测）：25 槽占满后「未持有的最高分」"
            "在 2025/2026 中位名次 6~7、最差 18，入场篮子实际是 top20 而不是 top5；"
            "评估 head-k 阶梯显示 k=5→15 在 2026 本身就要付 7.6pp（+37.9%→+30.3%），"
            "剩余约 28pp 仍未归因。集中度不是 2026 缺口的主因，已排除。未晋升。"
        ),
    },
    {
        "exp_id": "regime-adapt/m0h20es-all-top25d5h5f100-bt",
        "display_name": "M0 H20 ES top25 drop5 hold5 + 掉出前100必卖",
        "result": "all_top25d5h5f100",
        "detail_report": "backtest/experiments/phase_m_v1_bt_m0h20es_top25d5h5f100.html",
        "session": "backtest/result/20260821_234327_phase_s_m0h20es_all_top25d5h5f100_ensemble",
        "config": "TopkDropout topk=25 n_drop=5 hold_thresh=5 force_sell_rank=100",
        "hypothesis": (
            LADDER_WHY
            + " 在 25 槽阶梯上再叠加「掉出前 100 必卖」，用于排除「阶梯里仍有票靠打分赖着不走」"
            "这一条对 2026 缺口的贡献。"
        ),
        "conclusion": (
            "假设不成立。强制卖出把 2026 从 −1.5% 拉到 +3.7%，说明阶梯里确有靠打分赖着的票，"
            "但代价是全期退到 +22.9%/夏普 0.96（低于同期 top25d5h5 的 +24.6%/0.99），"
            "2022 算术年化转负（−1.1%），日换手升到 52.3%。对主格仍差 31pp。未晋升。"
        ),
    },
    {
        "exp_id": "regime-adapt/m0h20es-all-ladder-k5h5-bt",
        "display_name": "M0 H20 ES 真阶梯 k5×h5（按持有天数到期退出）",
        "result": "all_ladder_k5h5",
        "detail_report": "backtest/experiments/phase_m_v1_bt_m0h20es_ladder_k5h5.html",
        "session": "backtest/result/20260822_001249_phase_s_m0h20es_all_ladder_k5h5_ensemble",
        "config": "CohortLadder topk=5 horizon=5",
        "hypothesis": (
            LADDER_WHY
            + " 但 top25d5h5 实测失败，原因是 TopkDropout 按打分退出、槽位被前排常驻票占住，"
            "入场中位名次掉到 6~7。改用新策略类 CohortLadderStrategy：按**持有天数**到期退出"
            "（第 5 天无条件卖），并允许同一只票被多个分层同时持有（连续上榜自动加仓）。"
            "这样入场应恒为当日 top5，预期分年偏差显著收窄，尤其修好 2026。"
        ),
        "conclusion": (
            "实现按预期生效但假设只成立一半。入场校验通过：中位名次 4、98.6%/99.1% 落在 top5、"
            "最差第 7 名（对比 top25d5h5 的中位 6~7、最差 18）；cohort 账龄严格 5 天，"
            "日换手 35.0%，现金 12.3%。分年偏差均值降到 9.7pp，是五个执行方案里最好的"
            "（top5d1 11.8 / h5f100 12.2 / t25d5h5 11.3 / t25f100 14.4）；"
            "2023 +18.3% 对 +17.8%、2024 +32.4% 对 +36.1%、2020 +7.7% 对 +5.2% 都很接近。"
            "但 2026 只有 +2.8%，对主格 +37.9% 仍差 35pp；2025 反而低到 +36.0%（主格 +43.7%）。"
            "结论：**2026 缺口不是组合构造问题**——入场只数、入场名次、退出规则、重复持有、"
            "有效槽位数全部对齐并逐项验证后缺口依旧，残差只能出在评估自身的收益计算与可成交性"
            "之间（标签删失 / t+1 买不进 / 停牌复牌路径），需另开诊断。绝对指标 +23.4%/夏普 0.98/"
            "回撤 −33.4%，与 BT v2（+23.1%/0.86/−39.2%）同量级但回撤更浅。未晋升。"
        ),
    },
]

PROTO = (
    "ensemble daily_zscore_mean | {cfg} risk_degree=0.90 account=1e6 | market_cn | "
    "日频ST+成交额+上市60+近60日成交 | 等权全A | 2020-08-03~2026-07-31"
)


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
    lines = [line for line in REG.read_text().splitlines() if line.strip()]
    kept = [
        line for line in lines if json.loads(line)["exp_id"] not in {s["exp_id"] for s in SPECS}
    ]
    for spec in SPECS:
        summary = json.loads((RES / spec["result"] / "m0h20es.json").read_text())["ensemble"]
        kept.append(
            json.dumps(
                {
                    "exp_id": spec["exp_id"],
                    "direction": "phase-m-v1-bt",
                    "phase": "S",
                    "date": "2026-08-21",
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
                    "baseline_ref": "baseline/phase-m-v1-bt-v2",
                    "note": "只改策略；信号与 BT v2 同一条 ensemble pred；官方只跑均值信号一次",
                    "metrics": pick(summary),
                    "ensemble_session": spec["session"],
                    "conclusion": spec["conclusion"],
                },
                ensure_ascii=False,
            )
        )
    REG.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"registry 共 {len(kept)} 条")
    for spec in SPECS:
        summary = json.loads((RES / spec["result"] / "m0h20es.json").read_text())["ensemble"]
        m = pick(summary)
        print(
            f"{spec['exp_id']}: 累乘年化 {m['annualized_return']:+.1%} "
            f"夏普 {m['sharpe_ratio']:.2f} 回撤 {m['max_drawdown']:+.1%} "
            f"2026 {m['y2026_annualized_return']:+.1%}"
        )


if __name__ == "__main__":
    main()
