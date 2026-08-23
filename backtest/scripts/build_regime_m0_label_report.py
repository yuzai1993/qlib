"""M0 改标签实验的 Phase M v1 详细报告。

主指标、分年、分风格都只读 top3×h5。
全期 k×h 网格仅作稳健性，见 phase_m_v1_report.html。
四臂都是 M0 + top3×h5 早停，只改训练标签期限。
"""
from __future__ import annotations

import json
from pathlib import Path

EXP_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = EXP_ROOT / "backtest" / "result" / "eval_regime_m0_t3h5es"
OUT = EXP_ROOT / "backtest" / "experiments" / "regime_adapt_m0_label_report.html"

ARMS = [
    ("m0h1", "M0 H1 t3h5es", 1),
    ("m0h5", "M0 H5 t3h5es", 5),
    ("m0h10", "M0 H10 t3h5es", 10),
    ("m0h20", "M0 H20 t3h5es", 20),
]
KS = ["1", "2", "3", "4", "5"]
HS = ["2", "3", "4", "5"]
REGS = ["D", "F", "T"]
PRIMARY_K, PRIMARY_H = "3", "5"

EVAL_FILES = {key: f"eval_{key}.json" for key, _, _ in ARMS}


def load_arm(key: str):
    path = EVAL_DIR / EVAL_FILES.get(key, f"eval_{key}.json")
    return json.loads(path.read_text()) if path.exists() else None


def seed_mean(rec):
    if rec is None:
        return None
    pool = rec.get("pools", {}).get("all", {}) or {}
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
            tds.append(
                f"<td>{fmt(cell(sm, PRIMARY_K, PRIMARY_H, metric, year=y), kind)}</td>"
            )
        body += "<tr>" + "".join(tds) + "</tr>"
    return f"<h3>{title}</h3><table><thead>{header}</thead><tbody>{body}</tbody></table>"


def _n_days(data) -> object:
    for key, _, _ in ARMS:
        sm = seed_mean(data.get(key))
        n = cell(sm, "1", "5", "n_days")
        if n is not None:
            return n
    return None


def render(data: dict) -> str:
    any_rec = next((v for v in data.values() if v is not None), None)
    filters = (any_rec or {}).get("filters") or {}
    n_days = _n_days(data)
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
        "当前轨道 baseline 见总报告当前行；本页是 H1/H5/H10/H20 × top3×h5 早停对照 · "
        f"全A · 2020-08-03~2026-07-31 · h=5 约 {n_days or '—'} 个交易日 "
        "（更短 h 末端截断更少） · "
        f"过滤：上市≥{filters.get('min_listing_days', 60)} 日、"
        f"ST={filters.get('st_filter', '—')}、"
        f"成交额≥{filters.get('min_amount', 0):.0f} 元、剔 t+1 涨停/零量 · "
        "本页网格 <b>top∈{1,2,3,4,5} × h∈{2,3,4,5}</b>；"
        "官方主格 <b>top3 × h5</b>（蓝底）。"
        "日换手 = 相隔 h 日的单边换手 / h（h=5 全换仓 → 日换手 20%）；"
        "年化成本 = 238 × 日换手 × 0.092%；"
        "扣费净年化 = 非扣费年化 − 年化成本；"
        "扣费波动 = 绝对收益序列 HAC 年化标准差；扣费夏普 = 净年化 / 波动。</p>",
    ]

    H.append("<h2>1. 网格 top∈{1,2,3,4,5} × h∈{2,3,4,5}</h2>")
    H.append(grid_table("扣费净年化", data, "net_ann", "ann"))
    H.append(grid_table("扣费波动", data, "net_ann_vol", "vol"))
    H.append(grid_table("扣费夏普", data, "net_sharpe", "sharpe"))
    H.append(grid_table("非扣费年化", data, "ann", "ann"))
    H.append(grid_table("日换手", data, "turnover", "vol"))
    H.append(
        "<p class='note'>黄底 = 该臂训练标签期限等于该列 h（例如 H5 在 h=5 列）。</p>"
    )

    sub = "<th>净年化</th><th>波动</th><th>夏普</th><th>非扣费</th><th>日换手</th>"

    H.append("<h2>2. 主格 top3 × h5 分风格</h2>")
    span = "".join(f"<th colspan='5'>{r} 态</th>" for r in REGS)
    H.append(
        f"<table><thead><tr><th class='l'>臂</th>{span}</tr>"
        f"<tr><th></th>{sub * len(REGS)}</tr></thead><tbody>"
    )
    for key, name, _ in ARMS:
        sm = seed_mean(data.get(key))
        if sm is None:
            continue
        tds = [f"<td class='l'>{name}</td>"]
        for r in REGS:
            tds.extend(
                f"<td>{v}</td>"
                for v in metric_cells(sm, PRIMARY_K, PRIMARY_H, regime=r)
            )
        H.append("<tr>" + "".join(tds) + "</tr>")
    H.append("</tbody></table>")

    H.append("<h2>3. 主格 top3 × h5 分年</h2>")
    if years:
        H.append(year_table("扣费净年化", data, years, "net_ann", "ann"))
        H.append(year_table("扣费波动", data, years, "net_ann_vol", "vol"))
        H.append(year_table("扣费夏普", data, years, "net_sharpe", "sharpe"))
        H.append(year_table("非扣费年化", data, years, "ann", "ann"))
        H.append(year_table("日换手", data, years, "turnover", "vol"))
        H.append("<p class='note'>分年、分风格只读主格 top3×h5，不再叉乘 k×h。</p>")
    else:
        H.append("<p class='note'>尚无分年切片。</p>")

    H.append("<div class='card note'><ul>")
    H.append(
        "<li>这是 Phase M v1 单实验详细报告；总报告官方主格是 top3×h5，见 "
        "<code>phase_m_v1_report.html</code>。</li>"
    )
    H.append(
        "<li>四臂都是 M0 + top3×h5 早停，只改训练标签期限 H1/H5/H10/H20。</li>"
    )
    H.append("<li>本口径仍不是完整 TopkDropout 回测：无 n_drop、无冲击、卖出侧跌停未剔除。</li>")
    H.append(
        f"<li>本轮 JSON：<code>{EVAL_DIR.relative_to(EXP_ROOT)}/eval_m0h*.json</code></li>"
    )
    H.append("</ul></div></div></body></html>")
    return "\n".join(H)


def main() -> None:
    data = {k: load_arm(k) for k, _, _ in ARMS}
    if not any(v is not None for v in data.values()):
        raise SystemExit(f"no eval json in {EVAL_DIR}")
    OUT.write_text(render(data), encoding="utf-8")
    print("written:", OUT)


if __name__ == "__main__":
    main()
