"""生成 regime-adapt 阶段1（快筛）独立 HTML 报告。

数据源：backtest/result/eval_regime_fast/eval_{m0fast,m3fast,b6m_ref}.json
输出：backtest/experiments/regime_adapt_stage1_report.html
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

EXP_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = EXP_ROOT / "backtest" / "result" / "eval_regime_fast"
OUT = EXP_ROOT / "backtest" / "experiments" / "regime_adapt_stage1_report.html"

ARMS = [
    ("b6m_ref", "B6-M 参考行", "DoubleEnsemble lr=0.10（现役冻结产物），csi1000 池 2016-2020 窗，直接在新协议窗推理"),
    ("m0fast", "M0-fast（对照锚点）", "单 LGBM（B3-M 冻结超参）+ CSRankNorm(H40)，全A池 2004-2020 长窗，D态下采样，自然分布权重"),
    ("m3fast", "M3-fast（实验臂）", "M0-fast + 9 个 regime 日频特征 + 风格平衡权重（D/F/T=55/30/15，48 月半衰）"),
]
POOLS = ["csi300", "csi500", "csi1000", "all"]
HORIZONS = [1, 5, 10, 20, 40]
SEED_ORDER = ["42", "1000", "2000", "3000", "4000"]


def f4(x):
    return "—" if x is None else f"{x:.4f}"


def pct(x):
    return "—" if x is None else f"{x*100:+.1f}%"


def seed_mean(rec_list, path):
    vals = []
    for r in rec_list:
        cur = r
        for k in path:
            cur = cur.get(k, {}) if isinstance(cur, dict) else {}
        if isinstance(cur, (int, float)):
            vals.append(cur)
    return float(np.mean(vals)) if vals else None


def main():
    data = {k: json.loads((EVAL_DIR / f"eval_{k}.json").read_text()) for k, _, _ in ARMS}

    # ---- 各臂全A seeds 记录 ----
    all_seeds = {k: data[k]["pools"]["all"]["seeds"] for k, _, _ in ARMS}

    def sm(arm, path, pool="all"):
        return seed_mean(list(data[arm]["pools"][pool]["seeds"].values()), path)

    rows_main = []
    for key, name, desc in ARMS:
        rows_main.append({
            "name": name, "desc": desc,
            "north": sm(key, ["mean_h", "rank_ic_mean"]),
            "north_std": data[key]["pools"]["all"]["seed_mean"].get("mean_h.rank_ic_mean_std"),
            "h1": sm(key, ["h1", "rank_ic_mean"]),
            "icir": sm(key, ["mean_h", "rank_icir"]),
        })

    def regime_mean(arm, reg, block="mean_h", metric="rank_ic_mean"):
        return seed_mean(list(all_seeds[arm].values()), ["regimes", reg, block, metric])

    def tail_mean(arm, k, reg=None):
        recs = list(all_seeds[arm].values())
        if reg is None:
            return seed_mean(recs, ["tail", f"top{k}", "ann_excess"])
        return seed_mean(recs, ["tail", f"top{k}", "regimes", reg, "ann_excess"])

    # pairwise
    m0_by_seed = {s: all_seeds["m0fast"][s]["mean_h"]["rank_ic_mean"] for s in SEED_ORDER}
    m3_by_seed = {s: all_seeds["m3fast"][s]["mean_h"]["rank_ic_mean"] for s in SEED_ORDER}
    pairwise_win = sum(1 for s in SEED_ORDER if m3_by_seed[s] >= m0_by_seed[s])

    css = """
    body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;background:#f6f7f9;color:#1c2733;}
    .wrap{max-width:1180px;margin:0 auto;padding:32px 24px 64px;}
    h1{font-size:26px;margin:0 0 4px;} h2{font-size:19px;margin:36px 0 10px;border-left:4px solid #2563eb;padding-left:10px;}
    h3{font-size:15px;margin:20px 0 8px;color:#334155;}
    .meta{color:#64748b;font-size:13px;margin-bottom:20px;}
    table{border-collapse:collapse;width:100%;background:#fff;font-size:13px;box-shadow:0 1px 3px rgba(0,0,0,.06);margin:8px 0 16px;}
    th,td{border:1px solid #e2e8f0;padding:7px 10px;text-align:center;}
    th{background:#f1f5f9;font-weight:600;} td.l,th.l{text-align:left;}
    .primary{background:#eff6ff;font-weight:600;}
    .good{color:#059669;font-weight:600;} .bad{color:#dc2626;font-weight:600;} .dim{color:#94a3b8;}
    .verdict{border-radius:10px;padding:16px 20px;margin:16px 0;background:#fef2f2;border:1px solid #fecaca;}
    .verdict h2{border:none;padding:0;margin:0 0 8px;color:#b91c1c;}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px 18px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,.05);}
    code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:12px;}
    ul{margin:6px 0;padding-left:22px;} li{margin:4px 0;line-height:1.55;}
    .note{font-size:12px;color:#64748b;}
    """

    H = []
    H.append(f"<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'><title>regime-adapt 阶段1 快筛报告</title><style>{css}</style></head><body><div class='wrap'>")
    H.append("<h1>regime-adapt 阶段1（快筛）实验报告</h1>")
    H.append("<p class='meta'>生成 2026-08-13 ｜ 计划 <code>backtest/experiments/plans/20260809_regime_adaptation_plan.md</code>（12.1/12.2 节）｜ "
             "registry：<code>regime-adapt/m0-fast-anchor</code>、<code>regime-adapt/m3-fast-feat-sample</code>、<code>regime-adapt/b6m-reference</code></p>")

    # 协议
    H.append("<h2>1. 实验设置与评估协议</h2><div class='card'><ul>")
    H.append("<li><b>目的</b>：验证「regime 日频特征 + 风格平衡样本权重」能否改善模型跨风格泛化（针对 2024-09 后 alpha 消失的诊断结论：概念漂移集中在 F/T 态极端尾部）。</li>")
    H.append("<li><b>训练</b>：全A股票池（NameDFilter 剔除指数/北交所），2004-01-02~2020-07-31，D 态按 62% 下采样（权重补偿）；标签 H40 + CSRankNorm；模型为 B3-M 冻结超参的单 LGBModel（阶段1快筛，lr=0.2，RankIC 早停）；5 种子 [42,1000,2000,3000,4000]。</li>")
    H.append("<li><b>评估</b>：冻结 70% 分层采样测试日（2020-08-03~2026-07-31，499 天：D 347 / F 95 / T 57）；期限 h1/5/10/20/40；四池（csi300/csi500/csi1000/全A）；<b>北极星 = 全A不分风格五期限均值 RankIC</b>；分风格与尾部 top22/top50 仅看全A。</li>")
    H.append("<li><b>Gate 1f（方向闸门）</b>：m3 北极星较 m0 提升 ≥0.005 且 pairwise ≥4/5，<b>或</b> F 态五期限均值提升 ≥0.010。</li>")
    H.append("<li><b>训练成本</b>：10 跑共 22 分钟（DoubleEnsemble 同数据单跑约 7 小时）。</li>")
    H.append("</ul></div>")

    # 判定
    d_north = rows_main[2]["north"] - rows_main[1]["north"]
    d_f = regime_mean("m3fast", "F") - regime_mean("m0fast", "F")
    H.append("<div class='verdict'><h2>Gate 1f 判定：未通过</h2><ul>")
    H.append(f"<li>北极星：m3 − m0 = <b class='bad'>{d_north:+.4f}</b>（阈值 +0.0050；pairwise {pairwise_win}/5，阈值 4/5）</li>")
    H.append(f"<li>F 态五期限均值：m3 − m0 = <b>{d_f:+.4f}</b>（阈值 +0.0100，差 {0.010 - d_f:.4f}）</li>")
    H.append("<li>两条通路均未达标 → 按预登记规则登记失败；「特征+平衡权重」方向在 IC 口径下证伪，但尾部口径有信号（见第 5 节发现）。</li>")
    H.append("</ul></div>")

    # 主表
    H.append("<h2>2. 主结果（全A，五种子均值）</h2><table><thead><tr>")
    H.append("<th class='l'>臂</th><th class='l'>配置</th><th class='primary'>北极星<br>mean5h RankIC</th><th>种子std</th><th>h1 RankIC</th><th>mean5h RankICIR</th></tr></thead><tbody>")
    for r in rows_main:
        hl = " class='primary'" if "M0" in r["name"] else ""
        H.append(f"<tr><td class='l'><b>{r['name']}</b></td><td class='l'>{r['desc']}</td>"
                 f"<td class='primary'>{f4(r['north'])}</td><td class='dim'>{f4(r['north_std'])}</td>"
                 f"<td>{f4(r['h1'])}</td><td>{f4(r['icir'])}</td></tr>")
    H.append("</tbody></table>")

    # 分池
    H.append("<h2>3. 分池与分期限</h2><h3>3.1 mean5h RankIC 分池（种子均值）</h3><table><thead><tr><th class='l'>臂</th>"
             + "".join(f"<th>{p}</th>" for p in POOLS) + "</tr></thead><tbody>")
    for key, name, _ in ARMS:
        H.append(f"<tr><td class='l'>{name}</td>" + "".join(
            f"<td>{f4(sm(key, ['mean_h','rank_ic_mean'], p))}</td>" for p in POOLS) + "</tr>")
    H.append("</tbody></table>")

    H.append("<h3>3.2 全A 各期限 RankIC（种子均值）</h3><table><thead><tr><th class='l'>臂</th>"
             + "".join(f"<th>h{h}</th>" for h in HORIZONS) + "<th class='primary'>mean5h</th></tr></thead><tbody>")
    for key, name, _ in ARMS:
        H.append(f"<tr><td class='l'>{name}</td>" + "".join(
            f"<td>{f4(sm(key, [f'h{h}', 'rank_ic_mean']))}</td>" for h in HORIZONS)
            + f"<td class='primary'>{f4(sm(key, ['mean_h','rank_ic_mean']))}</td></tr>")
    H.append("</tbody></table>")

    # 分风格
    H.append("<h2>4. 全A 分风格与尾部诊断</h2>")
    H.append("<h3>4.1 分风格 mean5h RankIC（种子均值；测试日 D 347 / F 95 / T 57 天）</h3>")
    H.append("<table><thead><tr><th class='l'>臂</th><th>D 防御/震荡</th><th>F 高波投机</th><th>T 大盘趋势</th></tr></thead><tbody>")
    for key, name, _ in ARMS:
        H.append(f"<tr><td class='l'>{name}</td>" + "".join(
            f"<td>{f4(regime_mean(key, reg))}</td>" for reg in ("D", "F", "T")) + "</tr>")
    H.append("</tbody></table>")

    H.append("<h3>4.2 尾部 top-k 年化超额（对全池等权，h1 标签，种子均值）</h3>")
    H.append("<table><thead><tr><th rowspan='2' class='l'>臂</th><th colspan='4'>top22</th><th colspan='4'>top50</th></tr>"
             "<tr><th>整体</th><th>D</th><th>F</th><th>T</th><th>整体</th><th>D</th><th>F</th><th>T</th></tr></thead><tbody>")
    for key, name, _ in ARMS:
        cells = []
        for k in (22, 50):
            for reg in (None, "D", "F", "T"):
                v = tail_mean(key, k, reg)
                cls = "bad" if (v is not None and v < 0) else ("good" if (v is not None and v > 0.2) else "")
                cells.append(f"<td class='{cls}'>{pct(v)}</td>")
        H.append(f"<tr><td class='l'>{name}</td>" + "".join(cells) + "</tr>")
    H.append("</tbody></table>")
    H.append("<p class='note'>年化 = 日均超额 × 238。top22 对应实盘 B4-S 策略持仓规模；F 态三臂全深负，是 2024-09 式急涨行情尾部失效的直接量化。</p>")

    # 逐种子
    H.append("<h3>4.3 逐种子北极星（pairwise 判定依据）</h3><table><thead><tr><th class='l'>臂</th>"
             + "".join(f"<th>s{s}</th>" for s in SEED_ORDER) + "<th>m3≥m0</th></tr></thead><tbody>")
    H.append("<tr><td class='l'>M0-fast</td>" + "".join(f"<td>{m0_by_seed[s]:.4f}</td>" for s in SEED_ORDER) + "<td rowspan='2'>"
             + f"{pairwise_win}/5</td></tr>")
    H.append("<tr><td class='l'>M3-fast</td>" + "".join(
        f"<td class='{'good' if m3_by_seed[s] >= m0_by_seed[s] else ''}'>{m3_by_seed[s]:.4f}</td>" for s in SEED_ORDER) + "</tr>")
    H.append("</tbody></table>")

    # 发现
    H.append("<h2>5. 发现与建议</h2><div class='card'><ul>")
    H.append("<li><b>数据变更本身就是赢家</b>：M0-fast（全A池 + 2004-2020 长窗 + D态下采样 + 单 LGBM）北极星 0.1069 已超现役 B6-M 的 0.1048，"
             "且 <b>T 态尾部 top22 从 −2.7% 修复到 +35.7%</b>——训练窗覆盖 2007/2009/2014-15 牛市样本是主要收益来源，印证「缺牛市样本」假设中的数据部分。</li>")
    H.append("<li><b>M3 的特征+平衡权重是「搬运」而非「增益」</b>：IC 从 D 态（−0.0049）挪到 F 态（+0.0089），北极星微降 0.0023；"
             "但尾部口径 M3 全面更好（top22 整体 +21.5% vs M0 +16.9%，T 态 +53.5%）。<b>IC 口径与尾部口径排序不一致</b>，而实盘策略只吃 top22 尾部。</li>")
    H.append("<li><b>F 态尾部三臂全深负</b>（−31.7% ~ −37.1%）：regime 特征与样本平衡都救不了 F 态 top22——高波投机行情中被模型排进头部的股票系统性跑输，"
             "指向 label/目标函数设计或策略层规避，特征/采样路线在此点已证伪。</li>")
    H.append("</ul><b>建议（待决策）</b>：<ul>")
    H.append("<li>将 M0-fast 数据配方（全A+长窗）作为新候选主线推进阶段 2（DoubleEnsemble + 超参选择）；</li>")
    H.append("<li>M3 不宜直接废弃：可增设「tail top22 邻域」闸门维度重评，或将 regime 特征（不带平衡权重）单独拆臂归因；</li>")
    H.append("<li>F 态尾部问题另立方向（label 设计 / 策略层风控），不再走特征/采样路线。</li>")
    H.append("</ul></div>")

    # 附录
    H.append("<h2>6. 附录：口径与产物</h2><div class='card'><ul class='note'>")
    H.append("<li>评估产物：<code>backtest/result/eval_regime_fast/eval_{m0fast,m3fast,b6m_ref}.json</code>；配置 <code>backtest/configs/regime-adapt/eval_*.yaml</code>；队列 <code>backtest/scripts/run_regime_eval_queue.sh</code>（总耗时 46 分钟）。</li>")
    H.append("<li>训练产物：<code>backtest/result/2026081*_regimeadaptfast_{m0,m3}_s*</code>（10 跑，fit 62~151s，峰值 RSS ≤ 11GB）。</li>")
    H.append("<li>月度风格标签：原 <code>monthly_regime_3style.csv</code> 于 2026-08-11 清理事故丢失，已由冻结 70%/30% 清单的 regime 列精确重建评估窗 72 个月（<code>monthly_regime_labels_eval_window_v1.csv</code>，无冲突无缺失）。</li>")
    H.append("<li>全A池修复：<code>all.txt</code> 含 4 个指数代码（SH000300/852/903/905），多期限评估入口已加股票前缀过滤（与训练池 NameDFilter 一致）；历史 h1 入口未动。</li>")
    H.append("<li>B6-M 参考行原 valid（2020-01~2021-07）与评估窗部分重叠，仅作参考不作归因对照。</li>")
    H.append("</ul></div>")

    H.append("</div></body></html>")
    OUT.write_text("\n".join(H), encoding="utf-8")
    print("written:", OUT)


if __name__ == "__main__":
    main()
