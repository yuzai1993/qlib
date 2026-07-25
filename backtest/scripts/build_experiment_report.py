"""从 backtest/experiments/registry.jsonl 生成标准实验 HTML 报告。

registry 是唯一数据源（backtest/EXPERIMENT_STANDARD.md 第 6/7 节）：
- 报告顶部为目录 + Phase M 指标说明；
- 每个实验方向（direction）一张独立表格；
- 表格列仅含：实验名、实验内容、各指标（一指标一列）。

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

# Phase M：4 指标 × 3 指数（默认不含全A）；表头两行：指标 / 指数
PHASE_M_METRIC_KEYS = ("rank_ic_mean", "rank_icir", "ic_mean", "icir")
PHASE_M_METRIC_LABELS = {
    "rank_ic_mean": "RankIC",
    "rank_icir": "RankICIR",
    "ic_mean": "IC",
    "icir": "ICIR",
}
DEFAULT_TEST_POOLS = ("csi300", "csi500", "csi1000")
POOL_DISPLAY = {
    "csi300": "CSI300",
    "csi500": "CSI500",
    "csi1000": "CSI1000",
    "all": "全A",
}

# Phase S：扁平指标列
PHASE_S_METRIC_KEYS = ("ir", "ann", "mdd")
PHASE_S_METRIC_LABELS = {
    "ir": "扣费超额IR",
    "ann": "扣费超额年化",
    "mdd": "扣费最大回撤",
}

CSS = """
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       margin: 24px auto; max-width: 1400px; color: #1a1a2e; background: #fafafa; }
h1 { font-size: 22px; } h2 { font-size: 18px; margin-top: 36px;
     border-bottom: 2px solid #4a6fa5; padding-bottom: 6px; }
h3 { font-size: 15px; margin: 16px 0 8px; color: #333; }
.meta { color: #666; font-size: 13px; }
nav, .legend { background: #fff; border: 1px solid #ddd; border-radius: 8px;
      padding: 12px 20px; margin: 16px 0; }
nav ul { margin: 6px 0; padding-left: 20px; }
nav a { color: #2a5aa0; text-decoration: none; } nav a:hover { text-decoration: underline; }
.legend table { margin: 8px 0 0; font-size: 13px; }
.legend th { text-align: left; }
.priority { color: #b45309; font-weight: 600; }
.priority-tag { display: inline-block; background: #fff7ed; color: #b45309;
                border: 1px solid #fdba74; border-radius: 3px; padding: 0 6px;
                font-size: 11px; margin-left: 6px; vertical-align: middle; }
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
table.exp tr.diagnostic { background: #fff7ed; color: #7c2d12; }
.eval-role { white-space: nowrap; font-family: ui-monospace, monospace; }
.best { color: #1a7f37; font-weight: 600; }
.empty { color: #999; }
"""

PHASE_M_LEGEND_HTML = """
<section class="legend" id="phase-m-metrics">
<h3>Phase M 指标说明</h3>
<p class="meta">评测标签固定为次日可交易收益
<code>Ref($close,-2)/Ref($close,-1)-1</code>，与训练标签无关；数值均为 test 段逐日截面相关的时间均值，再对 5 种子取平均。</p>
<table>
<thead><tr><th>指标</th><th>含义</th><th>关注优先级</th></tr></thead>
<tbody>
<tr>
  <td class="priority">RankIC</td>
  <td>预测分数与真实收益的<strong>截面 Spearman 秩相关</strong>时间均值。对异常值稳健，直接对应选股排序能力（Topk 策略吃的就是排序）。</td>
  <td><span class="priority-tag">最高优先</span> 主指标：先看各测试集 RankIC 是否相对基线整体抬升</td>
</tr>
<tr>
  <td class="priority">RankICIR</td>
  <td>RankIC 均值 / RankIC 标准差。衡量排序信号的<strong>时间稳定性</strong>（高均值但波动大 → RankICIR 低）。</td>
  <td><span class="priority-tag">次优先</span> 与 RankIC 一起看：均值升但 RankICIR 明显下降说明信号不稳</td>
</tr>
<tr>
  <td>IC</td>
  <td>预测分数与真实收益的截面 Pearson 相关时间均值。对极端值更敏感，作辅助对照。</td>
  <td>参考</td>
</tr>
<tr>
  <td>ICIR</td>
  <td>IC 均值 / IC 标准差。Pearson 口径下的时间稳定性。</td>
  <td>参考</td>
</tr>
</tbody>
</table>
<p class="meta">读表建议：同一方向内横向对比各测试集 RankIC；优先关注实盘目标池 <b>CSI300</b>，再看 CSI500 / CSI1000 是否同步改善（防过拟合单一市场）。
默认测试集为三指数，不含全A（需评估全A 时在实验设计中显式指定）。每个方向表格第一行为对应 <code>baseline_ref</code> 的 baseline 指标。</p>
</section>
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


def _fmt_metric(v: Any) -> str:
    if v is None or v == "":
        return '<span class="empty">—</span>'
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return _esc(v)


def _phase_of(row: dict) -> str:
    return str(row.get("phase") or "M").upper()


def _is_baseline_anchor(row: dict) -> bool:
    """registry 中 direction=baseline 的锚点行（供各方向表首行注入）。"""
    if str(row.get("direction") or "").lower() != "baseline":
        return False
    conclusion = str(row.get("conclusion") or "").lower()
    exp_id = str(row.get("exp_id") or "").lower()
    return conclusion == "baseline" or exp_id.startswith("baseline/")


def _baseline_ref_of(rows: Sequence[dict]) -> str:
    refs = [str(r.get("baseline_ref") or "").strip() for r in rows if r.get("baseline_ref")]
    # 同一方向应只有一个版本；取首次出现
    return refs[0] if refs else ""


def _resolve_baseline_row(all_rows: Sequence[dict], group: Sequence[dict]) -> Optional[dict]:
    """按方向内 baseline_ref 查找对应 baseline 锚点行。"""
    ref = _baseline_ref_of(group)
    anchors = [r for r in all_rows if _is_baseline_anchor(r)]
    if not anchors:
        return None
    if ref:
        matched = [r for r in anchors if str(r.get("baseline_ref") or "").strip() == ref]
        if matched:
            return matched[0]
    # 回退：唯一锚点或最新锚点
    return sorted(anchors, key=lambda r: str(r.get("date") or ""))[-1]


def _with_baseline_first(all_rows: Sequence[dict], group: Sequence[dict]) -> tuple[list[dict], str]:
    """确保表格第一行为 baseline；返回 (rows, baseline_ref 标注)。"""
    ref = _baseline_ref_of(group)
    baseline = _resolve_baseline_row(all_rows, group)
    ordered = list(group)
    if baseline is not None:
        base_id = baseline.get("exp_id")
        ordered = [r for r in ordered if r.get("exp_id") != base_id]
        ordered.insert(0, baseline)
        if not ref:
            ref = str(baseline.get("baseline_ref") or "").strip()
    return ordered, ref


def _test_pools(_rows: Sequence[dict] | None = None) -> list[str]:
    """报告默认固定三指数；registry 里的 all 不展示（除非日后改 DEFAULT_TEST_POOLS）。"""
    return list(DEFAULT_TEST_POOLS)


def _metric_columns_m(pools: Sequence[str]) -> list[tuple[str, str, str, bool]]:
    """返回 [(col_key, metric_label, pool, is_primary), ...]。"""
    cols: list[tuple[str, str, str, bool]] = []
    for metric in PHASE_M_METRIC_KEYS:
        primary = metric in ("rank_ic_mean", "rank_icir")
        label = PHASE_M_METRIC_LABELS[metric]
        for pool in pools:
            cols.append((f"{metric}@{pool}", label, pool, primary))
    return cols


def _metric_columns_s() -> list[tuple[str, str, bool]]:
    return [
        (k, PHASE_S_METRIC_LABELS[k], k == "ir") for k in PHASE_S_METRIC_KEYS
    ]


def _get_pool_metric(row: dict, pool: str, key: str) -> Any:
    ms = row.get("metrics_summary") or {}
    pool_m = ms.get(pool) if isinstance(ms, dict) else None
    if not isinstance(pool_m, dict):
        return None
    # 兼容旧字段名
    aliases = {
        "rank_ic_mean": ("rank_ic_mean", "rankic_mean"),
        "rank_icir": ("rank_icir", "rank_icir"),
        "ic_mean": ("ic_mean",),
        "icir": ("icir",),
    }
    for cand in aliases.get(key, (key,)):
        if cand in pool_m:
            return pool_m[cand]
    return None


def _get_flat_metric(row: dict, key: str) -> Any:
    """Phase S：优先 metrics_summary 扁平或主池字段，其次 strategy_baseline_b0s。"""
    ms = row.get("metrics_summary") or {}
    if isinstance(ms, dict):
        if key in ms and not isinstance(ms[key], dict):
            return ms[key]
        # 常见写法：{"csi300": {"ir": ...}}
        for pool in row.get("test_pools") or ("csi300",):
            pool_m = ms.get(pool)
            if isinstance(pool_m, dict) and key in pool_m:
                return pool_m[key]
    b0s = row.get("strategy_baseline_b0s") or {}
    alias = {"ir": "ir_mean", "ann": "ann_mean", "mdd": "mdd_mean"}
    return b0s.get(alias.get(key, key))


def _best_indices(
    values: Sequence[Optional[float]],
    *,
    higher_better: bool,
    eligible: Optional[Sequence[bool]] = None,
) -> set[int]:
    nums = [
        (i, v)
        for i, v in enumerate(values)
        if v is not None and (eligible is None or eligible[i])
    ]
    if not nums:
        return set()
    best = max(v for _, v in nums) if higher_better else min(v for _, v in nums)
    return {i for i, v in nums if v == best}


def _expand_phase_m_rows(rows: Sequence[dict]) -> list[dict]:
    expanded: list[dict] = []
    for row in rows:
        by_label = row.get("metrics_by_eval_label")
        if not isinstance(by_label, dict):
            display = dict(row)
            display["_eval_label_role"] = "eval_1d"
            display["_rowspan"] = 1
            display["_rowspan_first"] = True
            expanded.append(display)
            continue
        roles = [
            role for role in ("eval_1d", "eval_self")
            if isinstance(by_label.get(role), dict)
        ]
        for index, role in enumerate(roles):
            display = dict(row)
            display["metrics_summary"] = by_label[role]
            display["_eval_label_role"] = role
            display["_rowspan"] = len(roles)
            display["_rowspan_first"] = index == 0
            expanded.append(display)
    return expanded


def _build_phase_m_table(rows: Sequence[dict]) -> str:
    rows = _expand_phase_m_rows(rows)
    pools = _test_pools(rows)
    cols = _metric_columns_m(pools)
    eligible = [
        row.get("_eval_label_role") != "eval_self"
        for row in rows
    ]

    # 预取数值，标记每列最优
    col_vals: list[list[Optional[float]]] = []
    for col_key, _, pool, _ in cols:
        metric = col_key.split("@", 1)[0]
        vals: list[Optional[float]] = []
        for r in rows:
            v = _get_pool_metric(r, pool, metric)
            try:
                vals.append(float(v) if v is not None else None)
            except (TypeError, ValueError):
                vals.append(None)
        col_vals.append(vals)

    best_sets = [
        _best_indices(
            vals,
            higher_better=True,
            eligible=eligible,
        )
        for vals in col_vals
    ]

    n_pools = len(pools)
    # 两行表头：第一行指标（colspan），第二行指数
    row1 = [
        '<th rowspan="2">实验名</th>',
        '<th rowspan="2">实验内容</th>',
        '<th rowspan="2">评测标签</th>',
    ]
    for metric in PHASE_M_METRIC_KEYS:
        primary = metric in ("rank_ic_mean", "rank_icir")
        cls = ' class="primary"' if primary else ""
        row1.append(
            f'<th{cls} colspan="{n_pools}">{_esc(PHASE_M_METRIC_LABELS[metric])}</th>'
        )
    row2 = []
    for metric in PHASE_M_METRIC_KEYS:
        primary = metric in ("rank_ic_mean", "rank_icir")
        cls = ' class="primary"' if primary else ""
        for pool in pools:
            row2.append(
                f"<th{cls}>{_esc(POOL_DISPLAY.get(pool, pool))}</th>"
            )

    body_rows = []
    for ri, r in enumerate(rows):
        name = _esc(r.get("exp_id") or "")
        content = _esc(r.get("hypothesis") or r.get("note") or "")
        cells = []
        if r.get("_rowspan_first"):
            rowspan = int(r.get("_rowspan") or 1)
            rowspan_attr = f' rowspan="{rowspan}"' if rowspan > 1 else ""
            cells.extend(
                [
                    f'<td class="name"{rowspan_attr}>{name}</td>',
                    f'<td class="content"{rowspan_attr}>{content}</td>',
                ]
            )
        role = str(r.get("_eval_label_role") or "eval_1d")
        cells.append(f'<td class="eval-role">{_esc(role)}</td>')
        for (_, _, _, primary), vals, best in zip(cols, col_vals, best_sets):
            v = vals[ri]
            cls = ["num"]
            if primary:
                cls.append("primary")
            if ri in best and v is not None:
                cls.append("best")
            cells.append(f'<td class="{" ".join(cls)}">{_fmt_metric(v)}</td>')
        row_class = ' class="diagnostic"' if role == "eval_self" else ""
        body_rows.append(
            f"<tr{row_class}>" + "".join(cells) + "</tr>"
        )

    return (
        '<table class="exp"><thead>'
        f"<tr>{''.join(row1)}</tr><tr>{''.join(row2)}</tr>"
        "</thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
    )


def _build_phase_s_table(rows: Sequence[dict]) -> str:
    cols = _metric_columns_s()
    col_vals: list[list[Optional[float]]] = []
    for key, _, _ in cols:
        vals = []
        for r in rows:
            v = _get_flat_metric(r, key)
            try:
                vals.append(float(v) if v is not None else None)
            except (TypeError, ValueError):
                vals.append(None)
        col_vals.append(vals)

    # IR/ann 越高越好；mdd（通常为负）越高（越接近 0）越好
    best_sets = [
        _best_indices(vals, higher_better=True) for vals in col_vals
    ]

    header = ['<th>实验名</th>', '<th>实验内容</th>']
    for _, label, primary in cols:
        cls = ' class="primary"' if primary else ""
        header.append(f"<th{cls}>{_esc(label)}</th>")

    body_rows = []
    for ri, r in enumerate(rows):
        cells = [
            f'<td class="name">{_esc(r.get("exp_id") or "")}</td>',
            f'<td class="content">{_esc(r.get("hypothesis") or r.get("note") or "")}</td>',
        ]
        for ci, ((_, _, primary), vals, best) in enumerate(zip(cols, col_vals, best_sets)):
            v = vals[ri]
            cls = ["num"]
            if primary:
                cls.append("primary")
            if ri in best and v is not None:
                cls.append("best")
            cells.append(f'<td class="{" ".join(cls)}">{_fmt_metric(v)}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    return (
        '<table class="exp"><thead><tr>'
        + "".join(header)
        + "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
    )


def build_html(rows: Sequence[dict]) -> str:
    by_direction: dict[str, list[dict]] = {}
    for r in rows:
        by_direction.setdefault(r.get("direction") or "uncategorized", []).append(r)

    toc_items = [
        '<li><a href="#phase-m-metrics">Phase M 指标说明</a></li>'
    ]
    sections = []
    for direction in sorted(by_direction):
        group = sorted(by_direction[direction], key=lambda r: str(r.get("date") or ""))
        table_rows, baseline_ref = _with_baseline_first(rows, group)
        # toc 计数不含注入的外来 baseline 行
        n_native = len(group)
        anchor = _slug(direction)
        toc_items.append(
            f"<li><a href='#{anchor}'>{_esc(direction)}</a>（{n_native} 个实验）</li>"
        )
        ref_note = (
            f"<p class='meta'>对照 baseline：<b>{_esc(baseline_ref)}</b>"
            "（表格第一行）</p>"
            if baseline_ref
            else "<p class='meta'>警告：本方向缺少 <code>baseline_ref</code>，"
            "未注入 baseline 首行。</p>"
        )
        phases = {_phase_of(r) for r in table_rows}
        if phases == {"S"}:
            table = _build_phase_s_table(table_rows)
        else:
            # 混合或纯 M：用 Phase M 宽表（S 行指标列为空）
            table = _build_phase_m_table(table_rows)
            if "S" in phases:
                table += "<p class='meta'>本组含 Phase S 实验，策略指标见独立 strategy 方向表。</p>"
                s_rows = [r for r in table_rows if _phase_of(r) == "S"]
                if s_rows:
                    table += _build_phase_s_table(s_rows)
        sections.append(
            f"<h2 id='{anchor}'>{_esc(direction)}</h2>\n{ref_note}\n{table}"
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
{PHASE_M_LEGEND_HTML}
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
