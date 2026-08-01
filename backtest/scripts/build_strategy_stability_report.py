"""Build the standalone full-period strategy stability diagnostic report."""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from phase_s_protocol import CURRENT_STRATEGY_BASELINE_ID, strategy_grid

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = REPO_ROOT / "backtest/experiments/registry.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "backtest/experiments/strategy_stability_report.html"
METRICS = (
    ("annualized_return", "扣费年化", True),
    ("sharpe_ratio", "夏普", False),
    ("calmar_ratio", "卡玛", False),
    ("annualized_volatility", "年化波动", True),
    ("max_drawdown", "最大回撤", True),
    ("annualized_one_way_turnover", "年化单边换手", False),
)
STABILITY_SUMMARIES = (
    ("positive_complete_years", "完整年正收益数", False),
    ("complete_year_sharpe_median", "完整年夏普中位", False),
    ("worst_complete_year_max_drawdown", "最差完整年回撤", True),
)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _fmt(value: Any, percent: bool = False) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _esc(value)
    return f"{number * 100:.2f}%" if percent else f"{number:.3f}"


def _ordered(row: dict) -> list[dict]:
    results = list(row.get("diagnostic_results") or [])
    return sorted(
        results,
        key=lambda item: (
            item.get("candidate_id") != CURRENT_STRATEGY_BASELINE_ID,
        ),
    )


def _table(rows: list[dict], *, yearly: bool = False, table_class: str = "") -> str:
    prefix = "<th>年度</th>" if yearly else ""
    headers = "".join(f"<th>{label}</th>" for _, label, _ in METRICS)
    if not yearly:
        headers += "".join(f"<th>{label}</th>" for _, label, _ in STABILITY_SUMMARIES)
    body = []
    for candidate in rows:
        year_items = sorted((candidate.get("years") or {}).items()) if yearly else [(None, candidate.get("full_period") or {})]
        if yearly and not year_items:
            continue
        for year, metrics in year_items:
            cells = []
            if yearly:
                partial = "（部分年度）" if metrics.get("partial_year") else ""
                cells.append(f"<td>{_esc(year)}{partial}</td>")
            cells.extend(f"<td class=\"num\">{_fmt(metrics.get(key), percent)}</td>" for key, _, percent in METRICS)
            if not yearly:
                cells.extend(
                    f'<td class="num">{_fmt(candidate.get(key), percent)}</td>'
                    for key, _, percent in STABILITY_SUMMARIES
                )
            status = candidate.get("status") or "—"
            error_lines = str(candidate.get("error") or "").splitlines()
            reason = error_lines[0] if error_lines else ""
            attempts = 1 + len(candidate.get("previous_attempts") or [])
            note = f"{reason}；{attempts} 次" if reason else f"{attempts} 次"
            body.append(f"<tr><td>{_esc(candidate.get('candidate_id'))}</td>{''.join(cells)}<td>{_esc(status)}</td><td>{_esc(note)}</td></tr>")
    class_attr = f' class="{table_class}"' if table_class else ""
    return f"<table{class_attr}><thead><tr><th>策略</th>{prefix}{headers}<th>状态</th><th>说明</th></tr></thead><tbody>{''.join(body)}</tbody></table>"


def _benchmark_context(rows: list[dict]) -> str:
    value = next(
        (
            item.get("full_period", {}).get("benchmark_cumulative_return")
            for item in rows
            if item.get("full_period", {}).get("benchmark_cumulative_return") is not None
        ),
        None,
    )
    return f'<p class="note">CSI1000 区间累计收益：{_fmt(value, True)}</p>'


def build_html(rows: Sequence[dict]) -> str:
    b6_matches = [
        row
        for row in rows
        if row.get("exp_id") == "strategy-stability-full-period/b6-m"
        and row.get("conclusion") == "diagnostic_no_selection"
    ]
    if len(b6_matches) != 1:
        raise ValueError("report requires exactly one B6-M stability diagnostic row")
    b6_row = b6_matches[0]
    b6 = _ordered(b6_row)
    expected_ids = {item["candidate_id"] for item in strategy_grid("b6-m")}
    actual_ids = {item.get("candidate_id") for item in b6}
    if actual_ids != expected_ids or len(b6) != len(expected_ids):
        raise ValueError("B6-M stability diagnostic candidate set is incomplete")
    sections = [
        '<section class="model" id="b6-m"><h2>B6-M</h2>'
        '<h3>全周期连续组合（2020-01-13 至 2026-07-31）</h3>'
        + _benchmark_context(b6)
        + _table(b6, table_class="full-period")
        + '<h3>自然年拆分</h3><p class="note">2020 与 2026 为部分年度；其余年份为完整自然年。</p>'
        + _table(b6, yearly=True, table_class="yearly")
        + "</section>"
    ]
    neighborhood = [
        item for item in b6
        if item.get("topk") == 30 and item.get("n_drop") in (2, 3) and item.get("hold_thresh") in (5, 10, 20)
    ]
    sections.append(
        '<section id="b6-neighborhood"><h2>B6-M Top30 邻域对照</h2>'
        '<p class="note">固定 Top30，对比 d2/d3 与 h5/h10/h20；仅作敏感性诊断。</p>'
        + _table(neighborhood, table_class="full-period")
        + "</section>"
    )
    css = """
body{font-family:-apple-system,'PingFang SC',sans-serif;max-width:1500px;margin:24px auto;color:#172033;background:#f7f8fa}
h1{font-size:24px}h2{margin-top:34px;border-bottom:2px solid #355f9d;padding-bottom:7px}h3{font-size:15px;margin-top:20px}
.meta,.note{color:#596579;font-size:13px}.card{background:#fff;border:1px solid #dfe4eb;border-radius:8px;padding:14px 18px}
table{border-collapse:collapse;width:100%;background:#fff;margin:10px 0 24px;font-size:12px}th,td{border:1px solid #dfe4eb;padding:5px 7px}th{background:#edf2f8;white-space:nowrap}td.num{text-align:right;font-variant-numeric:tabular-nums}tbody tr:first-child{background:#fff7dc}tbody tr:nth-child(even){background:#f8fafc}
"""
    generated = datetime.now().isoformat(timespec="seconds")
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Phase S 全周期稳定性诊断</title><style>{css}</style></head><body>"
        '<h1>Phase S 全周期稳定性诊断（仅 CSI1000）</h1>'
        f'<div class="card"><p class="meta">生成时间：{generated}</p>'
        '<p>50 万账户、当前实盘费率；同一组合从 2020-01-13 连续运行至 2026-07-31。结果只用于回看稳定性，不产生策略胜者，也不改变实盘配置。</p>'
        '<p class="note">指标均为扣费后的绝对收益口径；夏普无风险利率取 0。不使用相对绩效指标。</p></div>'
        + "".join(sections)
        + "</body></html>"
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    rows = [json.loads(line) for line in args.registry.read_text(encoding="utf-8").splitlines() if line.strip()]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(rows), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
