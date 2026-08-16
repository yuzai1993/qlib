"""M0 改标签实验的 Phase M v1 详细报告。

主格 top5×h5 的扣费净年化/波动/夏普，外加网格、风格、分年表。
总报告入口：phase_m_v1_report.html。只收录 M0 臂。
"""
from __future__ import annotations

import json
from pathlib import Path

EXP_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = EXP_ROOT / "backtest" / "result" / "eval_regime_m0_labels"
OUT = EXP_ROOT / "backtest" / "experiments" / "regime_adapt_m0_label_report.html"

ARMS = [
    ("m0h1", "M0 H1", 1),
    ("m0h2", "M0 H2", 2),
    ("m0h3", "M0 H3", 3),
    ("m0h5", "M0 H5", 5),
    ("m0h10", "M0 H10", 10),
    ("m0h20", "M0 H20", 20),
    ("m0h40", "M0 H40", 40),
]
KS = ["5", "15", "50"]
HS = ["2", "3", "5", "10"]
REGS = ["D", "F", "T"]
PRIMARY_K, PRIMARY_H = "5", "5"
BASELINE_KEY = "m0h20"


def load_arm(key: str):
    path = EVAL_DIR / f"eval_{key}.json"
    return json.loads(path.read_text()) if path.exists() else None


def seed_mean(rec):
    if rec is None:
        return None
    return (rec.get("pools", {}).get("all", {}) or {}).get("seed_mean")


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
    # 新口径 turnover 已是日换手；旧 JSON 只有 period、无 turnover_period
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
    """净年化 / 波动 / 夏普 / 非扣费年化 / 日换手。"""
    return (
        fmt(cell(sm, k, h, "net_ann_excess", regime=regime, year=year), "ann"),
        fmt(cell(sm, k, h, "net_ann_vol", regime=regime, year=year), "vol"),
        fmt(cell(sm, k, h, "net_sharpe", regime=regime, year=year), "sharpe"),
        fmt(cell(sm, k, h, "ann_excess", regime=regime, year=year), "ann"),
        fmt(cell(sm, k, h, "turnover", regime=regime, year=year), "vol"),
    )


def grid_table(title, data, metric, kind):
    header = "<tr><th class='l'>臂</th>"
    for k in KS:
        header += f"<th colspan='{len(HS)}'>k={k}</th>"
    header += "</tr><tr><th></th>"
    for _ in KS:
        for h in HS:
            header += f"<th>h{h}</th>"
    header += "</tr>"
    body = ""
    for key, name, home_h in ARMS:
        sm = seed_mean(data.get(key))
        if sm is None:
            continue
        tds = [f"<td class='l'>{name}</td>"]
        for k in KS:
            for h in HS:
                v = cell(sm, k, h, metric)
                css = " class='primary'" if (k, h) == (PRIMARY_K, PRIMARY_H) else ""
                if int(h) == home_h and not css:
                    css = " class='home'"
                tds.append(f"<td{css}>{fmt(v, kind)}</td>")
        body += "<tr>" + "".join(tds) + "</tr>"
    return f"<h3>{title}</h3><table><thead>{header}</thead><tbody>{body}</tbody></table>"


def years_in(data) -> list[str]:
    found: set[str] = set()
    for key, _, _ in ARMS:
        sm = seed_mean(data.get(key))
        if sm:
            found.update((sm.get("head_years") or {}).keys())
    return sorted(found, key=int)


def year_table(title, data, years, metric, kind):
    header = "<tr><th class='l'>臂</th>" + "".join(f"<th>{y}</th>" for y in years) + "</tr>"
    body = ""
    for key, name, _ in ARMS:
        sm = seed_mean(data.get(key))
        if sm is None or not (sm.get("head_years") or {}):
            continue
        tds = [f"<td class='l'>{name}</td>"]
        for y in years:
            tds.append(f"<td>{fmt(cell(sm, PRIMARY_K, PRIMARY_H, metric, year=y), kind)}</td>")
        body += "<tr>" + "".join(tds) + "</tr>"
    return f"<h3>{title}</h3><table><thead>{header}</thead><tbody>{body}</tbody></table>"


