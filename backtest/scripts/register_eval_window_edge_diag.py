"""把「评估窗口末端前视」这次口径诊断登记进 registry。

report_kind 用 `eval_protocol_diag`，Phase M v1 报告生成器只认 `phase_m_v1_bt`
与 `phase_m_v1_bt_baseline`，故本条不进任何 HTML 表，只作审计痕迹。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "backtest" / "experiments" / "registry.jsonl"
EXP_ID = "diag/eval-window-edge-lookahead"

ENTRY = {
    "exp_id": EXP_ID,
    "direction": "eval-protocol-diag",
    "phase": "M",
    "date": "2026-08-22",
    "state": "completed",
    "report_kind": "eval_protocol_diag",
    "display_name": "评估窗口末端前视：主格 2026 缺口的真因",
    "arm": "m0h20es",
    "seeds": [42, 1000, 2000, 3000, 4000],
    "hypothesis": (
        "真阶梯把入场只数/名次/退出规则/重复持有/槽位数全部对齐主格后 2026 仍差 35pp，"
        "缺口只能出在评估自身的收益计算与真实可成交性之间。候选：(a) 标签 Ref 在个股行序"
        "上位移，停牌股的 5 日收益可能跨更多交易日却仍按 ×238/5 年化；(b) 标签删失（退市/"
        "长停股在评估里静默消失，回测却真拿着）；(c) t+1 买不进。"
    ),
    "eval_protocol": (
        "只读诊断，不训练。信号 = BT v2 同一条五种子 zscore 合成 pred；"
        "宇宙复现 evaluate_multi_horizon 全部过滤（非基金+上市60+日频ST+成交额1000万+t+1可成交）；"
        "头部走 eval_ic_multi_pool.daily_head_panel 本体，不手写选股；"
        "全A top5×h5，2020-08-03~2026-07-31"
    ),
    "eval_output": "backtest/result/diag_eval_span/window_edge_m0h20es.json",
    "detail_report": None,
    "metrics_source": "diagnostic",
    "baseline_ref": "baseline/phase-m-v1-v2",
    "note": (
        "诊断脚本：diag_eval_holding_span.py（跨度+删失）、diag_eval_2026_replication.py"
        "（复现对账）、diag_eval_edge_days.py（末端标的价格路径）、diag_eval_window_edge.py"
        "（量化+产出 JSON）"
    ),
    "metrics": {
        "span_all_exactly_h_share": 1.0,
        "span_over_h_share": 0.0,
        "label_censor_share_2026": 0.00144,
        "dropped_eval_days_2026": 6,
        "y2026_net_ann_old": 0.380,
        "y2026_net_ann_in_window": -0.002,
        "y2026_ladder_bt_arith": 0.028,
        "full_net_ann_old": 0.313,
        "full_net_ann_in_window": 0.278,
        "mean_abs_year_gap_before": 0.097,
        "mean_abs_year_gap_after": 0.040,
    },
    "conclusion": (
        "候选 (a)(b)(c) 全部证伪：7240 个入选样本真实占用交易日全为 5 天（qlib cn_data 逐"
        "交易日建行，行序=市场日历），标签删失各年 ≤0.8%。真因是第四种：标签在日 t 记入 "
        "t+1→t+6 收盘收益，窗口末日 2026-07-31 往前 6 个评估日的平仓价落在 2026-08-03~08-21，"
        "回测在 07-31 停止交易并按当日收盘估值，这段收益永远兑现不了。2026 仅 139 个评估日，"
        "这 6 天权重 4.3% 而篮子收益 +7.6%~+35.4%（全年日均 +0.88%），贡献全年头部收益约 77%。"
        "剔除后 2026 净年化 +38.0% → −0.2%，真阶梯回测 +2.8%，缺口由 35pp 转为 +3.0pp（回测反"
        "而略优）；分年 |BT−评估| 均值 9.7pp → 4.0pp，残差方向与手数取整、12.3% 现金拖累、"
        "重复持有集中度一致。这是评估口径缺陷而非回测缺陷：声明测试窗到 07-31 却取到 08-21 的"
        "价格，末端 h+1 天一直在窗口外取数，h=10 时是 11 天，IC 同样受影响。已修 "
        "eval_ic_multi_pool.label_window_cutoff + evaluate_multi_horizon 截断（含 3 个单测），"
        "规范 v2.12 第 5.1.1 节第 5 条待用户确认；官方评估尚未重跑，registry 与报告里的 M0 H20 "
        "ES 数字仍是旧口径。"
    ),
}


def main() -> None:
    rows = [json.loads(line) for line in REGISTRY.read_text().splitlines() if line.strip()]
    kept = [r for r in rows if r.get("exp_id") != EXP_ID]
    kept.append(ENTRY)
    REGISTRY.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + "\n", encoding="utf-8"
    )
    print(f"registry 条目 {len(rows)} -> {len(kept)}；已写入 {EXP_ID}")


if __name__ == "__main__":
    main()
