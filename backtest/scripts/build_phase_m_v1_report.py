"""从 registry.jsonl 生成 Phase M v1 总报告（只放主指标）。

只收录 ``phase_m_protocol=v1`` 的行。每个 direction 一张表，
第一行固定为当前 baseline M0 H20（regime-adapt/m0-h20-label-v4）。
禁止手工编辑 HTML。
"""
from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = BACKTEST_ROOT / "experiments" / "registry.jsonl"
DEFAULT_OUTPUT = BACKTEST_ROOT / "experiments" / "phase_m_v1_report.html"

BASELINE_ID = "regime-adapt/m0-h20-label-v4"
BASELINE_NAME = "M0 H20"

PRIMARY_KEYS = (
    ("net_ann_excess", "扣费净年化", "ann", True),
    ("net_ann_vol", "扣费波动", "vol", True),
    ("net_sharpe", "扣费夏普", "sharpe", True),
    ("ann_excess", "非扣费年化", "ann", False),
    ("turnover", "日换手", "vol", False),
)

DIRECTION_TITLES = {
    "regime-adapt": "regime-adapt（M0 训练标签期限）",
    "m0-label": "M0 训练标签期限",
}

CSS = """
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       margin: 24px auto; max-width: 1280px; color: #1a1a2e; background: #fafafa; }
h1 { font-size: 22px; } h2 { font-size: 18px; margin-top: 36px;
     border-bottom: 2px solid #2563eb; padding-bottom: 6px; }
h3 { font-size: 15px; margin: 16px 0 8px; color: #333; }
.meta { color: #666; font-size: 13px; }
nav, .legend { background: #fff; border: 1px solid #ddd; border-radius: 8px;
      padding: 12px 20px; margin: 16px 0; }
nav ul { margin: 6px 0; padding-left: 20px; }
nav a { color: #2a5aa0; text-decoration: none; } nav a:hover { text-decoration: underline; }
.legend table { margin: 8px 0 0; font-size: 13px; }
.legend th { text-align: left; }
table.exp { border-collapse: collapse; width: 100%; background: #fff; font-size: 13px;
        margin: 12px 0; }
table.exp th, table.exp td { border: 1px solid #ddd; padding: 6px 8px;
        vertical-align: top; }
table.exp th { background: #eef2f7; white-space: nowrap; text-align: center; }
table.exp td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
table.exp td.name { white-space: nowrap; font-weight: 600; }
table.exp td.content { max-width: 360px; font-size: 12px; color: #333; }
table.exp tr:nth-child(even) { background: #f7f9fb; }
table.exp th.primary { background: #dbeafe; }
table.exp td.primary { background: #f0f7ff; }
table.exp tr.baseline { background: #eff6ff; }
.note { font-size: 12px; color: #64748b; }
.empty { color: #999; }
a { color: #2a5aa0; }
"""


def load_registry(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"registry 第 {i} 行不是合法 JSON: {exc}") from exc
    return rows


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _slug(direction: str) -> str:
    return "direction-" + "".join(c if c.isalnum() else "-" for c in direction.lower())


def _is_v1(row: dict) -> bool:
    return str(row.get("phase_m_protocol") or "") == "v1"


def display_name(row: dict) -> str:
    hh = row.get("train_label_horizon")
    if hh is not None:
        name = f"M0 H{hh}"
    else:
        name = str(row.get("arm") or row.get("exp_id") or "")
    if row.get("exp_id") == BASELINE_ID or str(row.get("baseline_ref") or "") == "self":
        return f"{name}（当前 baseline）"
    return name


def metric_of(row: dict, key: str) -> Optional[float]:
    metrics = row.get("metrics") or {}
    if not isinstance(metrics, dict):
        return None
    v = metrics.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt(v: Optional[float], kind: str) -> str:
    if v is None:
        return '<span class="empty">—</span>'
    if kind == "ann":
        return f"{v * 100:+.1f}%"
    if kind == "vol":
        return f"{v * 100:.1f}%"
    if kind == "sharpe":
        return f"{v:.2f}"
    return f"{v:.4f}"


def _with_baseline_first(all_v1: Sequence[dict], group: Sequence[dict]) -> list[dict]:
    baseline = next((r for r in all_v1 if r.get("exp_id") == BASELINE_ID), None)
    rest = [r for r in group if r.get("exp_id") != BASELINE_ID]
    rest.sort(key=lambda r: (r.get("train_label_horizon") is None, r.get("train_label_horizon") or 0, str(r.get("exp_id") or "")))
    if baseline is None:
        return rest
    return [baseline, *rest]


