"""Phase M v1 执行层回测总报告：只认五种子均值信号的单次回测。"""

from __future__ import annotations

import json
import os
from html import escape
from pathlib import Path

from typing import Any, Optional

if __package__:
    from .research_layout import resolve_research_path as _resolve_research_path
    from .research_layout import report_href as _layout_href
    from .research_labels import strategy_display_version, strategy_reference_text
else:
    from research_layout import resolve_research_path as _resolve_research_path
    from research_layout import report_href as _layout_href
    from research_labels import strategy_display_version, strategy_reference_text

_RESEARCH_ROOT = Path(__file__).resolve().parents[2]

if __package__:
    from .experiment_registry import (
        load_registry as _load_registry_rows,
    )
else:
    from experiment_registry import (
        load_registry as _load_registry_rows,
    )

EXP_ROOT = Path(__file__).resolve().parents[2]
EXP_DIR = EXP_ROOT / "backtest" / "experiments"
REGISTRY = EXP_DIR / "registry.jsonl"
HUB_OUT = EXP_DIR / "reports" / "phase_m_v1_bt_report.html"

YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]
REGIMES = ["D", "F", "T"]
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
]
YEAR_METRICS = [
    ("annualized_return", "累乘年化", "pct"),
    ("annualized_return_arith", "算术年化", "pct"),
    ("sharpe_ratio", "夏普", "num"),
    ("alpha", "Alpha", "pct"),
    ("beta", "Beta", "num"),
    ("max_drawdown", "最大回撤", "pct"),
    ("annualized_one_way_turnover", "年化单边换手", "turn"),
]
CSS = """
body{font-family:-apple-system,'PingFang SC',sans-serif;margin:0;background:#f6f7f9;color:#1c2733;}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 64px;}
h1{font-size:24px;margin:0 0 6px;} h2{font-size:18px;margin:28px 0 8px;border-left:4px solid #2563eb;padding-left:10px;}
h3{font-size:14px;margin:16px 0 6px;color:#334155;}
.meta,.note{color:#64748b;font-size:13px;}
table{border-collapse:collapse;width:100%;background:#fff;font-size:12px;margin:6px 0 14px;}
th,td{border:1px solid #e2e8f0;padding:5px 7px;text-align:center;}
th{background:#f1f5f9;} td.l,th.l{text-align:left;}
.primary{background:#eff6ff;font-weight:600;}
.current{background:#dbeafe;font-weight:600;}
a{color:#2563eb;}
"""


def fmt(v: Optional[float], kind: str) -> str:
    if v is None:
        return "—"
    if kind == "pct":
        return f"{v*100:+.1f}%"
    if kind == "turn":
        return f"{v:.1f}x"
    return f"{v:.2f}"


def ensemble_of(doc: dict[str, Any]) -> dict[str, Any]:
    ens = doc.get("ensemble")
    if not ens:
        raise ValueError("official Phase M v1 backtest metrics must come from ensemble")
    return ens


def rel_from_exp(path: Path, output_path: Path | None = None) -> str:
    return _layout_href(path, output_path or HUB_OUT, EXP_ROOT)


def session_report_href(session_dir: str, output_path: Path | None = None) -> str:
    return rel_from_exp(EXP_ROOT / session_dir / "run_01" / "report.html", output_path)


def report_href(ensemble: dict, output_path: Path | None = None) -> str:
    portable = ensemble.get("portable_report")
    return rel_from_exp(EXP_ROOT / portable, output_path) if portable else session_report_href(ensemble["session_dir"], output_path)


def nav_svg(curve: list[list]) -> str:
    """A self-contained chart from the persisted, after-cost daily NAV series."""
    values = [1.0] + [float(v) for row in curve for v in row[1:3]]
    low, high = min(values), max(values)
    span = high - low or 1.0
    width, height, margin = 1000, 330, 35
    def points(column):
        return " ".join(
            f"{margin + i * (width - 2 * margin) / max(len(curve) - 1, 1):.2f},"
            f"{height - margin - (float(row[column]) - low) * (height - 2 * margin) / span:.2f}"
            for i, row in enumerate(curve)
        )
    return (
        '<svg viewBox="0 0 1000 330" role="img" aria-label="扣费净值与等权全A基准" '
        'style="width:100%;background:white;border:1px solid #e2e8f0">'
        f'<text x="35" y="20" fill="#2563eb">扣费净值</text>'
        '<text x="155" y="20" fill="#94a3b8">等权全A基准</text>'
        f'<polyline points="{points(2)}" fill="none" stroke="#94a3b8" stroke-width="1.5"/>'
        f'<polyline points="{points(1)}" fill="none" stroke="#2563eb" stroke-width="1.7"/>'
        f'<text x="35" y="322">{escape(str(curve[0][0]))}</text>'
        f'<text x="865" y="322">{escape(str(curve[-1][0]))}</text></svg>'
    )


