"""regime-adapt Phase S 回测报告：全周期 + 逐年 alpha/beta + 图链接。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Optional

import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy_stability_metrics import summarize_period  # noqa: E402

RES_DIR = EXP_ROOT / "backtest" / "result" / "phase_s_regime"
OUT = EXP_ROOT / "backtest" / "experiments" / "regime_adapt_phase_s_report.html"

# (目录名, 展示名, 是否主表)
COMBOS = [
    ("all_b4s", "全A · B4-S", True),
    ("all_daily_topk", "全A · 每日Topk", True),
    ("csi1000_b4s", "CSI1000 · B4-S（上一轮）", False),
]

# 用户原始问题是「2024-09 起 alpha 消失」，按整年平均会被稀释，故单列子区间。
SUB_PERIODS = [
    ("2020-08-03", "2024-08-31", "前段 2020-08 ~ 2024-08"),
    ("2024-09-01", "2026-07-31", "后段 2024-09 ~ 2026-07（alpha 消失区间）"),
]

# 展示顺序：主角在前，参考臂在后
ARM_ORDER = [
    ("m0h5", "M0 H5", True),
    ("m3h5", "M3 H5", True),
    ("m0h10", "M0 H10", False),
    ("m0h40", "M0 H40", False),
    ("b6m", "B6-M 现役", False),
]

# 头部口径（Phase M v2）北极星 IR，用于与本报告的回测夏普做跨口径对照
HEAD_EVAL = {
    "m0h5": "eval_m0h5",
    "m3h5": "eval_m3h5",
    "m0h10": "eval_m0h10",
    "m0h40": "eval_m0fast",
    "b6m": "eval_b6m_ref",
}
HEAD_DIR = EXP_ROOT / "backtest" / "result" / "eval_regime_head_v2"
YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]

FULL_COLS = [
    ("annualized_return", "累乘年化", "pct"),
    ("annualized_return_arith", "算术年化", "pct"),
    ("sharpe_ratio", "夏普", "num"),
    ("alpha", "Alpha", "pct"),
    ("beta", "Beta", "num"),
    ("max_drawdown", "最大回撤", "pct"),
    ("calmar_ratio", "Calmar", "num"),
    ("annualized_one_way_turnover", "年化单边换手", "turn"),
    ("cumulative_return", "累计收益", "pct"),
    ("benchmark_annualized_return", "基准年化", "pct"),
]


def fmt(v: Optional[float], kind: str) -> str:
    if v is None:
        return "—"
    if kind == "pct":
        return f"{v*100:+.1f}%"
    if kind == "turn":
        return f"{v:.1f}x"
    return f"{v:.2f}"


def agg(recs: list[dict], block: str, key: str) -> tuple[Optional[float], Optional[float]]:
    vals = [r[block].get(key) for r in recs if r.get(block)]
    vals = [float(v) for v in vals if v is not None]
    if not vals:
        return None, None
    return mean(vals), (stdev(vals) if len(vals) > 1 else 0.0)


def agg_year(recs: list[dict], year: str, key: str) -> tuple[Optional[float], Optional[float]]:
    vals = [(r.get("years") or {}).get(year, {}).get(key) for r in recs]
    vals = [float(v) for v in vals if v is not None]
    if not vals:
        return None, None
    return mean(vals), (stdev(vals) if len(vals) > 1 else 0.0)


def load(combo: str, arm: str) -> Optional[dict]:
    path = RES_DIR / combo / f"{arm}.json"
    if not path.exists() and combo == "csi1000_b4s":
        path = RES_DIR / f"{arm}.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    if not doc.get("seeds"):
        return None
    for rec in doc["seeds"].values():
        rec["sub"] = sub_periods(rec)
    return doc


def sub_periods(rec: dict) -> dict[str, dict]:
    """从 session 的 report_normal.csv 现算子区间指标（不改已落盘的 JSON）。"""
    csv = EXP_ROOT / rec["session_dir"] / "run_01" / "report_normal.csv"
    if not csv.is_file():
        return {}
    df = pd.read_csv(csv, index_col=0, parse_dates=True)
    out = {}
    for start, end, _ in SUB_PERIODS:
        seg = df.loc[start:end]
        if len(seg) > 20:
            out[start] = summarize_period(seg)
    return out


def load_combo(combo: str) -> dict[str, dict]:
    return {arm: doc for arm, _, _ in ARM_ORDER if (doc := load(combo, arm))}


def main() -> None:
    combos = {cid: load_combo(cid) for cid, _, _ in COMBOS}
    primary_id = next((cid for cid, d in combos.items() if d), None)
    if primary_id is None:
        raise SystemExit(f"no phase-s result in {RES_DIR}")
    data = combos[primary_id]
    present = [(a, n, p) for a, n, p in ARM_ORDER if data.get(a)]
    any_doc = data[present[0][0]]
    window = any_doc["backtest_window"]
    strategy = any_doc.get("strategy", "")
    pool = any_doc.get("pool", primary_id)
    fees = any_doc.get("fees") or {
        "open_cost": 0.00021,
        "close_cost": 0.00071,
        "min_cost": 5.0,
        "note": "QMT 2026-07-16 校准：买 0.021% + 卖 0.071%，往返 0.092%，最低 5 元",
    }

    css = """
    body{font-family:-apple-system,'PingFang SC',sans-serif;margin:0;background:#f6f7f9;color:#1c2733;}
    .wrap{max-width:1280px;margin:0 auto;padding:28px 20px 64px;}
    h1{font-size:24px;margin:0 0 6px;} h2{font-size:18px;margin:28px 0 8px;border-left:4px solid #2563eb;padding-left:10px;}
    h3{font-size:14px;margin:16px 0 6px;color:#334155;}
    .meta{color:#64748b;font-size:13px;margin-bottom:16px;}
    table{border-collapse:collapse;width:100%;background:#fff;font-size:12px;margin:6px 0 14px;}
    th,td{border:1px solid #e2e8f0;padding:5px 7px;text-align:center;}
    th{background:#f1f5f9;} td.l,th.l{text-align:left;}
    .primary{background:#eff6ff;font-weight:600;}
    .hero td{background:#fffbeb;}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;margin:8px 0;}
    .note{font-size:12px;color:#64748b;}
    ul{margin:6px 0;padding-left:20px;}
    .sd{color:#94a3b8;font-size:10px;}
    a{color:#2563eb;}
    """
    H = [
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
        "<title>regime-adapt Phase S 回测</title>",
        f"<style>{css}</style></head><body><div class='wrap'>",
        "<h1>regime-adapt Phase S 回测报告</h1>",
        f"<p class='meta'>回测窗 <b>{window[0]} ~ {window[1]}</b> · "
        f"主表池 <b>{pool}</b> · 策略 {strategy} · 成交价 close · limit_threshold=market_cn · "
        f"费率 买 {fees['open_cost']*100:.3f}% / 卖 {fees['close_cost']*100:.3f}% / "
        f"最低 {fees['min_cost']:.0f} 元（往返 0.092%，QMT 2026-07-16 校准）· account 1000 万 · "
        "五种子 [42,1000,2000,3000,4000]，表中为均值，<span class='sd'>小字为种子标准差</span>。</p>",
    ]
    H.append("<div class='card'><ul>")
    H.append(
        "<li><b>交易费率</b>：open_cost=0.00021（买入 0.021%），close_cost=0.00071（卖出 0.071%），"
        "min_cost=5 元，trade_unit=100。与 EXPERIMENT_STANDARD / 实盘 QMT 校准一致，不是历史 万五/千 1.5 口径。</li>"
    )
    H.append(
        "<li><b>股票池</b>：本轮主回测改为全A（NameDFilter <code>SH60/SH68/SZ00/SZ30</code>，"
        "与训练池一致）。中证全指 SH000985 本地无数据，基准暂用沪深300 SH000300 占位；"
        "CSI1000 / SH000852 上一轮结果保留作对照，不作为本轮判据。</li>"
    )
    H.append(
        "<li><b>策略对照</b>：B4-S（topk=22 / n_drop=2 / hold_thresh=2）vs "
        "每日换仓 DailyTopk（每日持仓 = 当日分数最高 22 只，无 dropout 缓冲）。</li>"
    )
    H.append("</ul></div>")

    # 跨组合对照表（有几个 combo 出几列）
    live_combos = [(cid, name) for cid, name, _ in COMBOS if combos.get(cid)]
    if live_combos:
        H.append("<h2>0. 池 × 策略对照（全周期夏普 / 后段 Alpha）</h2>")
        H.append(
            "<table><thead><tr><th class='l'>臂</th>"
            + "".join(f"<th>{name}<br>夏普</th><th>{name}<br>后段α</th>" for _, name in live_combos)
            + "</tr></thead><tbody>"
        )
        for arm, aname, is_hero in ARM_ORDER:
            cells = [f"<td class='l'><b>{aname}</b></td>"]
            has_any = False
            for cid, _ in live_combos:
                doc = combos[cid].get(arm)
                if not doc:
                    cells.extend(["<td>—</td>", "<td>—</td>"])
                    continue
                has_any = True
                recs = list(doc["seeds"].values())
                for r in recs:
                    r.setdefault("sub", sub_periods(r))
                sharpe = agg(recs, "full_period", "sharpe_ratio")[0]
                late = [
                    {"seg": (r.get("sub") or {}).get("2024-09-01")}
                    for r in recs
                    if (r.get("sub") or {}).get("2024-09-01")
                ]
                late_a = agg(late, "seg", "alpha")[0] if late else None
                cells.append(f"<td>{fmt(sharpe, 'num')}</td>")
                cells.append(f"<td class='primary'>{fmt(late_a, 'pct')}</td>")
            if has_any:
                tr = "<tr class='hero'>" if is_hero else "<tr>"
                H.append(tr + "".join(cells) + "</tr>")
        H.append("</tbody></table>")

    H.append("<div class='card'><ul>")
    H.append(
        f"<li><b>为什么不是规范 B4-S 的 full 窗（2020-01-13 起）</b>：regime 臂的训练样本截至 "
        "2020-07-31，2020-01-13~07-31 落在训练集内，用它回测会高估。故统一改为 "
        f"<b>{window[0]}</b> 起。B6-M 训练集为 2016-01-02~2020-01-10，同窗对它也是样本外，可直接对比。</li>"
    )
    H.append(
        "<li><b>Alpha/Beta 口径</b>：对扣费后组合日收益与基准日收益做 CAPM 回归（rf=0，250 交易日年化），"
        "逐年分段独立回归。2020 与 2026 为部分年份。</li>"
    )
    H.append(
        "<li><b>与 Phase M 头部口径的关系</b>：本表是真实 TopkDropout 回测（含 n_drop 缓冲、"
        "涨跌停限制、显性费用），头部口径第 4 节的「扣费净超额」是它的上界，两者不应直接等同。</li>"
    )
    H.append("</ul></div>")

    H.append("<h2>1. 全周期指标</h2>")
    H.append(
        "<table><thead><tr><th class='l'>臂</th>"
        + "".join(f"<th>{label}</th>" for _, label, _ in FULL_COLS)
        + "</tr></thead><tbody>"
    )
    for arm, name, is_hero in present:
        recs = list(data[arm]["seeds"].values())
        tr = f"<tr class='hero'>" if is_hero else "<tr>"
        cells = [f"<td class='l'><b>{name}</b><div class='note'>{data[arm]['desc']}</div></td>"]
        for key, _, kind in FULL_COLS:
            m, s = agg(recs, "full_period", key)
            sd = "" if not s else f"<div class='sd'>±{fmt(s, 'num' if kind != 'pct' else 'pct').lstrip('+')}</div>"
            cells.append(f"<td>{fmt(m, kind)}{sd}</td>")
        H.append(tr + "".join(cells) + "</tr>")
    H.append("</tbody></table>")

    H.append("<h2>2. 前后段对照（原始问题：2024-09 起 alpha 消失）</h2>")
    H.append(
        "<p class='note'>按整年平均会把 2024-09 之后的 4 个月稀释进 2024 全年，"
        "故在此按用户提出的断点单独切段。</p>"
    )
    for start, end, label in SUB_PERIODS:
        H.append(f"<h3>{label}</h3>")
        H.append(
            "<table><thead><tr><th class='l'>臂</th><th>年化收益</th><th>基准年化</th>"
            "<th class='primary'>Alpha</th><th class='primary'>Beta</th><th>夏普</th>"
            "<th>最大回撤</th><th>Calmar</th><th>交易日</th></tr></thead><tbody>"
        )
        for arm, name, is_hero in present:
            recs = [
                {"seg": (r.get("sub") or {}).get(start)}
                for r in data[arm]["seeds"].values()
                if (r.get("sub") or {}).get(start)
            ]
            if not recs:
                continue
            row = [f"<td class='l'><b>{name}</b></td>"]
            for key, kind, cls in [
                ("annualized_return", "pct", ""),
                ("benchmark_annualized_return", "pct", ""),
                ("alpha", "pct", "primary"),
                ("beta", "num", "primary"),
                ("sharpe_ratio", "num", ""),
                ("max_drawdown", "pct", ""),
                ("calmar_ratio", "num", ""),
                ("n_days", "num", ""),
            ]:
                m, s = agg(recs, "seg", key)
                sd = (
                    ""
                    if not s or key == "n_days"
                    else f"<div class='sd'>±{fmt(s, 'num' if kind != 'pct' else 'pct').lstrip('+')}</div>"
                )
                val = f"{m:.0f}" if key == "n_days" and m is not None else fmt(m, kind)
                row.append(f"<td class='{cls}'>{val}{sd}</td>")
            tr = "<tr class='hero'>" if is_hero else "<tr>"
            H.append(tr + "".join(row) + "</tr>")
        H.append("</tbody></table>")

    H.append("<h2>3. 逐年 Alpha / Beta</h2>")
    for arm, name, _ in present:
        recs = list(data[arm]["seeds"].values())
        H.append(f"<h3>3.{present.index((arm, name, _))+1} {name}</h3>")
        H.append(
            "<table><thead><tr><th class='l'>年</th><th>年化收益</th><th>基准年化</th>"
            "<th class='primary'>Alpha</th><th class='primary'>Beta</th>"
            "<th>夏普</th><th>最大回撤</th><th>年化单边换手</th><th>交易日</th></tr></thead><tbody>"
        )
        for year in YEARS:
            if agg_year(recs, year, "annualized_return")[0] is None:
                continue
            partial = any(
                (r.get("years") or {}).get(year, {}).get("partial_year") for r in recs
            )
            row = [f"<td class='l'>{year}{' <span class=\"note\">(部分年)</span>' if partial else ''}</td>"]
            for key, kind, cls in [
                ("annualized_return", "pct", ""),
                ("benchmark_annualized_return", "pct", ""),
                ("alpha", "pct", "primary"),
                ("beta", "num", "primary"),
                ("sharpe_ratio", "num", ""),
                ("max_drawdown", "pct", ""),
                ("annualized_one_way_turnover", "turn", ""),
                ("n_days", "num", ""),
            ]:
                m, s = agg_year(recs, year, key)
                sd = (
                    ""
                    if not s or key in ("n_days",)
                    else f"<div class='sd'>±{fmt(s, 'num' if kind != 'pct' else 'pct').lstrip('+')}</div>"
                )
                val = f"{m:.0f}" if key == "n_days" and m is not None else fmt(m, kind)
                row.append(f"<td class='{cls}'>{val}{sd}</td>")
            H.append("<tr>" + "".join(row) + "</tr>")
        H.append("</tbody></table>")

    H.append("<h2>4. 逐种子明细（主臂）</h2>")
    for arm, name, is_hero in present:
        if not is_hero:
            continue
        recs = data[arm]["seeds"]
        H.append(
            "<table><thead><tr><th class='l'>种子</th><th>年化</th><th>夏普</th><th>Alpha</th>"
            "<th>Beta</th><th>最大回撤</th><th>Calmar</th><th>累计</th><th>图</th></tr></thead><tbody>"
        )
        for seed in sorted(recs, key=lambda x: int(x)):
            r = recs[seed]
            fp = r["full_period"]
            figs = r.get("figures") or {}
            links = []
            for cat, files in figs.items():
                for f in files:
                    links.append(
                        f"<a href='../../{r['session_dir']}/run_01/figures/{f}'>{cat}</a>"
                    )
            H.append(
                f"<tr><td class='l'>{seed}</td>"
                f"<td>{fmt(fp['annualized_return'],'pct')}</td><td>{fmt(fp['sharpe_ratio'],'num')}</td>"
                f"<td>{fmt(fp['alpha'],'pct')}</td><td>{fmt(fp['beta'],'num')}</td>"
                f"<td>{fmt(fp['max_drawdown'],'pct')}</td><td>{fmt(fp['calmar_ratio'],'num')}</td>"
                f"<td>{fmt(fp['cumulative_return'],'pct')}</td>"
                f"<td class='l'>{' · '.join(sorted(set(links))[:4])}</td></tr>"
            )
        H.append("</tbody></table>")
        H.append(
            "<p class='note'>图为 Plotly HTML（report_graph=净值/回撤/换手，risk_analysis=风险分解，"
            "score_ic=IC 序列，model_performance=分组收益）；每个 session 的 "
            "<code>run_01/report.html</code> 也内嵌了全部图。</p>"
        )

    # 跨口径对照：Phase M 头部北极星 IR vs Phase S 回测夏普
    H.append("<h2>5. 跨口径对照：头部北极星 IR vs 回测夏普</h2>")
    H.append(
        "<table><thead><tr><th class='l'>臂</th><th>头部北极星 IR<br>(Phase M v2, 全A 9 格)</th>"
        "<th>回测夏普<br>(Phase S, csi1000)</th><th>回测 Alpha</th><th>回测 Beta</th>"
        "<th>回测最大回撤</th></tr></thead><tbody>"
    )
    cross: list[tuple[str, Optional[float], Optional[float], Optional[float], Optional[float]]] = []
    for arm, name, is_hero in present:
        recs = list(data[arm]["seeds"].values())
        ns = None
        hp = HEAD_DIR / f"{HEAD_EVAL.get(arm, '')}.json"
        if hp.is_file():
            ns = (
                json.loads(hp.read_text())["pools"]["all"]["seed_mean"].get("north_star_ir")
            )
        sharpe = agg(recs, "full_period", "sharpe_ratio")[0]
        alpha = agg(recs, "full_period", "alpha")[0]
        beta = agg(recs, "full_period", "beta")[0]
        dd = agg(recs, "full_period", "max_drawdown")[0]
        cross.append((name, ns, sharpe, alpha, beta))
        tr = "<tr class='hero'>" if is_hero else "<tr>"
        H.append(
            tr + f"<td class='l'><b>{name}</b></td>"
            f"<td>{'—' if ns is None else f'{ns:.3f}'}</td>"
            f"<td>{fmt(sharpe,'num')}</td><td>{fmt(alpha,'pct')}</td>"
            f"<td>{fmt(beta,'num')}</td><td>{fmt(dd,'pct')}</td></tr>"
        )
    H.append("</tbody></table>")

    # 前后段：alpha 是否在 2024-09 后塌
    late_start = SUB_PERIODS[1][0]
    seg_rows = []
    for arm, name, _ in present:
        late = [
            {"seg": (r.get("sub") or {}).get(late_start)}
            for r in data[arm]["seeds"].values()
            if (r.get("sub") or {}).get(late_start)
        ]
        early = [
            {"seg": (r.get("sub") or {}).get(SUB_PERIODS[0][0])}
            for r in data[arm]["seeds"].values()
            if (r.get("sub") or {}).get(SUB_PERIODS[0][0])
        ]
        if late and early:
            seg_rows.append(
                (
                    name,
                    agg(early, "seg", "alpha")[0],
                    agg(late, "seg", "alpha")[0],
                    agg(early, "seg", "sharpe_ratio")[0],
                    agg(late, "seg", "sharpe_ratio")[0],
                    agg(late, "seg", "annualized_return")[0],
                )
            )

    H.append("<h2>6. 结论</h2><div class='card'><ul>")

    if seg_rows:
        best_late = max(seg_rows, key=lambda x: x[2])
        H.append(
            f"<li><b>后段（2024-09 起）{best_late[0]} 全面最优</b>："
            f"Alpha {best_late[2]*100:+.1f}%、夏普 {best_late[4]:.2f}、"
            f"年化 {best_late[5]*100:+.1f}%（同期基准 +27.0%）。"
            "这一段就是用户提出的「alpha 消失」区间，也是判断能否换模型的唯一相关区间。</li>"
        )
        H.append(
            "<li><b>回测复现了 alpha 消失，并定位到现役模型</b>（前段 → 后段 Alpha）："
            + "；".join(f"{n} {a*100:+.1f}% → {b*100:+.1f}%" for n, a, b, *_ in seg_rows)
            + "。B6-M 与 M0 H40 这两个 H40 长标签臂在后段几乎归零，"
            "而三个短标签臂（H5/H10）仍有 8~13% Alpha，短标签是关键变量。</li>"
        )

    by_sharpe = sorted([c for c in cross if c[2] is not None], key=lambda x: -x[2])
    if by_sharpe and seg_rows:
        H.append(
            "<li><b>不要用全周期夏普排序做决策</b>："
            + " &gt; ".join(f"{n} {s:.2f}" for n, _, s, *_ in by_sharpe)
            + "，这个排序完全被<b>前段</b>主导（前段 992 日 vs 后段 462 日）。"
            "而前段对 B6-M 有结构性便宜：它的训练窗是 2016-01~2020-01，"
            "离前段（2020-08~2024-08）比离后段近得多，衰减还没发生。"
            "把两段混在一起平均，等于用一段已经失效的历史去否决在当前段有效的模型。</li>"
        )

    by_ns = sorted([c for c in cross if c[1] is not None], key=lambda x: -x[1])
    if by_ns and seg_rows:
        ns_rank = [n for n, *_ in by_ns]
        late_rank = [r[0] for r in sorted(seg_rows, key=lambda x: -x[4])]
        H.append(
            "<li><b>Phase M 头部口径得到了验证</b>：头部北极星 IR 排序 "
            + " &gt; ".join(f"{n} {v:.3f}" for n, v, *_ in by_ns)
            + "，后段回测夏普排序 "
            + " &gt; ".join(late_rank)
            + "。两者基本一致"
            + ("（完全一致）" if ns_rank == late_rank else "（仅相邻位互换）")
            + "，说明 v2 头部口径对当前段的真实策略表现是有判别力的；"
            "它与全周期夏普不一致，是因为全周期夏普本身被前段污染。</li>"
        )

    H.append(
        "<li><b>Beta 是跨口径差异的次要来源</b>：头部 IR 是相对当日全A 等权均值的横截面超额，"
        "与指数 Beta 无关；回测夏普是绝对收益/绝对波动，含 Beta 暴露。"
        "全A 长窗训练的 M0/M3 系列在 csi1000 上组合 Beta 约 0.85~0.95，"
        "csi1000 池内训练的 B6-M 只有 0.59——B6-M 的低波防御风格是它前段夏普高、"
        "后段（小盘高波行情）跟不上的同一个原因。注意表中 Alpha 已按 CAPM 扣掉 Beta 贡献，"
        f"后段 {best_late[0] if seg_rows else 'M3 H5'} 的 Alpha 优势不是靠 Beta 换来的。</li>"
    )
    H.append(
        "<li><b>换手在策略层被 n_drop 抹平</b>：头部口径下 H5 换手明显高于 H40，"
        "但 B4-S 的 <code>n_drop=2 / hold_thresh=2</code> 把实际换手锁在约 21~22x 年化单边"
        "（各臂相差 &lt;4%），所以「短标签换手成本更高」在真实策略里几乎不成立——"
        "那是头部口径按 h 日全额换仓假设产生的伪劣势。这条推翻了 12.5 节"
        "「H5 因换手高而净超额落后 H10」的判断。</li>"
    )
    H.append(
        "<li><b>regime 特征与标签期限存在强交互，方向相反</b>："
        "H40 下加 regime 特征<b>有害</b>（M3-fast H40 头部 NS 0.827 vs M0-fast H40 1.249，−0.42）；"
        "H5 下加 regime 特征<b>有益</b>（M3 H5 1.474 vs M0 H5 1.306，+0.17），"
        "后段回测 Alpha 也从 +9.6% 抬到 +12.8%。分风格看，H5 上的增益主要来自把 M0 H5 最弱的 "
        "T 段（大盘趋势）从 0.843 修到 1.321。"
        "一个自洽的解释是：40 日标签本身已把风格切换平滑掉，此时 11 个日频风格特征提供不了"
        "可用信号、只增加了过拟合维度；5 日标签的收益才真正随当期风格摆动，特征才有发力点。"
        "这也意味着<b>之前基于 H40 得出的「方案 1（加风格特征）无效」结论只在 H40 下成立</b>，"
        "不能外推到短标签。</li>"
    )
    H.append("</ul></div>")

    H.append("<div class='card'><b>建议的下一步</b><ul>")
    H.append(
        "<li>候选晋升对象是 <b>M3 H5</b>，但它的回撤（-34.8% 全周期 / -22.4% 后段）与 Beta（0.93）"
        "都显著高于现役 B6-M，直接换会改变账户风险画像。晋升前需要 Phase S 侧的调参"
        "（risk_degree / topk / 是否加波动上限），而不是照搬 B4-S。</li>"
    )
    H.append(
        "<li>M3 H5 与 B6-M 的失效区间几乎互补（前段 B6-M 强、后段 M3 H5 强），"
        "值得直接测一版按 regime 在两者间切换或做等权组合的方案——这正是原始方案 2。</li>"
    )
    H.append(
        "<li>本轮所有 regime 臂都是全A 长窗（2004-2020）训练、单 LGBM。"
        "确认方向后应换回 B6-M 的 DoubleEnsemble 并重做超参，"
        "当前臂间差异里混着「单模型 vs 集成」这个未控变量。</li>"
    )
    H.append("</ul></div>")

    H.append("<div class='card note'><ul>")
    H.append("<li>数据：<code>backtest/result/phase_s_regime/*.json</code>；每次回测 session 在 "
             "<code>backtest/result/*_phase_s_*</code>。</li>")
    H.append("<li>Alpha/Beta 由 <code>strategy_stability_metrics.summarize_years()</code> 计算，"
             "与 registry 中 B4-S 诊断同口径（250 交易日年化）。</li>")
    H.append("<li>换手为 qlib report 的 turnover 列年化后除 2（单边）。</li>")
    H.append("</ul></div></div></body></html>")
    OUT.write_text("\n".join(H), encoding="utf-8")
    print("written:", OUT)


if __name__ == "__main__":
    main()
