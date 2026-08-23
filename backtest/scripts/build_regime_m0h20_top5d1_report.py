"""M0 H20 × top5d1 对照回测报告（Phase M v1 口径，不是 CSI1000 B4-S 晋升）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Optional

EXP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

RES_JSON = EXP_ROOT / "backtest" / "result" / "phase_s_regime" / "all_top5d1" / "m0h20.json"
BEFORE_FIX_JSON = (
    EXP_ROOT / "backtest" / "result" / "phase_s_regime" / "all_top5d1" / "m0h20_before_fix.json"
)
EXEC_FIX_JSON = (
    EXP_ROOT
    / "backtest"
    / "result"
    / "phase_s_regime"
    / "all_top5d1"
    / "m0h20_exec_fix_old_st.json"
)
OUT = EXP_ROOT / "backtest" / "experiments" / "regime_adapt_m0h20_top5d1_bt_report.html"
EXP_DIR = EXP_ROOT / "backtest" / "experiments"

FULL_COLS = [
    ("annualized_return", "累乘年化", "pct"),
    ("annualized_return_arith", "算术年化", "pct"),
    ("sharpe_ratio", "夏普", "num"),
    ("alpha", "Alpha", "pct"),
    ("beta", "Beta", "num"),
    ("max_drawdown", "最大回撤", "pct"),
    ("calmar_ratio", "Calmar", "num"),
    ("annualized_volatility", "年化波动", "pct"),
    ("annualized_one_way_turnover", "年化单边换手", "turn"),
    ("cumulative_return", "累计收益", "pct"),
    ("benchmark_annualized_return", "基准年化", "pct"),
]
YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]


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


def rel_from_exp(path: Path) -> str:
    return Path(os_relpath(path, EXP_DIR)).as_posix()


def os_relpath(path: Path, start: Path) -> str:
    import os

    return os.path.relpath(path, start)


def figure_iframe(session_dir: Path, name: str) -> str:
    href = rel_from_exp(session_dir / "run_01" / "figures" / name)
    return (
        f'<iframe src="{href}" title="{name}" loading="lazy" '
        'style="width:100%;height:420px;border:1px solid #e2e8f0;border-radius:6px;"></iframe>'
    )


def _metric_cells(block: dict, cols: list[tuple[str, str, str]]) -> list[str]:
    return [f"<td>{fmt(block.get(key), kind)}</td>" for key, _, kind in cols]


def _seed_recs(doc: dict[str, Any]) -> list[dict[str, Any]]:
    seeds = doc.get("seeds") or {}
    return [seeds[k] for k in sorted(seeds, key=lambda x: int(x))]


def _mean_block(recs: list[dict], block: str, key: str) -> Optional[float]:
    m, _ = agg(recs, block, key)
    return m


def _mean_year(recs: list[dict], year: str, key: str) -> Optional[float]:
    vals = []
    for rec in recs:
        y = (rec.get("years") or {}).get(year) or {}
        if y.get(key) is not None:
            vals.append(float(y[key]))
    if not vals:
        return None
    return mean(vals)


def _mean_excess(recs: list[dict], year: Optional[str] = None) -> Optional[float]:
    vals = []
    for rec in recs:
        block = rec.get("full_period") if year is None else (rec.get("years") or {}).get(year)
        if not block:
            continue
        ann = block.get("annualized_return")
        bench = block.get("benchmark_annualized_return")
        if ann is None or bench is None:
            continue
        vals.append(float(ann) - float(bench))
    if not vals:
        return None
    return mean(vals)


def _filter_bullets(uf: dict[str, Any], filter_stats: dict[str, Any]) -> list[str]:
    st_daily = uf.get("st_daily") or filter_stats.get("st_daily")
    st_names = uf.get("st_names")
    st_path = st_daily or st_names or "st_names.csv"
    recent = int(uf.get("min_recent_trading_days") or filter_stats.get("min_recent_trading_days") or 0)
    lines = [
        "<li><b>过滤</b>（选股决策时置 NaN，与 Phase M v1 / "
        f"<code>eval_ic_multi_pool.py</code> 同口径）：ST "
        f"<code>{st_path}</code>"
        + ("（日频，含退市整理期）" if st_daily else "")
        + f"；成交额 ≥ {float(uf.get('min_amount') or 10_000_000):,.0f}；"
        f"上市 ≥ {int(uf.get('min_listing_days') or 60)} 交易日"
        + (f"；近{recent}交易日连续有成交" if recent else "")
        + "。旧持仓当天不满足过滤时按 n_drop 换出，不会绕过过滤继续买。</li>"
    ]
    return lines


def _compare_section(current: dict[str, Any], compare_runs: list[tuple[str, dict]]) -> list[str]:
    rows = list(compare_runs) + [("日频 ST（当前）", current)]
    H = ["<h2>0. 与修复前对照</h2>"]
    H.append(
        "<p class='note'>同一套 M0 H20 × top5d1 / 账户 100 万 / 等权全A。"
        "修复前：持仓停牌股占住 n_drop，组合冻结；ST 用静态快照，退市股不在名单里。"
        "中间档：只修执行层，仍用静态 ST。当前：日频 ST + 近60日成交过滤。"
        "数字从对照 JSON 现算，不是手写。</p>"
    )
    H.append(
        "<table><thead><tr><th class='l'>版本</th><th>全期年化</th><th>全期夏普</th>"
        "<th>全期Alpha</th><th>全期回撤</th><th>全期换手</th>"
        "<th>2026年化</th><th>2026超额</th><th>2026换手</th></tr></thead><tbody>"
    )
    for i, (title, doc) in enumerate(rows):
        recs = _seed_recs(doc)
        cls = " class='primary'" if i == len(rows) - 1 else ""
        H.append(
            f"<tr{cls}><td class='l'>{title}</td>"
            f"<td>{fmt(_mean_block(recs, 'full_period', 'annualized_return'), 'pct')}</td>"
            f"<td>{fmt(_mean_block(recs, 'full_period', 'sharpe_ratio'), 'num')}</td>"
            f"<td>{fmt(_mean_block(recs, 'full_period', 'alpha'), 'pct')}</td>"
            f"<td>{fmt(_mean_block(recs, 'full_period', 'max_drawdown'), 'pct')}</td>"
            f"<td>{fmt(_mean_block(recs, 'full_period', 'annualized_one_way_turnover'), 'turn')}</td>"
            f"<td>{fmt(_mean_year(recs, '2026', 'annualized_return'), 'pct')}</td>"
            f"<td>{fmt(_mean_excess(recs, '2026'), 'pct')}</td>"
            f"<td>{fmt(_mean_year(recs, '2026', 'annualized_one_way_turnover'), 'turn')}</td></tr>"
        )
    H.append("</tbody></table>")

    H.append("<h3>2026 逐种子年化</h3>")
    labels = [title for title, _ in rows]
    H.append(
        "<table><thead><tr><th>种子</th>"
        + "".join(f"<th>{lab}</th>" for lab in labels)
        + "</tr></thead><tbody>"
    )
    seed_ids = sorted({k for _, doc in rows for k in (doc.get("seeds") or {})}, key=lambda x: int(x))
    for seed in seed_ids:
        cells = [f"<td>{seed}</td>"]
        for _, doc in rows:
            y = ((doc.get("seeds") or {}).get(seed) or {}).get("years") or {}
            cells.append(f"<td>{fmt((y.get('2026') or {}).get('annualized_return'), 'pct')}</td>")
        H.append("<tr>" + "".join(cells) + "</tr>")
    H.append("</tbody></table>")
    return H


def _current_ensemble(doc: dict[str, Any]) -> dict[str, Any]:
    """旧快照 ST 的 ensemble 不得和日频 ST 五种子混排。"""
    ens = doc.get("ensemble") or {}
    uf = ens.get("universe_filter") or {}
    if uf.get("st_filter") in {"enabled", "names"}:
        return {}
    return ens


def render_html(
    doc: dict[str, Any],
    compare_runs: Optional[list[tuple[str, dict]]] = None,
) -> str:
    seeds: dict[str, dict[str, Any]] = doc.get("seeds") or {}
    if not seeds:
        raise ValueError("no seeds in report json")
    recs = [seeds[k] for k in sorted(seeds, key=lambda x: int(x))]
    window = doc.get("backtest_window") or ["2020-08-03", "2026-07-31"]
    uf = doc.get("universe_filter") or {}
    account = doc.get("account") or 1_000_000
    seed42 = seeds.get("42") or {}
    session42 = None
    if seed42.get("session_dir"):
        session42 = EXP_ROOT / seed42["session_dir"]
    figs = (seed42.get("figures") or {}) if seed42 else {}
    filter_stats = seed42.get("universe_filter") or {}
    ensemble = _current_ensemble(doc)
    ens_session = None
    if ensemble.get("session_dir"):
        ens_session = EXP_ROOT / ensemble["session_dir"]
    ens_figs = ensemble.get("figures") or {}

    css = """
    body{font-family:-apple-system,'PingFang SC',sans-serif;margin:0;background:#f6f7f9;color:#1c2733;}
    .wrap{max-width:1180px;margin:0 auto;padding:28px 20px 64px;}
    h1{font-size:24px;margin:0 0 6px;} h2{font-size:18px;margin:28px 0 8px;border-left:4px solid #2563eb;padding-left:10px;}
    h3{font-size:14px;margin:16px 0 6px;color:#334155;}
    .meta{color:#64748b;font-size:13px;margin-bottom:16px;}
    table{border-collapse:collapse;width:100%;background:#fff;font-size:12px;margin:6px 0 14px;}
    th,td{border:1px solid #e2e8f0;padding:5px 7px;text-align:center;}
    th{background:#f1f5f9;} td.l,th.l{text-align:left;}
    .primary{background:#eff6ff;font-weight:600;}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;margin:8px 0;}
    .note{font-size:12px;color:#64748b;}
    ul{margin:6px 0;padding-left:20px;}
    .sd{color:#94a3b8;font-size:10px;}
    """
    H = [
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
        "<title>M0 H20 × top5d1 对照回测</title>",
        f"<style>{css}</style></head><body><div class='wrap'>",
        "<h1>M0 H20 × top5d1 对照回测</h1>",
        f"<p class='meta'>Phase M v1 对照（真实 TopkDropout，不是 CSI1000 B4-S 晋升）· "
        f"窗 <b>{window[0]} ~ {window[1]}</b> · 账户 <b>{account:,.0f}</b> · "
        "策略 topk=5 / n_drop=1 / hold_thresh=1 / risk_degree=0.90 · "
        "基准 等权全A · 种子 "
        f"{', '.join(sorted(seeds, key=lambda x: int(x)))}</p>",
    ]
    H.append("<div class='card'><ul>")
    H.append(
        "<li><b>模型</b>：已有 session <code>regimeadaptfast_m0h20_s{42,1000,2000,3000,4000}</code>，"
        "不新训、不跑 DoubleEnsemble。</li>"
    )
    H.append(
        "<li><b>池</b>：全A NameDFilter <code>^(SH60|SH68|SZ00|SZ30)</code>；"
        "基准 <code>backtest/configs/regime-adapt/bench_ew_all.csv</code>。</li>"
    )
    H.extend(_filter_bullets(uf, filter_stats))
    H.append(
        "<li><b>费率</b>：open 0.00021 / close 0.00071 / min_cost 5 / trade_unit 100 / "
        "deal_price close / limit_threshold market_cn（主板 9.5%、创业板/科创板 19.5%）。</li>"
    )
    H.append(
        "<li><b>五种子均值信号</b>：先对各种子 pred 做<b>日截面 z-score</b>，再等权平均，"
        "用同一套 top5d1 / 三过滤 / 费率跑一次回测。"
        "表中「均值」是五次回测指标的算术平均；「五种子均值信号」是合成信号的单次回测，不是同一件事。</li>"
    )
    if filter_stats:
        H.append(
            f"<li><b>过滤生效</b>：保留 {filter_stats.get('n_keep')} / {filter_stats.get('n_raw')} "
            f"({float(filter_stats.get('keep_rate') or 0):.1%})；日可选 "
            f"min/med/max = {filter_stats.get('eligible_min')} / "
            f"{filter_stats.get('eligible_median')} / {filter_stats.get('eligible_max')}；"
            f"样本日 {filter_stats.get('sample_day')} raw={filter_stats.get('sample_day_raw')} "
            f"keep={filter_stats.get('sample_day_eligible')}；"
            f"ST 日频命中 {filter_stats.get('n_st_symbols')} 条。</li>"
        )
    H.append("</ul></div>")

    if compare_runs:
        H.extend(_compare_section(doc, compare_runs))

    H.append("<h2>1. 全周期（逐种子 + 均值）</h2>")
    H.append(
        "<table><thead><tr><th class='l'>种子</th>"
        + "".join(f"<th>{lab}</th>" for _, lab, _ in FULL_COLS)
        + "</tr></thead><tbody>"
    )
    for seed in sorted(seeds, key=lambda x: int(x)):
        fp = seeds[seed]["full_period"]
        cells = [f"<td class='l'>{seed}</td>"]
        for key, _, kind in FULL_COLS:
            cells.append(f"<td>{fmt(fp.get(key), kind)}</td>")
        H.append("<tr>" + "".join(cells) + "</tr>")
    if len(recs) > 1:
        cells = ["<td class='l primary'>均值</td>"]
        for key, _, kind in FULL_COLS:
            m, s = agg(recs, "full_period", key)
            sd = "" if not s else f"<div class='sd'>±{fmt(s, 'num' if kind != 'pct' else 'pct').lstrip('+')}</div>"
            cells.append(f"<td class='primary'>{fmt(m, kind)}{sd}</td>")
        H.append("<tr>" + "".join(cells) + "</tr>")
    if ensemble.get("full_period"):
        cells = ["<td class='l primary'>五种子均值信号</td>"]
        cells.extend(_metric_cells(ensemble["full_period"], FULL_COLS))
        H.append("<tr>" + "".join(cells) + "</tr>")
    H.append("</tbody></table>")

    H.append("<h2>2. 分年（均值）</h2>")
    H.append(
        "<table><thead><tr><th>年</th><th>年化</th><th>夏普</th><th>Alpha</th><th>Beta</th>"
        "<th>回撤</th><th>换手</th></tr></thead><tbody>"
    )
    for year in YEARS:
        vals = {
            key: [
                float((r.get("years") or {}).get(year, {}).get(key))
                for r in recs
                if (r.get("years") or {}).get(year, {}).get(key) is not None
            ]
            for key in (
                "annualized_return",
                "sharpe_ratio",
                "alpha",
                "beta",
                "max_drawdown",
                "annualized_one_way_turnover",
            )
        }
        if not any(vals.values()):
            continue
        def _m(key, kind):
            xs = vals[key]
            return fmt(mean(xs) if xs else None, kind)
        H.append(
            f"<tr><td>{year}</td><td>{_m('annualized_return','pct')}</td>"
            f"<td>{_m('sharpe_ratio','num')}</td><td>{_m('alpha','pct')}</td>"
            f"<td>{_m('beta','num')}</td><td>{_m('max_drawdown','pct')}</td>"
            f"<td>{_m('annualized_one_way_turnover','turn')}</td></tr>"
        )
    H.append("</tbody></table>")

    if len(recs) > 1:
        H.append("<h3>分年逐种子</h3>")
        H.append(
            "<table><thead><tr><th>种子</th><th>年</th><th>年化</th><th>夏普</th>"
            "<th>Alpha</th><th>Beta</th><th>回撤</th></tr></thead><tbody>"
        )
        for seed in sorted(seeds, key=lambda x: int(x)):
            years = seeds[seed].get("years") or {}
            for year in YEARS:
                y = years.get(year)
                if not y:
                    continue
                H.append(
                    f"<tr><td>{seed}</td><td>{year}</td>"
                    f"<td>{fmt(y.get('annualized_return'),'pct')}</td>"
                    f"<td>{fmt(y.get('sharpe_ratio'),'num')}</td>"
                    f"<td>{fmt(y.get('alpha'),'pct')}</td>"
                    f"<td>{fmt(y.get('beta'),'num')}</td>"
                    f"<td>{fmt(y.get('max_drawdown'),'pct')}</td></tr>"
                )
        H.append("</tbody></table>")

    if ensemble.get("years"):
        H.append("<h3>分年 · 五种子均值信号</h3>")
        H.append(
            "<table><thead><tr><th>年</th><th>年化</th><th>夏普</th>"
            "<th>Alpha</th><th>Beta</th><th>回撤</th><th>换手</th></tr></thead><tbody>"
        )
        years = ensemble["years"]
        for year in YEARS:
            y = years.get(year)
            if not y:
                continue
            H.append(
                f"<tr><td>{year}</td>"
                f"<td>{fmt(y.get('annualized_return'),'pct')}</td>"
                f"<td>{fmt(y.get('sharpe_ratio'),'num')}</td>"
                f"<td>{fmt(y.get('alpha'),'pct')}</td>"
                f"<td>{fmt(y.get('beta'),'num')}</td>"
                f"<td>{fmt(y.get('max_drawdown'),'pct')}</td>"
                f"<td>{fmt(y.get('annualized_one_way_turnover'),'turn')}</td></tr>"
            )
        H.append("</tbody></table>")

    H.append("<h2>3. Seed 42 图</h2>")
    if session42 and figs:
        titles = {
            "report_graph": "净值 / 回撤 / 换手",
            "risk_analysis": "风险分解",
            "score_ic": "Score IC",
            "model_performance": "模型表现",
        }
        for cat, title in titles.items():
            files = figs.get(cat) or []
            H.append(f"<h3>{title}</h3>")
            if not files:
                H.append("<p class='note'>未生成</p>")
                continue
            for name in files:
                H.append(figure_iframe(session42, name))
                href = rel_from_exp(session42 / "run_01" / "figures" / name)
                H.append(f"<p class='note'><code>{href}</code></p>")
    else:
        H.append("<p class='note'>seed 42 尚无 figures_manifest。</p>")

    H.append("<h2>4. 五种子均值信号图</h2>")
    if ens_session and ens_figs:
        titles = {
            "report_graph": "净值 / 回撤 / 换手",
            "risk_analysis": "风险分解",
            "score_ic": "Score IC",
            "model_performance": "模型表现",
        }
        for cat, title in titles.items():
            files = ens_figs.get(cat) or []
            H.append(f"<h3>{title}</h3>")
            if not files:
                H.append("<p class='note'>未生成</p>")
                continue
            for name in files:
                H.append(figure_iframe(ens_session, name))
                href = rel_from_exp(ens_session / "run_01" / "figures" / name)
                H.append(f"<p class='note'><code>{href}</code></p>")
    else:
        raw_ens = doc.get("ensemble") or {}
        if (raw_ens.get("universe_filter") or {}).get("st_filter") in {"enabled", "names"}:
            H.append(
                "<p class='note'>五种子均值信号尚未用日频 ST 重跑，本表不展示旧快照结果。</p>"
            )
        else:
            H.append("<p class='note'>ensemble 尚无 figures_manifest。</p>")

    H.append("</div></body></html>")
    return "".join(H)


def _load_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def default_compare_runs() -> list[tuple[str, dict]]:
    pairs = [
        ("修复前（组合冻结 + 静态 ST）", BEFORE_FIX_JSON),
        ("执行层修复（仍静态 ST）", EXEC_FIX_JSON),
    ]
    out = []
    for title, path in pairs:
        doc = _load_json(path)
        if doc:
            out.append((title, doc))
    return out


def main() -> None:
    if not RES_JSON.is_file():
        raise SystemExit(f"missing {RES_JSON}")
    doc = json.loads(RES_JSON.read_text(encoding="utf-8"))
    html = render_html(doc, compare_runs=default_compare_runs())
    OUT.write_text(html, encoding="utf-8")
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