def figure_blocks(ensemble: dict, output_path: Path | None = None) -> list[str]:
    if ensemble.get("nav_curve"):
        return [nav_svg(ensemble["nav_curve"])]
    return figure_iframes(ensemble["session_dir"], ensemble.get("figures") or {}, output_path)


def coverage_note(doc: dict) -> str:
    coverage = doc.get("regime_coverage") or {}
    if coverage.get("unlabeled_days"):
        return (f"<p class='note'>规范风格标签覆盖 {coverage['labeled_days']} / {coverage['total_days']} 日；"
                f"新增 {coverage['unlabeled_days']} 日无标签，仅进入全期及分年指标，不推断其风格。</p>")
    return ""


def diagnostic_periods(doc: dict) -> str:
    blocks = []
    for period in doc.get("diagnostic_periods", []):
        metrics = period["metrics"]
        blocks.append(
            f"<h2>{escape(period['name'])}</h2>"
            f"<p class='note'>{escape(period['start'])} ～ {escape(period['end'])}，"
            f"{metrics['n_days']} 个交易日。{escape(period['note'])}</p>"
            "<table><tr><th>区间收益</th><th>基准区间收益</th><th>夏普</th><th>最大回撤</th></tr><tr>"
            + "".join(_row_metrics(metrics, [
                ("cumulative_return", "", "pct"),
                ("benchmark_cumulative_return", "", "pct"),
                ("sharpe_ratio", "", "num"),
                ("max_drawdown", "", "pct"),
            ]))
            + "</tr></table>"
        )
        if period.get("nav_curve"):
            blocks.append(nav_svg(period["nav_curve"]))
    return "".join(blocks)


def figure_iframes(session_dir: str, figures: dict[str, Any], output_path: Path | None = None) -> list[str]:
    out = []
    files = figures.get("report_graph") or []
    if not files:
        return ["<p class='note'>无净值图</p>"]
    root = EXP_ROOT / session_dir / "run_01" / "figures"
    for name in files:
        href = rel_from_exp(root / name, output_path)
        out.append(
            f'<iframe src="{href}" title="{name}" loading="lazy" '
            'style="width:100%;height:420px;border:1px solid #e2e8f0;border-radius:6px;"></iframe>'
        )
    return out


def _row_metrics(block: dict, cols: list[tuple[str, str, str]]) -> list[str]:
    return [f"<td>{fmt(block.get(key), kind)}</td>" for key, _, kind in cols]


def _matrix_table(
    title: str,
    rows: list[tuple[str, dict]],
    columns: list[str],
    key: str,
    kind: str,
    bucket: str,
) -> str:
    parts = [
        f"<h3>{escape(title)}</h3>",
        "<table><thead><tr><th class='l'>版本</th>"
        + "".join(f"<th>{escape(c)}</th>" for c in columns)
        + "</tr></thead><tbody>",
    ]
    for name, ens in rows:
        cells = [f"<td class='l'>{escape(name)}</td>"]
        src = ens.get(bucket) or {}
        for col in columns:
            cells.append(f"<td>{fmt((src.get(col) or {}).get(key), kind)}</td>")
        parts.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _strategy_version(item: dict) -> str:
    value = item.get("display_version") or item.get("bt_version") or item.get("version") or ""
    return strategy_display_version(value, kind=item.get("kind", "strategy"), track=item.get("track", "all_a"))


def _model_label(item: dict) -> str:
    summary = item.get("model_summary") or {}
    return " · ".join(str(summary[key]) for key in ("version", "name") if summary.get(key))


def _strategy_label(item: dict) -> str:
    value = (item.get("strategy_summary") or {}).get("name") or item.get("display_name", "")
    return strategy_reference_text(value, kind=item.get("kind", "strategy"), track=item.get("track", "all_a"))


def _benchmark_label(item: dict) -> str:
    return " · ".join(part for part in (_strategy_version(item), _model_label(item), _strategy_label(item)) if part)