def _detail_href(row: dict) -> str:
    path = str(row.get("detail_report") or "").strip()
    if not path:
        return ""
    name = Path(path).name
    return name


def build_table(rows: Sequence[dict]) -> str:
    header = [
        "<th>实验名</th>",
        "<th>实验内容 / 假设</th>",
    ]
    for _, label, _, primary in PRIMARY_KEYS:
        cls = ' class="primary"' if primary else ""
        header.append(f"<th{cls}>{_esc(label)}</th>")
    header.append("<th>详细报告</th>")

    body = []
    for i, row in enumerate(rows):
        href = _detail_href(row)
        link = f'<a href="{_esc(href)}">{_esc(href)}</a>' if href else '<span class="empty">—</span>'
        cells = [
            f'<td class="name">{_esc(display_name(row))}'
            f'<div class="note">{_esc(row.get("exp_id") or "")}</div></td>',
            f'<td class="content">{_esc(row.get("hypothesis") or row.get("note") or "")}</td>',
        ]
        for key, _, kind, primary in PRIMARY_KEYS:
            cls = ["num"]
            if primary:
                cls.append("primary")
            cells.append(f'<td class="{" ".join(cls)}">{fmt(metric_of(row, key), kind)}</td>')
        cells.append(f"<td>{link}</td>")
        tr_cls = ' class="baseline"' if i == 0 else ""
        body.append("<tr" + tr_cls + ">" + "".join(cells) + "</tr>")

    return (
        '<table class="exp"><thead><tr>'
        + "".join(header)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def build_html(rows: Sequence[dict]) -> str:
    v1 = [r for r in rows if _is_v1(r)]
    by_direction: dict[str, list[dict]] = {}
    for r in v1:
        by_direction.setdefault(r.get("direction") or "uncategorized", []).append(r)

    toc = []
    sections = []
    for direction in sorted(by_direction):
        group = by_direction[direction]
        table_rows = _with_baseline_first(v1, group)
        title = DIRECTION_TITLES.get(direction, direction)
        anchor = _slug(direction)
        toc.append(f'<li><a href="#{anchor}">{_esc(title)}</a>（{len(group)} 个实验）</li>')
        sections.append(
            f'<h2 id="{anchor}">{_esc(title)}</h2>'
            f'<p class="meta">对照 baseline：<b>{_esc(BASELINE_NAME)}</b>'
            f'（<code>{_esc(BASELINE_ID)}</code>）；表格第一行固定为该基线。</p>'
            f"{build_table(table_rows)}"
        )

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    toc_html = (
        "<nav><b>目录</b><ul>" + "".join(toc) + "</ul></nav>"
        if toc
        else "<nav><b>目录</b><p class='meta'>registry 中尚无 phase_m_protocol=v1 的行。</p></nav>"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Phase M v1 总报告</title>
<style>{CSS}</style>
</head>
<body>
<h1>Phase M v1 总报告</h1>
<p class="meta">生成时间 {generated} ·
数据源 <code>backtest/experiments/registry.jsonl</code>（仅 <code>phase_m_protocol=v1</code>）·
规范 <code>backtest/EXPERIMENT_STANDARD.md</code> 第 5.1.2 节。
CSI1000 历史 Phase M（IC/RankIC）见 <a href="report.html">report.html</a>。</p>
<section class="legend">
<h3>评估口径（已冻结）</h3>
<ul class="meta">
<li><b>无北极星</b>。主格：<b>top5 × h5</b>。</li>
<li>主指标：扣费净年化、扣费波动（HAC 年化标准差）、扣费夏普（净年化/波动）。主表次列：非扣费年化、日换手。</li>
<li>日换手 = 相隔 h 日的单边换手 / h（h=5 全换仓 → 日换手 20%）；年化成本 = 238 × 日换手 × 0.092%。</li>
<li>过滤（评估宇宙，top-k 与等权基准同口径）：ST 名单、成交额 ≥ 1000 万、上市 ≥ 60 交易日；另剔 t+1 涨停/零量。</li>
<li>当前 baseline = <b>M0 H20</b>（<code>{BASELINE_ID}</code>）。子维度（网格 / 风格 / 分年）只在各实验详细报告。</li>
</ul>
</section>
{toc_html}
{"".join(sections)}
</body>
</html>
"""


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="registry.jsonl → Phase M v1 总报告")
    p.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    rows = load_registry(args.registry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(rows), encoding="utf-8")
    n_v1 = sum(1 for r in rows if _is_v1(r))
    print(f"{n_v1} 条 Phase M v1 记录 → {args.output}")


if __name__ == "__main__":
    main()
