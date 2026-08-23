"""把 v4 RankIC ES × 真阶梯 k3h5 晋升为 Phase M v1 执行层基线 BT v4。

用户于 2026-08-23 明确要求。这是主格 top3×h5 的执行层等价物
（CohortLadderStrategy topk=3 horizon=5），不是因为对 BT v3 逐维占优。

相对 BT v3（v2 模型 × TopkDropout h5f100）：
CAGR +26.3% vs +30.1%、夏普 1.04 vs 1.06、回撤 −33.0% vs −34.7%、
换手 44.3 vs 58.1。收益/夏普略低，回撤更浅、换手更低，且组合规则对齐主格。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "backtest" / "experiments" / "registry.jsonl"
SOURCE = ROOT / "backtest" / "result" / "phase_s_regime" / "all_ladder_k3h5" / "m0h20rankices.json"
NEW_ID = "baseline/phase-m-v1-bt-v4"
PREV_ID = "baseline/phase-m-v1-bt-v3"
SOURCE_EXP = "regime-adapt/m0h20rankices-all-ladder-k3h5-bt"

METRIC_KEYS = (
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
)


def main() -> None:
    doc = json.loads(SOURCE.read_text())["ensemble"]
    fp = doc["full_period"]
    metrics = {k: fp[k] for k in METRIC_KEYS if k in fp}
    y2026 = (doc.get("years") or {}).get("2026") or {}
    if "annualized_return" in y2026:
        metrics["y2026_annualized_return"] = y2026["annualized_return"]

    entry = {
        "exp_id": NEW_ID,
        "direction": "phase-m-v1-bt",
        "phase": "M",
        "date": "2026-08-23",
        "state": "completed",
        "report_kind": "phase_m_v1_bt_baseline",
        "bt_version": "v4",
        "current_bt": True,
        "display_name": "v4 RankIC ES 真阶梯 k3×h5",
        "arm": "m0h20rankices",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "hypothesis": (
            "用户于 2026-08-23 明确要求将 v4 RankIC ES × CohortLadderStrategy "
            "topk=3 horizon=5 五种子均值信号单次回测提升为 Phase M v1 执行层对照锚点 BT v4。"
            "该行是主格 top3×h5 的执行层等价物：每日买当日 top3，持满 5 天到期无条件卖，"
            "允许同一票多层持有。晋升依据是组合规则对齐主格，不是对 BT v3 逐维占优。"
        ),
        "eval_protocol": (
            "ensemble daily_zscore_mean | CohortLadder topk=3 horizon=5 "
            "risk_degree=0.90 account=1e6 | market_cn | "
            "日频ST+成交额+上市60+近60日成交 | 等权全A | 2020-08-03~2026-07-31"
        ),
        "eval_output": "backtest/result/phase_s_regime/all_ladder_k3h5/m0h20rankices.json",
        "detail_report": "backtest/experiments/phase_m_v1_bt_report.html",
        "metrics_source": "ensemble",
        "metrics": metrics,
        "ensemble_session": doc.get("session_dir"),
        "baseline_ref": "self",
        "promoted_from": SOURCE_EXP,
        "note": (
            "官方数字来自 ensemble，不是五种子算术平均；"
            "相对 BT v3：CAGR +26.3/30.1、夏普 1.04/1.06、回撤 −33.0/−34.7、"
            "换手 44.3/58.1。冻结模型切到 v4，策略切到真阶梯。"
        ),
    }

    rows = [json.loads(line) for line in REGISTRY.read_text().splitlines() if line.strip()]
    out = []
    demoted = False
    marked = False
    for rec in rows:
        if rec.get("exp_id") == NEW_ID:
            continue
        if rec.get("exp_id") == PREV_ID and rec.get("current_bt"):
            rec = {k: v for k, v in rec.items() if k != "current_bt"}
            demoted = True
        if rec.get("exp_id") == SOURCE_EXP:
            rec = dict(rec)
            rec["state"] = "promoted"
            rec["note"] = (
                "2026-08-23 用户明确要求晋升为执行层基线 BT v4。"
                "原始登记作诊断、对照 BT v3。"
            )
            conclusion = rec.get("conclusion") or ""
            suffix = "2026-08-23 用户明确要求将该行晋升为 BT v4。"
            if suffix not in conclusion:
                rec["conclusion"] = (conclusion + suffix) if conclusion else suffix
            marked = True
        out.append(rec)
    out.append(entry)
    REGISTRY.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8"
    )
    print(
        f"已登记 {NEW_ID}（current_bt=True）；"
        f"{'已' if demoted else '未找到需'}摘除 {PREV_ID} 的 current_bt 标记；"
        f"{'已' if marked else '未找到需'}标记 {SOURCE_EXP} 为 promoted"
    )
    print("指标：" + json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    main()