def enrich_strategy_labels(baselines: list[dict], experiments: list[dict], snapshot: dict) -> tuple[list[dict], list[dict]]:
    """Attach catalogue display labels without changing registry-owned versions."""
    sources = {record["id"]: record for record in snapshot.get("records", [])}

    def enrich(items):
        return [{**item, **{key: sources[item["exp_id"]][key]
                            for key in ("display_version", "model_summary", "strategy_summary", "kind", "track")
                            if key in sources.get(item["exp_id"], {})}} for item in items]

    baselines, experiments = enrich(baselines), enrich(experiments)
    names = {item["exp_id"]: _benchmark_label(item) for item in baselines}
    experiments = [{**item, "baseline_name": names.get(item.get("baseline_ref"), item.get("baseline_name", ""))}
                   for item in experiments]
    return baselines, experiments


def render_hub(baselines: list[dict], experiments: list[dict], *, output_path: Path | None = None) -> str:
    output_path = output_path or HUB_OUT
    # 锚点与策略参数一律由 current baseline 派生，避免晋升后页头留着旧版本
    cur = next((b for b in baselines if b.get("current")), None)
    anchor = (
        _benchmark_label(cur) if cur else "未设定（无 current baseline）"
    )
    strategy = (cur or {}).get("strategy") or ""
    H = [
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
        "<title>Phase M v1 执行层回测</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        "<h1>Phase M v1 执行层回测</h1>",
        "<p class='meta'>官方指标只认<b>五种子均值信号</b>："
        "先对五种子 pred 做日截面 z-score，再等权平均，回测一次。"
        f"不是五次回测指标的算术平均。当前对照锚点是 <b>{escape(anchor)}</b>。"
        + (f"策略 {escape(strategy)}。" if strategy else "")
        + "日频 ST / 等权全A / 窗 2020-08-03~2026-07-31。"
        "主年化是扣费净值累乘后再折年（CAGR）；算术年化（日均×250）只作审计。"
        "夏普分子仍是算术年化。</p>",
    ]
    if cur and cur["doc"].get("selection_limitation"):
        H.append(f"<p class='note'>{escape(strategy_reference_text(cur['doc']['selection_limitation']))}</p>")

    H.append("<h2>1. 历史 baseline</h2>")
    H.append(
        "<table><thead><tr><th class='l'>策略版本</th><th class='l'>模型</th><th class='l'>策略</th>"
        + "".join(f"<th>{lab}</th>" for _, lab, _ in FULL_COLS)
        + "<th>带图报告</th></tr></thead><tbody>"
    )
    for item in baselines:
        ens = ensemble_of(item["doc"])
        href = report_href(ens, output_path)
        cls = "current" if item.get("current") else "primary"
        tag = " · 当前" if item.get("current") else ""
        H.append(
            f"<tr class='{cls}'><td class='l'>"
            f"{escape(_strategy_version(item))}{tag}</td>"
            f"<td class='l'>{escape(_model_label(item) or '未登记')}</td>"
            f"<td class='l'>{escape(_strategy_label(item))}</td>"
            + "".join(_row_metrics(ens["full_period"], FULL_COLS))
            + f"<td><a href='{escape(href, quote=True)}'>report.html</a></td></tr>"
        )
    H.append("</tbody></table>")

    named = [
        (_benchmark_label(item), ensemble_of(item["doc"]))
        for item in baselines
    ]
    H.append("<h2>2. 历史 baseline 分年</h2>")
    for key, lab, kind in YEAR_METRICS:
        H.append(_matrix_table(f"分年 · {lab}", named, YEARS, key, kind, "years"))

    H.append("<h2>3. 历史 baseline 分风格</h2>")
    for key, lab, kind in YEAR_METRICS:
        H.append(_matrix_table(f"分风格 · {lab}", named, REGIMES, key, kind, "regimes"))

    H.append("<h2>4. 实验</h2>")
    H.append(
        "<table><thead><tr><th class='l'>实验</th><th class='l'>对照 baseline</th>"
        "<th>累乘年化</th><th>夏普</th><th>Alpha</th><th>回撤</th><th>详细报告</th></tr></thead><tbody>"
    )
    for exp in experiments:
        detail = exp.get("detail_report") or ""
        link = _layout_href(detail, output_path, EXP_ROOT) if detail else ""
        if exp.get("group_summary"):
            count = exp["group_summary"]["variant_count"]
            H.append(
                f"<tr><td class='l'>{escape(strategy_reference_text(exp['display_name']))}</td>"
                f"<td class='l'>{escape(strategy_reference_text(exp.get('baseline_name', '')))}</td>"
                f"<td colspan='4'>{count}个变体，逐项结果见组报告</td>"
                f"<td><a href='{escape(link, quote=True)}'>summary.html</a></td></tr>"
            )
            continue
        ens = ensemble_of(exp["doc"])
        fp = ens["full_period"]
        detail_link = (f"<a href='{escape(link, quote=True)}'>{escape(Path(detail).name)}</a>"
                       if detail else "—")
        H.append(
            f"<tr><td class='l'>{escape(strategy_reference_text(exp['display_name']))}</td>"
            f"<td class='l'>{escape(strategy_reference_text(exp.get('baseline_name', '')))}</td>"
            f"<td>{fmt(fp.get('annualized_return'), 'pct')}</td>"
            f"<td>{fmt(fp.get('sharpe_ratio'), 'num')}</td>"
            f"<td>{fmt(fp.get('alpha'), 'pct')}</td>"
            f"<td>{fmt(fp.get('max_drawdown'), 'pct')}</td>"
            f"<td>{detail_link}</td></tr>"
        )
    if not experiments:
        H.append("<tr><td class='l' colspan='7'>暂无实验</td></tr>")
    H.append("</tbody></table>")
    H.append("</div></body></html>")
    return "".join(H)


