"""Render strategy parameter studies from the catalogue's frozen source records.

A study owns editorial descriptions and references; each result remains owned
by its original experiment. No group score or arithmetic aggregate is produced.
"""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path
import sys
from urllib.parse import quote

if __package__:
    from .research_layout import report_href, resolve_research_path
    from .research_labels import strategy_display_version, strategy_reference_text
else:
    from research_layout import report_href, resolve_research_path
    from research_labels import strategy_display_version, strategy_reference_text

REPO_ROOT = Path(__file__).resolve().parents[2]
METRICS = (
    ("annualized_return", "扣费累乘年化（CAGR）", "pct"),
    ("sharpe_ratio", "扣费夏普", "number"),
    ("alpha", "Alpha", "pct"),
    ("beta", "Beta", "number"),
    ("benchmark_cumulative_return", "基准涨幅", "pct"),
    ("max_drawdown", "最大回撤", "pct"),
    ("calmar_ratio", "Calmar", "number"),
    ("annualized_volatility", "年化波动", "pct"),
    ("annualized_one_way_turnover", "年化单边换手（倍）", "number"),
    ("annualized_return_arith", "算术年化（审计）", "pct"),
)
AXES = {"hold_thresh": "最短持有期", "force_sell_rank": "强卖排名阈值（F100）",
        "refill_force_sell": "强卖后补位", "topk": "持仓数", "n_drop": "每日常规换仓数",
        "risk_degree": "仓位比例"}
RESOURCE_LABELS = {"report": "原始报告", "metrics": "指标JSON", "registry_record": "登记原件",
                   "config": "运行配置", "plan": "说明文档", "metadata": "实验元数据"}
CSS = """
body{font-family:-apple-system,'PingFang SC',sans-serif;margin:0;background:#f6f7f9;color:#1c2733}
main{max-width:1440px;margin:auto;padding:28px 20px 64px}h1{font-size:24px}h2{font-size:18px;margin-top:26px}
p,li{line-height:1.7}dl{display:grid;grid-template-columns:100px 1fr;gap:8px;margin:20px 0}
dt{font-weight:600}dd{margin:0}table{border-collapse:collapse;width:100%;background:white;font-size:12px}
th,td{border:1px solid #dce3ed;padding:8px;text-align:right}th:first-child,td:first-child{text-align:left}
th{background:#eef2f8}tr.baseline{background:#eaf2ff}.table{overflow:auto}.note{color:#64748b;font-size:13px}
a{color:#2563eb}.artifacts{padding-left:22px}code{overflow-wrap:anywhere}
"""


def _text(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _format(value, kind: str) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.1f}%" if kind == "pct" else f"{value:.2f}"


def _strategy_version(record: dict) -> str:
    return strategy_display_version(record.get("display_version") or record.get("version") or "",
                                    kind=record.get("kind", "strategy"), track=record.get("track", "all_a"))


def _row_label(record: dict, label: str, control: bool) -> str:
    label = strategy_reference_text(label, kind=record.get("kind", "strategy"), track=record.get("track", "all_a"))
    return " · ".join(part for part in ("Baseline" if control else "实验组", _strategy_version(record), label) if part)


def _link(target, label: str, output: Path, root: Path) -> str:
    target = resolve_research_path(target, root)
    if not target.is_relative_to(root) or not target.is_file():
        return ""
    href = quote(report_href(target, output, root), safe="/.-_~")
    return f'<a href="{_text(href)}">{_text(label)}</a>'


def authoritative_rows(group: dict, records: list[dict]) -> list[tuple[dict, str, bool]]:
    """Validate the projection and order its declared control before variants."""
    summary = group.get("group_summary") or {}
    core = group.get("core_results") or {}
    if core.get("kind") != "strategy_parameter_comparison" or core.get("status") != "available":
        raise ValueError("strategy group has no available frozen comparison")
    refs = summary.get("member_refs") or []
    control = summary.get("control_ref")
    projected = core.get("records") or []
    projection = {row["id"]: row for row in projected}
    by_id = {row["id"]: row for row in records}
    if (not refs or control not in refs or len(refs) != len(set(refs))
            or len(projection) != len(projected) or set(projection) != set(refs)):
        raise ValueError("strategy group membership disagrees with authoritative sources")
    result = []
    for ref in [control, *(ref for ref in refs if ref != control)]:
        row = by_id.get(ref)
        if not row or row.get("kind") != "strategy" or row.get("track") != group.get("track"):
            raise ValueError(f"strategy group authoritative source is missing: {ref}")
        for field in ("metrics", "window", "seeds", "source"):
            if projection[ref].get(field) != row.get(field):
                raise ValueError(f"strategy group projection differs from frozen source {ref}: {field}")
        result.append((row, projection[ref].get("label") or row["name"], ref == control))
    return result


