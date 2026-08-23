"""Phase M v1 单实验详细报告：主指标 + 网格 / 风格 / 分年（一指标一表）。

对照行必须是当前 Phase M v1 baseline（M0 H20 ES）。用法：
  python backtest/scripts/build_regime_phase_m_detail_report.py --spec feat
  python backtest/scripts/build_regime_phase_m_detail_report.py --spec sample
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

EXP_ROOT = Path(__file__).resolve().parents[2]
KS = ["5", "15", "50"]
HS = ["2", "3", "5", "10"]
REGS = ["D", "F", "T"]
PRIMARY_K, PRIMARY_H = "3", "5"

SPECS = {
    "feat": {
        "title": "Phase M v1 详细报告 · M0 H20 + regime 特征",
        "out": "backtest/experiments/regime_adapt_m0h20_feat_report.html",
        "hypothesis": "只加 11 列 regime 特征，标签仍 H20，日权重仍用 M0 自然分布",
        "arms": [
            {
                "key": "t5h5es",
                "name": "M0 H20 ES",
                "path": "backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json",
                "baseline": True,
            },
            {
                "key": "feat",
                "name": "M0 H20 + regime 特征",
                "path": "backtest/result/eval_regime_ablation/eval_feat.json",
                "baseline": False,
            },
        ],
    },
    "sample": {
        "title": "Phase M v1 详细报告 · M0 H20 + 样本采样",
        "out": "backtest/experiments/regime_adapt_m0h20_sample_report.html",
        "hypothesis": "特征仍是 M0，只把日权换成 M3 的 55/30/15 + 48m 半衰期",
        "arms": [
            {
                "key": "t5h5es",
                "name": "M0 H20 ES",
                "path": "backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json",
                "baseline": True,
            },
            {
                "key": "sample",
                "name": "M0 H20 + 样本采样",
                "path": "backtest/result/eval_regime_ablation/eval_sample.json",
                "baseline": False,
            },
        ],
    },
    "t5h5es": {
        "title": "Phase M v1 详细报告 · M0 H20 top5×h5 早停",
        "out": "backtest/experiments/regime_adapt_m0h20_t5h5es_report.html",
        "hypothesis": (
            "训练配方仍是 M0 H20，早停改为全A 1454 天 top5×h5 扣费净年化；"
            "2026-08-19 晋升为当前 baseline。2026-08-20 起官方主格改为"
            "五种子合成信号后再算 top5×h5"
        ),
        "arms": [
            {
                "key": "t5h5es",
                "name": "M0 H20 ES",
                "path": "backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json",
                "baseline": True,
            },
            {
                "key": "m0h20",
                "name": "M0 H20（日频 ST，前基线）",
                "path": "backtest/result/eval_regime_m0_labels/eval_m0h20_st_daily.json",
                "baseline": False,
            },
        ],
    },
    "densemble-v2": {
        "title": "Phase M v1 详细报告 · v2 + DoubleEnsemble s42",
        "out": "backtest/experiments/regime_adapt_m0h20es_densemble_report.html",
        "hypothesis": (
            "相对当前基线 v2，只把单 LGBM 换成 B6-M DoubleEnsemble；"
            "单种子 42，不作晋升依据"
        ),
        "arms": [
            {
                "key": "t5h5es",
                "name": "M0 H20 ES 官方合成信号",
                "path": "backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json",
                "baseline": True,
            },
            {
                "key": "v2s42",
                "name": "M0 H20 ES seed 42",
                "path": "backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json",
                "seed": 42,
                "baseline": False,
            },
            {
                "key": "densemble",
                "name": "DoubleEnsemble s42",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_s42_vs_v2.json",
                "baseline": False,
            },
        ],
    },
    "densemble-v3": {
        "title": "Phase M v1 详细报告 · v3 + DoubleEnsemble s42",
        "out": "backtest/experiments/regime_adapt_m0h20_t3h5es_densemble_s42_report.html",
        "hypothesis": (
            "相对当前基线 v3，只把单 LGBM 换成 B6-M DoubleEnsemble；"
            "早停对齐 top3_h5_net_ann；单种子 42 侦察，不作晋升依据"
        ),
        "arms": [
            {
                "key": "v3",
                "name": "M0 H20 t3h5es 官方合成信号",
                "path": "backtest/result/eval_regime_m0_t3h5es/eval_m0h20.json",
                "baseline": True,
            },
            {
                "key": "v3s42",
                "name": "M0 H20 t3h5es seed 42",
                "path": "backtest/result/eval_regime_m0_t3h5es/eval_m0h20.json",
                "seed": 42,
                "baseline": False,
            },
            {
                "key": "densemble",
                "name": "DoubleEnsemble s42",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_s42_vs_v3.json",
                "baseline": False,
            },
        ],
    },
    "densemble-v3-all": {
        "title": "Phase M v1 详细报告 · v3 + DoubleEnsemble",
        "out": "backtest/experiments/regime_adapt_m0h20_t3h5es_densemble_report.html",
        "hypothesis": (
            "相对当前基线 v3，只把单 LGBM 换成 B6-M DoubleEnsemble；"
            "早停对齐 top3_h5_net_ann；五种子官方合成信号"
        ),
        "arms": [
            {
                "key": "v3",
                "name": "M0 H20 t3h5es 官方合成信号",
                "path": "backtest/result/eval_regime_m0_t3h5es/eval_m0h20.json",
                "baseline": True,
            },
            {
                "key": "densemble",
                "name": "DoubleEnsemble 官方合成信号",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v3.json",
                "baseline": False,
            },
            {
                "key": "des42",
                "name": "DoubleEnsemble seed 42",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v3.json",
                "seed": 42,
                "baseline": False,
            },
            {
                "key": "des1000",
                "name": "DoubleEnsemble seed 1000",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v3.json",
                "seed": 1000,
                "baseline": False,
            },
            {
                "key": "des2000",
                "name": "DoubleEnsemble seed 2000",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v3.json",
                "seed": 2000,
                "baseline": False,
            },
            {
                "key": "des3000",
                "name": "DoubleEnsemble seed 3000",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v3.json",
                "seed": 3000,
                "baseline": False,
            },
            {
                "key": "des4000",
                "name": "DoubleEnsemble seed 4000",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v3.json",
                "seed": 4000,
                "baseline": False,
            },
        ],
    },
    "rankices": {
        "title": "Phase M v1 详细报告 · v4 · M0 H20 RankIC ES",
        "out": "backtest/experiments/regime_adapt_m0h20_rankices_report.html",
        "hypothesis": (
            "相对 v3，valid 仍是评估窗 t5h5es 帧，只把早停打分换成 daily_rank_ic；"
            "不是重跑 v1 的 499 天次日 RankIC。2026-08-22 晋升为当前模型基线 v4。"
        ),
        "arms": [
            {
                "key": "v3",
                "name": "M0 H20 t3h5es 官方合成信号（历史 v3）",
                "path": "backtest/result/eval_regime_m0_t3h5es/eval_m0h20.json",
                "baseline": False,
            },
            {
                "key": "rankices",
                "name": "M0 H20 RankIC ES 官方合成信号（当前 v4）",
                "path": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
                "baseline": True,
            },
            {
                "key": "rics42",
                "name": "RankIC 早停 seed 42",
                "path": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
                "seed": 42,
                "baseline": False,
            },
            {
                "key": "rics1000",
                "name": "RankIC 早停 seed 1000",
                "path": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
                "seed": 1000,
                "baseline": False,
            },
            {
                "key": "rics2000",
                "name": "RankIC 早停 seed 2000",
                "path": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
                "seed": 2000,
                "baseline": False,
            },
            {
                "key": "rics3000",
                "name": "RankIC 早停 seed 3000",
                "path": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
                "seed": 3000,
                "baseline": False,
            },
            {
                "key": "rics4000",
                "name": "RankIC 早停 seed 4000",
                "path": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
                "seed": 4000,
                "baseline": False,
            },
        ],
    },
    "densemble-v4-all": {
        "title": "Phase M v1 详细报告 · v4 + DoubleEnsemble",
        "out": "backtest/experiments/regime_adapt_m0h20_rankices_densemble_report.html",
        "hypothesis": (
            "相对当前基线 v4，只把单 LGBM 换成 B6-M DoubleEnsemble；"
            "早停对齐评估窗 daily_rank_ic；五种子官方合成信号"
        ),
        "arms": [
            {
                "key": "v4",
                "name": "M0 H20 RankIC ES 官方合成信号（当前 v4）",
                "path": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
                "baseline": True,
            },
            {
                "key": "densemble",
                "name": "v4 + DoubleEnsemble 官方合成信号",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v4.json",
                "baseline": False,
            },
            {
                "key": "des42",
                "name": "v4 + DoubleEnsemble seed 42",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v4.json",
                "seed": 42,
                "baseline": False,
            },
            {
                "key": "des1000",
                "name": "v4 + DoubleEnsemble seed 1000",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v4.json",
                "seed": 1000,
                "baseline": False,
            },
            {
                "key": "des2000",
                "name": "v4 + DoubleEnsemble seed 2000",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v4.json",
                "seed": 2000,
                "baseline": False,
            },
            {
                "key": "des3000",
                "name": "v4 + DoubleEnsemble seed 3000",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v4.json",
                "seed": 3000,
                "baseline": False,
            },
            {
                "key": "des4000",
                "name": "v4 + DoubleEnsemble seed 4000",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_vs_v4.json",
                "seed": 4000,
                "baseline": False,
            },
        ],
    },
    "densemble-v4": {
        "title": "Phase M v1 详细报告 · v4 + DoubleEnsemble s42",
        "out": "backtest/experiments/regime_adapt_m0h20_rankices_densemble_s42_report.html",
        "hypothesis": (
            "相对当前基线 v4，只把单 LGBM 换成 B6-M DoubleEnsemble；"
            "早停对齐评估窗 daily_rank_ic；单种子 42 侦察，不作晋升依据"
        ),
        "arms": [
            {
                "key": "v4",
                "name": "M0 H20 RankIC ES 官方合成信号（当前 v4）",
                "path": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
                "baseline": True,
            },
            {
                "key": "v4s42",
                "name": "M0 H20 RankIC ES seed 42",
                "path": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
                "seed": 42,
                "baseline": False,
            },
            {
                "key": "densemble",
                "name": "v4 + DoubleEnsemble s42",
                "path": "backtest/result/eval_regime_ablation/eval_densemble_s42_vs_v4.json",
                "baseline": False,
            },
        ],
    },
    "densemble": {
        "title": "Phase M v1 详细报告 · M0 H20 DoubleEnsemble s42",
        "out": "backtest/experiments/regime_adapt_m0h20_densemble_report.html",
        "hypothesis": "单 LGBM 换成 B6-M 超参 DoubleEnsemble；单种子 42，不作晋升依据",
        "arms": [
            {
                "key": "t5h5es",
                "name": "M0 H20 ES 五种子均值",
                "path": "backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json",
                "baseline": True,
            },
            {
                "key": "m0h20s42",
                "name": "M0 H20 seed 42（日频 ST）",
                "path": "backtest/result/eval_regime_m0_labels/eval_m0h20_st_daily.json",
                "seed": 42,
                "baseline": False,
            },
            {
                "key": "densemble",
                "name": "M0 H20 DoubleEnsemble s42",
                "path": "backtest/result/eval_regime_ablation/eval_densemble.json",
                "baseline": False,
            },
        ],
    },
}


def load_json(rel: str):
    path = EXP_ROOT / rel
    return json.loads(path.read_text()) if path.exists() else None


def seed_view(rec, seed=None):
    if rec is None:
        return None
    pool = (rec.get("pools", {}).get("all", {}) or {})
    if seed is not None:
        return (pool.get("seeds") or {}).get(str(seed))
    return pool.get("ensemble") or pool.get("seed_mean")


def _grid(sm, k, h, *, regime=None, year=None):
    if sm is None:
        return {}
    if year is not None:
        root = (sm.get("head_years") or {}).get(str(year)) or {}
    elif regime is not None:
        root = (sm.get("head_regimes") or {}).get(regime) or {}
    else:
        root = sm.get("head") or {}
    return (root.get(str(k), {}) or {}).get(str(h), {}) or {}


def cell(sm, k, h, metric, *, regime=None, year=None):
    rec = _grid(sm, k, h, regime=regime, year=year)
    if metric != "turnover":
        return rec.get(metric)
    daily = rec.get("turnover")
    period = rec.get("turnover_period")
    if period is not None:
        return daily if daily is not None else period / int(h)
    if daily is not None and daily > 1.0 / int(h) * 1.5:
        return daily / int(h)
    return daily


def fmt(x, kind="num"):
    if x is None:
        return "—"
    if kind == "ann":
        return f"{x * 100:+.1f}%"
    if kind == "vol":
        return f"{x * 100:.1f}%"
    if kind == "sharpe":
        return f"{x:.2f}"
    return f"{x:.4f}"


def metric_cells(sm, k, h, *, regime=None, year=None):
    return (
        fmt(cell(sm, k, h, "net_ann", regime=regime, year=year), "ann"),
        fmt(cell(sm, k, h, "net_ann_vol", regime=regime, year=year), "vol"),
        fmt(cell(sm, k, h, "net_sharpe", regime=regime, year=year), "sharpe"),
        fmt(cell(sm, k, h, "ann", regime=regime, year=year), "ann"),
        fmt(cell(sm, k, h, "turnover", regime=regime, year=year), "vol"),
    )


def grid_table(title, arms, data, metric, kind):
    header = "<tr><th class='l'>臂</th>"
    for k in KS:
        header += f"<th colspan='{len(HS)}'>k={k}</th>"
    header += "</tr><tr><th></th>"
    for _ in KS:
        for h in HS:
            header += f"<th>h{h}</th>"
    header += "</tr>"
    body = ""
    for arm in arms:
        sm = seed_view(data.get(arm["key"]), arm.get("seed"))
        if sm is None:
            continue
        tds = [f"<td class='l'>{arm['name']}</td>"]
        for k in KS:
            for h in HS:
                css = " class='primary'" if (k, h) == (PRIMARY_K, PRIMARY_H) else ""
                tds.append(f"<td{css}>{fmt(cell(sm, k, h, metric), kind)}</td>")
        body += "<tr>" + "".join(tds) + "</tr>"
    return f"<h3>{title}</h3><table><thead>{header}</thead><tbody>{body}</tbody></table>"


def year_table(title, arms, data, years, metric, kind):
    header = "<tr><th class='l'>臂</th>" + "".join(f"<th>{y}</th>" for y in years) + "</tr>"
    body = ""
    for arm in arms:
        sm = seed_view(data.get(arm["key"]), arm.get("seed"))
        if sm is None or not (sm.get("head_years") or {}):
            continue
        tds = [f"<td class='l'>{arm['name']}</td>"]
        for y in years:
            tds.append(f"<td>{fmt(cell(sm, PRIMARY_K, PRIMARY_H, metric, year=y), kind)}</td>")
        body += "<tr>" + "".join(tds) + "</tr>"
    return f"<h3>{title}</h3><table><thead>{header}</thead><tbody>{body}</tbody></table>"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, choices=sorted(SPECS))
    args = parser.parse_args()
    spec = SPECS[args.spec]
    arms = spec["arms"]
    data = {a["key"]: load_json(a["path"]) for a in arms}
    if not any(v is not None for v in data.values()):
        raise SystemExit(f"no eval json for spec {args.spec}")
    baseline_name = next((a["name"] for a in arms if a.get("baseline")), "M0 H20 ES")
    any_rec = next(v for v in data.values() if v is not None)
    filters = any_rec.get("filters") or {}
    years = sorted(
        {
            y
            for a in arms
            for y in ((seed_view(data.get(a["key"]), a.get("seed")) or {}).get("head_years") or {})
        },
        key=int,
    )
    n_days = cell(seed_view(any_rec), PRIMARY_K, PRIMARY_H, "n_days")
    css = """
    body{font-family:-apple-system,'PingFang SC',sans-serif;margin:0;background:#f6f7f9;color:#1c2733;}
    .wrap{max-width:1400px;margin:0 auto;padding:28px 20px 64px;}
    h1{font-size:24px;margin:0 0 6px;} h2{font-size:18px;margin:28px 0 8px;border-left:4px solid #2563eb;padding-left:10px;}
    h3{font-size:14px;margin:16px 0 6px;color:#334155;}
    .meta{color:#64748b;font-size:13px;margin-bottom:16px;}
    table{border-collapse:collapse;width:100%;background:#fff;font-size:12px;margin:6px 0 14px;}
    th,td{border:1px solid #e2e8f0;padding:5px 7px;text-align:center;}
    th{background:#f1f5f9;} td.l,th.l{text-align:left;}
    .primary{background:#eff6ff;font-weight:600;}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;margin:8px 0;}
    .note{font-size:12px;color:#64748b;}
    """
    H = [
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
        f"<title>{spec['title']}</title>",
        f"<style>{css}</style></head><body><div class='wrap'>",
        f"<h1>{spec['title']}</h1>",
        "<p class='meta'>总报告入口 <a href='phase_m_v1_report.html'>phase_m_v1_report.html</a> · "
        f"当前 baseline = <b>{baseline_name}</b> · "
        f"{spec['hypothesis']} · "
        f"全A · 2020-08-03~2026-07-31 全部 {n_days or '—'} 个交易日 · "
        f"过滤：上市≥{filters.get('min_listing_days', 60)} 日、"
        f"ST={filters.get('st_filter', '—')}、"
        f"成交额≥{filters.get('min_amount', 0):.0f} 元、剔 t+1 涨停/零量 · "
        "主格 <b>top3 × h5</b>；无北极星。</p>",
        "<h2>1. 主格 top3 × h5</h2>",
        "<table><thead><tr><th class='l'>臂</th>"
        "<th class='primary'>扣费净年化</th><th class='primary'>扣费波动</th>"
        "<th class='primary'>扣费夏普</th><th>非扣费年化</th><th>日换手</th>"
        "</tr></thead><tbody>",
    ]
    for arm in arms:
        sm = seed_view(data.get(arm["key"]), arm.get("seed"))
        if sm is None:
            H.append(
                f"<tr><td class='l'><b>{arm['name']}</b></td>"
                "<td colspan='5' class='note'>尚无评估 JSON</td></tr>"
            )
            continue
        ann, vol, sharpe, gross, to = metric_cells(sm, PRIMARY_K, PRIMARY_H)
        tag = " · 当前 baseline" if arm.get("baseline") else ""
        row_cls = " class='primary'" if arm.get("baseline") else ""
        H.append(
            f"<tr{row_cls}><td class='l'><b>{arm['name']}{tag}</b></td>"
            f"<td class='primary'>{ann}</td><td class='primary'>{vol}</td>"
            f"<td class='primary'>{sharpe}</td><td>{gross}</td><td>{to}</td></tr>"
        )
    H.append("</tbody></table>")
    H.append("<h2>2.1 网格 top∈{5,15,50} × h∈{2,3,5,10}</h2>")
    H.append(grid_table("扣费净年化", arms, data, "net_ann", "ann"))
    H.append(grid_table("扣费波动", arms, data, "net_ann_vol", "vol"))
    H.append(grid_table("扣费夏普", arms, data, "net_sharpe", "sharpe"))
    H.append(grid_table("非扣费年化", arms, data, "ann", "ann"))
    H.append(grid_table("日换手", arms, data, "turnover", "vol"))
    H.append("<h2>2.2 主格 top3 × h5 分风格</h2>")
    sub = "<th>净年化</th><th>波动</th><th>夏普</th><th>非扣费</th><th>日换手</th>"
    span = "".join(f"<th colspan='5'>{r} 态</th>" for r in REGS)
    H.append(
        f"<table><thead><tr><th class='l'>臂</th>{span}</tr>"
        f"<tr><th></th>{sub * len(REGS)}</tr></thead><tbody>"
    )
    for arm in arms:
        sm = seed_view(data.get(arm["key"]), arm.get("seed"))
        if sm is None:
            continue
        tds = [f"<td class='l'>{arm['name']}</td>"]
        for r in REGS:
            tds.extend(f"<td>{v}</td>" for v in metric_cells(sm, PRIMARY_K, PRIMARY_H, regime=r))
        H.append("<tr>" + "".join(tds) + "</tr>")
    H.append("</tbody></table>")
    H.append("<h2>2.3 主格 top3 × h5 分年</h2>")
    if years:
        H.append(year_table("扣费净年化", arms, data, years, "net_ann", "ann"))
        H.append(year_table("扣费波动", arms, data, years, "net_ann_vol", "vol"))
        H.append(year_table("扣费夏普", arms, data, years, "net_sharpe", "sharpe"))
        H.append(year_table("非扣费年化", arms, data, years, "ann", "ann"))
        H.append(year_table("日换手", arms, data, years, "turnover", "vol"))
    else:
        H.append("<p class='note'>尚无分年切片。</p>")
    H.append(
        "<div class='card note'><ul>"
        "<li>这是 Phase M v1 单实验详细报告；总报告只放主指标。</li>"
        f"<li>第一行固定为当前 baseline {baseline_name}。本口径不是完整 TopkDropout 回测。</li>"
        "</ul></div></div></body></html>"
    )
    out = EXP_ROOT / spec["out"]
    out.write_text("\n".join(H), encoding="utf-8")
    print("written:", out)


if __name__ == "__main__":
    main()