def render_experiment(experiment: dict, baseline: dict, *, output_path: Path | None = None) -> str:
    output_path = output_path or experiment.get("detail_report") or HUB_OUT
    exp_ens = ensemble_of(experiment["doc"])
    base_ens = ensemble_of(baseline["doc"])
    rows = [
        (f"实验组 {experiment['display_name']}", exp_ens),
        (f"基准组 {baseline['display_name']}", base_ens),
    ]
    H = [
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
        f"<title>{experiment['display_name']} 对照回测</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        f"<h1>{experiment['display_name']} 对照回测</h1>",
        "<p class='meta'>实验组对比基准组。指标来自五种子均值信号单次回测，"
        f"对照 <b>{baseline['display_name']}</b>（{_strategy_version(baseline)}）。"
        "主年化是扣费净值累乘后再折年（CAGR）；算术年化（日均×250）只作审计。"
        "夏普分子仍是算术年化。"
        f" <a href='{_layout_href(HUB_OUT, output_path, EXP_ROOT)}'>返回总报告</a></p>",
    ]
    if experiment["doc"].get("selection_limitation"):
        H.append(f"<p class='note'>{escape(experiment['doc']['selection_limitation'])}</p>")
    H.append(coverage_note(experiment["doc"]))
    H.append(diagnostic_periods(experiment["doc"]))
    if experiment.get("hypothesis") or experiment.get("eval_protocol") or experiment.get("conclusion"):
        notes = ["<p class='note'>"]
        if experiment.get("eval_protocol"):
            notes.append(f"<b>协议</b>：{experiment['eval_protocol']}<br>")
        if experiment.get("hypothesis"):
            notes.append(f"<b>假设</b>：{experiment['hypothesis']}<br>")
        if experiment.get("conclusion"):
            notes.append(f"<b>结论</b>：{experiment['conclusion']}")
        notes.append("</p>")
        H.extend(notes)
    H += [
        "<h2>1. 全周期</h2>",
        "<table><thead><tr><th class='l'>组别</th>"
        + "".join(f"<th>{lab}</th>" for _, lab, _ in FULL_COLS)
        + "<th>带图报告</th></tr></thead><tbody>",
    ]
    for i, (name, ens) in enumerate(rows):
        cls = " class='primary'" if i == 0 else ""
        href = report_href(ens, output_path)
        H.append(
            f"<tr{cls}><td class='l'>{name}</td>"
            + "".join(_row_metrics(ens["full_period"], FULL_COLS))
            + f"<td><a href='{href}'>report.html</a></td></tr>"
        )
    H.append("</tbody></table>")
    H.append("<h2>2. 分年</h2>")
    for key, lab, kind in YEAR_METRICS:
        H.append(_matrix_table(f"分年 · {lab}", rows, YEARS, key, kind, "years"))
    H.append("<h2>3. 分风格</h2>")
    for key, lab, kind in YEAR_METRICS:
        H.append(_matrix_table(f"分风格 · {lab}", rows, REGIMES, key, kind, "regimes"))
    H.append("<h2>4. 实验组图</h2>")
    H.extend(figure_blocks(exp_ens, output_path))
    H.append("<h2>5. 基准组图</h2>")
    H.extend(figure_blocks(base_ens, output_path))
    H.append("</div></body></html>")
    return "".join(H)