def render_group(group: dict, records: list[dict], *, repo_root: Path = REPO_ROOT,
                 output_path: Path | None = None, resource_paths: dict | None = None) -> str:
    """Render exact per-variant values; editorial group metrics are never read."""
    repo_root = Path(repo_root).resolve()
    output = output_path or repo_root / group["directory"] / "reports/summary.html"
    rows = authoritative_rows(group, records)
    summary = group["group_summary"]
    model = group.get("model_summary") or {}
    model_label = " · ".join(str(model.get(key)) for key in ("version", "name") if model.get(key))
    analysis = group.get("analysis") or {}
    title = group.get("name") or group.get("title") or group["id"]
    def editorial(value):
        return strategy_reference_text(value, kind=group.get("kind", "strategy"), track=group.get("track", "all_a"))
    title = editorial(title)
    axes = "、".join(AXES.get(axis, axis) for axis in summary.get("axes", []))
    parts = ["<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
             f"<title>{_text(title)}</title><style>{CSS}</style></head><body><main>",
             f"<h1>{_text(title)}</h1>",
             "<dl><dt>模型</dt><dd>" + _text(model_label or group.get("model_ref", "")) + "</dd>",
             f"<dt>固定口径</dt><dd>{_text(editorial(summary.get('fixed_summary', '')))}</dd>",
             f"<dt>变化参数</dt><dd>{_text(axes)}</dd>",
             f"<dt>实验结论</dt><dd>{_text(editorial(analysis.get('conclusion', '')))}</dd></dl>"]
    if analysis.get("limitations"):
        parts.append(f"<p class='note'>{_text(editorial(analysis['limitations']))}</p>")
    parts += ["<h2>Baseline与各变体核心指标</h2>",
              "<p class='note'>每行来自该变体的冻结历史结果；组内对照由元数据指定。"
              "原始登记中的历史Baseline及Benchmark版本继续保留。参数组不计算合并分数。</p>",
              "<div class='table'><table><thead><tr><th>组别</th><th>策略版本</th>",
              *(f"<th>{_text(label)}</th>" for _, label, _ in METRICS),
              "<th>评估窗口</th><th>种子</th></tr></thead><tbody>"]
    for record, label, control in rows:
        window = record.get("window") or {}
        period = "～".join(str(window.get(key, "")) for key in ("start", "end"))
        seeds = ", ".join(str(seed) for seed in record.get("seeds", []))
        style = ' class="baseline"' if control else ""
        parts.append(f'<tr{style} data-source-id="{_text(record["id"])}"><td>{_text(_row_label(record, label, control))}</td>')
        parts.append(f"<td>{_text(_strategy_version(record) or '—')}</td>")
        for key, _, kind in METRICS:
            value = record["metrics"].get(key)
            parts.append(f'<td data-metric="{key}" data-value="{_text(value)}">{_format(value, kind)}</td>')
        parts.append(f"<td>{_text(period)}</td><td>{_text(seeds)}</td></tr>")
    parts.append("</tbody></table></div><h2>原始报告与产物</h2>")
    resource_paths = resource_paths or {}
    for record, label, control in rows:
        parts.append(f"<h3>{_text(_row_label(record, label, control))}</h3>")
        parts.append(f"<p class='note'>原实验ID：<code>{_text(record['id'])}</code></p><ul class='artifacts'>")
        links = [(_link(record.get("source", ""), "指标JSON · 冻结全期、分年与分风格结果", output, repo_root),
                  resolve_research_path(record.get("source", ""), repo_root), "metrics")]
        for resource in record.get("resources", []):
            kind = resource.get("kind")
            target = resource_paths.get(resource.get("id"))
            if target and resource.get("available") and kind in RESOURCE_LABELS:
                label = RESOURCE_LABELS[kind] + " · " + Path(target).name
                links.append((_link(target, label, output, repo_root), resolve_research_path(target, repo_root), kind))
        seen = set()
        for link, actual, kind in links:
            if link and (kind, actual) not in seen:
                parts.append(f"<li>{link}</li>")
                seen.add((kind, actual))
        parts.append("</ul>")
    parts.append("</main></body></html>")
    return "".join(parts)


def load_catalog(repo_root: Path) -> tuple[dict, dict]:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from quant_platform.research import ResearchCatalog
    catalog = ResearchCatalog(repo_root)
    snapshot = catalog.snapshot()
    paths = {resource["id"]: catalog.resource(resource["id"])
             for row in snapshot.get("records", []) for resource in row.get("resources", [])
             if resource.get("available")}
    return snapshot, paths


def write_all(repo_root: Path = REPO_ROOT, *, snapshot: dict | None = None,
              resource_paths: dict | None = None) -> list[Path]:
    """Write generated group summaries, never their historical member reports."""
    if snapshot is None:
        snapshot, resource_paths = load_catalog(repo_root)
    written = []
    for group in snapshot.get("experiment_records", []):
        if not group.get("group_summary") or group.get("kind") != "strategy":
            continue
        destination = Path(repo_root) / group["directory"] / "reports/summary.html"
        rendered = render_group(group, snapshot["records"], repo_root=repo_root,
                                output_path=destination, resource_paths=resource_paths)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8")
        written.append(destination)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()
    for path in write_all(args.root.resolve()):
        print(f"written: {path}")


if __name__ == "__main__":
    main()
