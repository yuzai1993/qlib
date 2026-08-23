"""把 M0 H20 ES × hold5 + 掉出前100必卖 晋升为 Phase M v1 执行层基线 BT v3。

用户于 2026-08-22 明确要求（在得知 2026 缺口真因是评估窗口末端前视、真阶梯并无兑现优势
之后，选择不晋升真阶梯，改晋升 h5f100）。

h5f100 相对 BT v2（top5d1）在每个维度都占优：
CAGR +30.1% vs +23.1%、夏普 1.064 vs 0.855、回撤 −34.7% vs −39.2%、
Calmar 0.868 vs 0.591、alpha +21.0% vs +15.3%、beta 0.881 vs 0.922。
代价是单边年换手 58.1 vs 49.3（force_sell_rank=100 带来的强制卖出）。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "backtest" / "experiments" / "registry.jsonl"
SOURCE = ROOT / "backtest" / "result" / "phase_s_regime" / "all_top5d1h5f100" / "m0h20es.json"
NEW_ID = "baseline/phase-m-v1-bt-v3"
PREV_ID = "baseline/phase-m-v1-bt-v2"

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
        "date": "2026-08-22",
        "state": "completed",
        "report_kind": "phase_m_v1_bt_baseline",
        "bt_version": "v3",
        "current_bt": True,
        "display_name": "M0 H20 ES hold5 + 掉出前100必卖",
        "arm": "m0h20es",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "hypothesis": (
            "用户于 2026-08-22 明确要求将 M0 H20 ES × top5d1 + hold_thresh=5 + "
            "force_sell_rank=100 五种子均值信号单次回测提升为 Phase M v1 执行层对照锚点 BT v3。"
            "该决定作于查明 2026 主格缺口真因（评估窗口末端前视）之后：真阶梯虽结构最贴主格，"
            "但绝对指标不如 h5f100，故不晋升真阶梯。"
        ),
        "eval_protocol": (
            "ensemble daily_zscore_mean | TopkDropout top5 n_drop=1 hold_thresh=5 "
            "force_sell_rank=100 risk_degree=0.90 account=1e6 | market_cn | "
            "日频ST+成交额+上市60+近60日成交 | 等权全A | 2020-08-03~2026-07-31"
        ),
        "eval_output": "backtest/result/phase_s_regime/all_top5d1h5f100/m0h20es.json",
        "detail_report": "backtest/experiments/phase_m_v1_bt_report.html",
        "metrics_source": "ensemble",
        "metrics": metrics,
        "ensemble_session": doc.get("session_dir"),
        "baseline_ref": "self",
        "promoted_from": "regime-adapt/m0h20es-all-top5d1h5f100-bt",
        "note": (
            "官方数字来自 ensemble，不是五种子算术平均；每维度均优于 BT v2"
            "（CAGR +30.1/23.1、夏普 1.064/0.855、回撤 −34.7/−39.2、Calmar 0.868/0.591），"
            "代价是单边年换手 58.1 vs 49.3"
        ),
    }

    rows = [json.loads(line) for line in REGISTRY.read_text().splitlines() if line.strip()]
    out = []
    demoted = False
    for rec in rows:
        if rec.get("exp_id") == NEW_ID:
            continue
        if rec.get("exp_id") == PREV_ID and rec.get("current_bt"):
            rec = {k: v for k, v in rec.items() if k != "current_bt"}
            demoted = True
        out.append(rec)
    out.append(entry)
    REGISTRY.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8"
    )
    print(
        f"已登记 {NEW_ID}（current_bt=True）；"
        f"{'已' if demoted else '未找到需'}摘除 {PREV_ID} 的 current_bt 标记"
    )
    print("指标：" + json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    main()