def load_registry(path: Path = REGISTRY) -> list[dict]:
    return _load_registry_rows(path) if _resolve_research_path(path, _RESEARCH_ROOT).is_file() else []


def _load_doc(rel: str) -> dict:
    path = EXP_ROOT / rel
    if not _resolve_research_path(path, _RESEARCH_ROOT).is_file():
        raise FileNotFoundError(rel)
    return json.loads(_resolve_research_path(path, _RESEARCH_ROOT).read_text(encoding="utf-8"))


def render_snapshot(item: dict, *, output_path: Path | None = None) -> str:
    """Portable historical NAV and metrics; original files remain in provenance JSON."""
    doc = item["doc"]
    ens = ensemble_of(doc)
    output_path = output_path or ens["portable_report"]
    metrics_path = item.get("portable_eval_output") or Path(ens["portable_report"]).with_suffix(".json")
    parts = ["<!DOCTYPE html><html lang='zh'><meta charset='utf-8'>",
             f"<title>{escape(item['display_name'])}</title><style>{CSS}</style><div class='wrap'>",
             f"<h1>{escape(item['display_name'])}</h1>",
             f"<p><a href='{_layout_href(HUB_OUT, output_path, EXP_ROOT)}'>返回总报告</a></p>",
             f"<p class='note'>{escape(doc.get('selection_limitation', ''))}</p>",
             "<p class='note'>扣费净值曲线来自原始日收益CSV；来源路径与SHA-256保存在同名JSON。</p>",
             f"<p><a href='{_layout_href(metrics_path, output_path, EXP_ROOT)}'>指标与来源JSON</a></p>"]
    parts.extend(figure_blocks(ens, output_path))
    parts.append(coverage_note(doc))
    parts.append(diagnostic_periods(doc))
    parts.append("<table><tr>" + "".join(f"<th>{label}</th>" for _, label, _ in FULL_COLS) + "</tr><tr>")
    parts.extend(_row_metrics(ens["full_period"], FULL_COLS))
    parts.append("</tr></table>")
    for key, label, kind in YEAR_METRICS:
        parts.append(_matrix_table(f"分年 · {label}", [(item["display_name"], ens)], YEARS, key, kind, "years"))
        parts.append(_matrix_table(f"分风格 · {label}", [(item["display_name"], ens)], REGIMES, key, kind, "regimes"))
    parts.append("</div></html>")
    return "".join(parts)