def main() -> None:
    data = {k: load_arm(k) for k, _, _ in ARMS}
    if not any(v is not None for v in data.values()):
        raise SystemExit(f"no eval json in {EVAL_DIR}")
    any_rec = next(v for v in data.values() if v is not None)
    filters = any_rec.get("filters") or {}
    n_days = cell(seed_mean(any_rec), PRIMARY_K, PRIMARY_H, "n_days")
    years = years_in(data)

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
    .home{background:#fef3c7;}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;margin:8px 0;}
    .note{font-size:12px;color:#64748b;}
    """
    H = [
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
        "<title>Phase M v1 · M0 训练标签期限</title>",
        f"<style>{css}</style></head><body><div class='wrap'>",
        "<h1>Phase M v1 详细报告 · M0 训练标签期限</h1>",
        f"<p class='meta'>总报告入口 <a href='phase_m_v1_report.html'>phase_m_v1_report.html</a> · "
        "当前 baseline = <b>M0 H20</b> · "
        f"全A · 2020-08-03~2026-07-31 全部 {n_days or '—'} 个交易日 · "
        f"过滤：上市≥{filters.get('min_listing_days', 60)} 日、"
        f"ST={filters.get('st_filter', '—')}、"
        f"成交额≥{filters.get('min_amount', 0):.0f} 元、剔 t+1 涨停/零量 · "
        "主格 <b>top5 × h5</b>；无北极星。"
        "日换手 = 相隔 h 日的单边换手 / h（h=5 全换仓 → 日换手 20%）；"
        "年化成本 = 238 × 日换手 × 0.092%；"
        "扣费净年化 = 非扣费年化 − 年化成本；"
        "扣费波动 = 超额序列 HAC 年化标准差；扣费夏普 = 净年化 / 波动。</p>",
    ]

    cols = (
        "<th class='primary'>扣费净年化</th><th class='primary'>扣费波动</th>"
        "<th class='primary'>扣费夏普</th><th>非扣费年化</th><th>日换手</th>"
    )
    H.append("<h2>1. 主格 top5 × h5</h2>")
    H.append(f"<table><thead><tr><th class='l'>臂</th>{cols}</tr></thead><tbody>")
    primary_order = [a for a in ARMS if a[0] == BASELINE_KEY] + [
        a for a in ARMS if a[0] != BASELINE_KEY
    ]
    ranked = []
    for key, name, hh in primary_order:
        sm = seed_mean(data.get(key))
        if sm is None:
            continue
        sh = cell(sm, PRIMARY_K, PRIMARY_H, "net_sharpe")
        ranked.append((name, sh if sh is not None else -1e9))
        ann, vol, sharpe, gross, to = metric_cells(sm, PRIMARY_K, PRIMARY_H)
        tag = " · 当前 baseline" if key == BASELINE_KEY else ""
        row_cls = " class='primary'" if key == BASELINE_KEY else ""
        H.append(
            f"<tr{row_cls}><td class='l'><b>{name}{tag}</b>"
            f"<div class='note'>训练标签 H{hh}</div></td>"
            f"<td class='primary'>{ann}</td><td class='primary'>{vol}</td>"
            f"<td class='primary'>{sharpe}</td><td>{gross}</td><td>{to}</td></tr>"
        )
    H.append("</tbody></table>")
    ranked.sort(key=lambda x: -x[1])
    if ranked:
        H.append(
            "<p class='note'>第一行固定为当前 baseline M0 H20；其余按训练标签期限排列。"
            "主格扣费夏普排序："
            + " &gt; ".join(n for n, _ in ranked)
            + "。</p>"
        )

    H.append("<h2>2.1 网格 top∈{5,15,50} × h∈{2,3,5,10}</h2>")
    H.append(grid_table("扣费净年化", data, "net_ann_excess", "ann"))
    H.append(grid_table("扣费波动", data, "net_ann_vol", "vol"))
    H.append(grid_table("扣费夏普", data, "net_sharpe", "sharpe"))
    H.append(grid_table("非扣费年化", data, "ann_excess", "ann"))
    H.append(grid_table("日换手", data, "turnover", "vol"))
    H.append("<p class='note'>蓝底为主格；黄底为该臂训练标签期限落在网格内的格子。</p>")

    slice_head = (
        "<tr><th class='l'>臂</th>{span}</tr><tr><th></th>{sub}</tr>"
    )
    sub = "<th>净年化</th><th>波动</th><th>夏普</th><th>非扣费</th><th>日换手</th>"

    H.append("<h2>2.2 主格 top5 × h5 分风格</h2>")
    span = "".join(f"<th colspan='5'>{r} 态</th>" for r in REGS)
    H.append(f"<table><thead>{slice_head.format(span=span, sub=sub * len(REGS))}</thead><tbody>")
    for key, name, _ in ARMS:
        sm = seed_mean(data.get(key))
        if sm is None:
            continue
        tds = [f"<td class='l'>{name}</td>"]
        for r in REGS:
            tds.extend(f"<td>{v}</td>" for v in metric_cells(sm, PRIMARY_K, PRIMARY_H, regime=r))
        H.append("<tr>" + "".join(tds) + "</tr>")
    H.append("</tbody></table>")

    H.append("<h2>2.3 主格 top5 × h5 分年</h2>")
    missing_years = []
    for key, name, _ in ARMS:
        sm = seed_mean(data.get(key))
        if sm is None:
            continue
        if not (sm.get("head_years") or {}):
            missing_years.append(name)
    if years:
        H.append(year_table("扣费净年化", data, years, "net_ann_excess", "ann"))
        H.append(year_table("扣费波动", data, years, "net_ann_vol", "vol"))
        H.append(year_table("扣费夏普", data, years, "net_sharpe", "sharpe"))
        H.append(year_table("非扣费年化", data, years, "ann_excess", "ann"))
        H.append(year_table("日换手", data, years, "turnover", "vol"))
        H.append("<p class='note'>每张表列为年份，行是训练标签臂；格子都是主格 top5×h5。</p>")
        if missing_years:
            H.append(
                "<p class='note'>尚无分年切片："
                + "、".join(missing_years)
                + "。</p>"
            )
    else:
        H.append("<p class='note'>尚无分年切片。</p>")

    H.append("<div class='card note'><ul>")
    H.append("<li>这是 Phase M v1 单实验详细报告；总报告只放主指标，见 <code>phase_m_v1_report.html</code>。</li>")
    H.append("<li>只含 M0（全A 长窗 / 单 LGBM / CSRankNorm），训练标签不同。当前 baseline = M0 H20。</li>")
    H.append("<li>本口径仍不是完整 TopkDropout 回测：无 n_drop、无冲击、卖出侧跌停未剔除。</li>")
    H.append(f"<li>JSON：<code>{EVAL_DIR.relative_to(EXP_ROOT)}/eval_*.json</code></li>")
    H.append("</ul></div></div></body></html>")
    OUT.write_text("\n".join(H), encoding="utf-8")
    print("written:", OUT)


if __name__ == "__main__":
    main()
