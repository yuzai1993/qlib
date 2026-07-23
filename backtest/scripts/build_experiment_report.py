"""从 backtest/experiments/registry.jsonl 生成标准实验 HTML 报告。

registry 是唯一数据源（backtest/EXPERIMENT_STANDARD.md 第 6/7 节）：
- 报告顶部为目录（各实验方向锚点）；
- 每个实验方向（direction）一张独立表格；
- metrics_summary 按测试集展开为嵌套单元格，Phase M/S 指标字段可不同。

用法：
    /opt/anaconda3/envs/qlib/bin/python backtest/scripts/build_experiment_report.py \
        [--registry backtest/experiments/registry.jsonl] \
        [--output backtest/experiments/report.html]
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
DEFAULT_OUTPUT = BACKTEST_ROOT / "experiments" / "report.html"

METRIC_LABELS = {
    "rankic_mean": "RankIC均值",
    "rankic_std": "RankIC标准差",
    "rankic_delta_vs_b0": "ΔRankIC vs B0",
    "ic_mean": "IC均值",
    "icir": "ICIR",
    "rank_icir": "RankICIR",
    "ir": "扣费超额IR",
    "ann": "扣费超额年化",
    "mdd": "扣费最大回撤",
    "ir_delta_vs_b0": "ΔIR vs B0",
    "pairwise_wins": "成对胜出",
}

CSS = """
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       margin: 24px auto; max-width: 1280px; color: #1a1a2e; background: #fafafa; }
h1 { font-size: 22px; } h2 { font-size: 18px; margin-top: 36px;
     border-bottom: 2px solid #4a6fa5; padding-bottom: 6px; }
.meta { color: #666; font-size: 13px; }
nav { background: #fff; border: 1px solid #ddd; border-radius: 8px;
      padding: 12px 20px; margin: 16px 0; }
nav ul { margin: 6px 0; padding-left: 20px; }
nav a { color: #2a5aa0; text-decoration: none; } nav a:hover { text-decoration: underline; }
table { border-collapse: collapse; width: 100%; background: #fff; font-size: 13px;
        margin: 12px 0; }
th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #eef2f7; white-space: nowrap; }
tr:nth-child(even) { background: #f7f9fb; }
.metrics { white-space: nowrap; }
.metrics b { color: #4a6fa5; }
.paths { font-size: 11px; color: #555; word-break: break-all; }
.concl-improve { color: #1a7f37; font-weight: 600; }
.concl-regress { color: #c62828; font-weight: 600; }
.concl-neutral { color: #8a6d00; font-weight: 600; }
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


def _fmt_num(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    return _esc(v)


def _metrics_cell(metrics_summary: Any) -> str:
    """metrics_summary = {pool: {metric: value}} → 每池一行的嵌套展示。"""
    if not isinstance(metrics_summary, dict) or not metrics_summary:
        return ""
    parts = []
    for pool, m in metrics_summary.items():
        if isinstance(m, dict):
            kv = ", ".join(
                f"{METRIC_LABELS.get(k, k)}={_fmt_num(v)}" for k, v in m.items()
            )
        else:
            kv = _fmt_num(m)
        parts.append(f"<b>{_esc(pool)}</b>: {kv}")
    return "<div class='metrics'>" + "<br>".join(parts) + "</div>"


def _paths_cell(row: dict) -> str:
    items = list(row.get("configs") or []) + list(row.get("result_dirs") or [])
    if not items:
        return ""
    return "<div class='paths'>" + "<br>".join(_esc(p) for p in items) + "</div>"


def _conclusion_cell(row: dict) -> str:
    c = row.get("conclusion") or row.get("gate") or ""
    cls = {
        "improve": "concl-improve",
        "pass": "concl-improve",
        "regress": "concl-regress",
        "fail": "concl-regress",
        "neutral": "concl-neutral",
    }.get(str(c).lower(), "")
    return f"<span class='{cls}'>{_esc(c)}</span>" if cls else _esc(c)


COLUMNS = [
    ("exp_id", "实验"),
    ("phase", "Phase"),
    ("date", "日期"),
    ("hypothesis", "假设"),
    ("train_pool", "训练池"),
    ("seeds", "种子"),
    ("data_version", "数据版本"),
    ("metrics", "指标（按测试集）"),
    ("conclusion", "结论"),
    ("note", "备注"),
    ("paths", "配置 / 结果路径"),
]


def _row_html(row: dict) -> str:
    cells = []
    for key, _ in COLUMNS:
        if key == "metrics":
            cells.append(_metrics_cell(row.get("metrics_summary")))
        elif key == "paths":
            cells.append(_paths_cell(row))
        elif key == "conclusion":
            cells.append(_conclusion_cell(row))
        elif key == "seeds":
            seeds = row.get("seeds") or []
            cells.append(_esc(f"{len(seeds)}个" if seeds else ""))
        else:
            cells.append(_esc(row.get(key)))
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def build_html(rows: Sequence[dict]) -> str:
    by_direction: dict[str, list[dict]] = {}
    for r in rows:
        by_direction.setdefault(r.get("direction") or "uncategorized", []).append(r)

    toc_items = []
    sections = []
    header = "".join(f"<th>{label}</th>" for _, label in COLUMNS)
    for direction in sorted(by_direction):
        group = sorted(by_direction[direction], key=lambda r: str(r.get("date") or ""))
        anchor = _slug(direction)
        toc_items.append(
            f"<li><a href='#{anchor}'>{_esc(direction)}</a>（{len(group)} 个实验）</li>"
        )
        body = "".join(_row_html(r) for r in group)
        sections.append(
            f"<h2 id='{anchor}'>{_esc(direction)}</h2>\n"
            f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
        )

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    toc = (
        "<nav><b>目录</b><ul>" + "".join(toc_items) + "</ul></nav>"
        if toc_items
        else "<nav><b>目录</b><p class='meta'>registry 为空，暂无实验记录。</p></nav>"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>实验报告 — EXPERIMENT_STANDARD</title>
<style>{CSS}</style>
</head>
<body>
<h1>实验报告</h1>
<p class="meta">生成时间 {generated} ·
数据源 <code>backtest/experiments/registry.jsonl</code> ·
规范 <code>backtest/EXPERIMENT_STANDARD.md</code></p>
{toc}
{"".join(sections)}
</body>
</html>
"""


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="registry.jsonl → 实验 HTML 报告")
    p.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    rows = load_registry(args.registry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(rows), encoding="utf-8")
    print(f"{len(rows)} 条记录 → {args.output}")


if __name__ == "__main__":
    main()