def catalog_from_registry(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    baselines, experiments = [], []
    names = {}
    for rec in rows:
        kind = rec.get("report_kind")
        if kind == "phase_m_v1_bt_baseline":
            item = {
                "exp_id": rec["exp_id"],
                "bt_version": rec["bt_version"],
                "display_name": rec.get("display_name") or rec["exp_id"],
                "eval_output": rec["eval_output"],
                "portable_eval_output": rec.get("portable_eval_output"),
                "detail_report": rec.get("detail_report"),
                "current": bool(rec.get("current_bt")),
                # eval_protocol 的第 2 段是策略参数（signal | strategy | 涨跌停 | 过滤 | …）
                "strategy": (rec.get("eval_protocol") or "").split("|")[1].strip()
                if len((rec.get("eval_protocol") or "").split("|")) > 1
                else None,
                "doc": _load_doc(rec.get("portable_eval_output") or rec["eval_output"]),
            }
            names[rec["exp_id"]] = _benchmark_label(item)
            baselines.append(item)
        elif kind == "phase_m_v1_bt":
            experiments.append(rec)
    resolved = []
    for rec in experiments:
        resolved.append(
            {
                "exp_id": rec["exp_id"],
                "display_name": rec.get("display_name") or rec["exp_id"],
                "baseline_ref": rec.get("baseline_ref"),
                "baseline_name": names.get(rec.get("baseline_ref"), rec.get("baseline_ref") or ""),
                "detail_report": rec.get("detail_report"),
                "eval_output": rec["eval_output"],
                "portable_eval_output": rec.get("portable_eval_output"),
                "hypothesis": rec.get("hypothesis"),
                "eval_protocol": rec.get("eval_protocol"),
                "conclusion": rec.get("conclusion"),
                "doc": _load_doc(rec.get("portable_eval_output") or rec["eval_output"]),
            }
        )
    return baselines, resolved


def fold_strategy_experiments(items: list[dict], snapshot: dict) -> list[dict]:
    """Keep raw history authoritative while displaying each study once."""
    if "experiment_records" not in snapshot:
        return items
    by_id = {item["exp_id"]: item for item in items}
    result = []
    for record in snapshot["experiment_records"]:
        if record.get("kind") != "strategy":
            continue
        summary = record.get("group_summary")
        if summary:
            control = next((row for row in snapshot.get("records", [])
                            if row["id"] == summary.get("control_ref")), {})
            control_version = _strategy_version(control)
            control_label = summary.get("control_label", "")
            result.append({"exp_id": record["id"], "display_name": record["name"],
                           "baseline_name": " · ".join(part for part in (control_version, control_label) if part),
                           "detail_report": f"{record['directory']}/reports/summary.html",
                           "group_summary": summary})
        elif record["id"] in by_id:
            item = dict(by_id[record["id"]])
            if item.get("bt_version"):
                # A standalone initial Benchmark is also a history row. Its
                # original report is more useful than linking back to this hub.
                item["detail_report"] = (item["doc"]["ensemble"].get("portable_report")
                                         or item.get("detail_report"))
                if record.get("comparison_baseline", {}).get("initial"):
                    item["baseline_name"] = "初始基准，无前版"
            result.append(item)
    return result


def write_all() -> list[Path]:
    baselines, experiments = catalog_from_registry(load_registry())
    if __package__:
        from .build_strategy_group_reports import load_catalog, write_all as write_groups
    else:
        from build_strategy_group_reports import load_catalog, write_all as write_groups
    group_outputs = []
    displayed = experiments
    if (EXP_ROOT / "backtest/experiments").is_dir():
        snapshot, resource_paths = load_catalog(EXP_ROOT)
        baselines, experiments = enrich_strategy_labels(baselines, experiments, snapshot)
        group_outputs = write_groups(EXP_ROOT, snapshot=snapshot, resource_paths=resource_paths)
        if "experiment_records" in snapshot:
            displayed = fold_strategy_experiments(baselines + experiments, snapshot)
    hub = _resolve_research_path(HUB_OUT, _RESEARCH_ROOT)
    written = [hub, *group_outputs]
    hub.parent.mkdir(parents=True, exist_ok=True)
    hub.write_text(render_hub(baselines, displayed, output_path=hub), encoding="utf-8")
    by_id = {f"{b['bt_version']}": b for b in baselines}
    # also index by registry exp later via baseline_ref matching display
    base_by_ref = {item["display_name"]: item for item in baselines}
    for rec in load_registry():
        if rec.get("report_kind") == "phase_m_v1_bt_baseline":
            by_id[rec["exp_id"]] = next(
                b for b in baselines if b["bt_version"] == rec["bt_version"]
            )
    for exp in experiments:
        base = by_id.get(exp.get("baseline_ref") or "")
        if base is None or not exp.get("detail_report"):
            continue
        dest = _resolve_research_path(EXP_ROOT / exp["detail_report"], EXP_ROOT)
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_experiment(exp, base, output_path=dest), encoding="utf-8")
        written.append(dest)
    for rec in load_registry():
        if rec.get("report_kind") != "phase_m_v1_bt_baseline" or not rec.get("comparison_baseline_ref"):
            continue
        item = next(b for b in baselines if b["bt_version"] == rec["bt_version"])
        base = by_id[rec["comparison_baseline_ref"]]
        dest = _resolve_research_path(EXP_ROOT / rec["detail_report"], EXP_ROOT)
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_experiment(item, base, output_path=dest), encoding="utf-8")
        written.append(dest)
    seen = set()
    for item in baselines + experiments:
        portable = ensemble_of(item["doc"]).get("portable_report")
        if not portable or portable in seen:
            continue
        seen.add(portable)
        dest = _resolve_research_path(EXP_ROOT / portable, EXP_ROOT)
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_snapshot(item, output_path=dest), encoding="utf-8")
        written.append(dest)
    return written


def main() -> None:
    paths = write_all()
    for path in paths:
        print(f"written: {path}")


if __name__ == "__main__":
    main()
