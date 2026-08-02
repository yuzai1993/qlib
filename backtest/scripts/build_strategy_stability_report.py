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
CURRENT_STRATEGY_BASELINE_EXP_ID = "baseline/b2-s-on-b6-m"
FULL_NEIGHBORHOOD_EXP_ID = "strategy-neighborhood/b2-s-local-full-v2"
FULL_NEIGHBORHOOD_CORRECTION_EXP_ID = f"{FULL_NEIGHBORHOOD_EXP_ID}-correction-v1"
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


def _baseline_section(baseline_row: dict, candidates: Sequence[dict]) -> str:
    strategy = baseline_row.get("strategy") or {}
    candidate_id = strategy.get("candidate_id")
    matches = [item for item in candidates if item.get("candidate_id") == candidate_id]
    if len(matches) != 1:
        raise ValueError("B2-S baseline candidate must match exactly one B6-M diagnostic result")
    metrics = matches[0].get("full_period") or {}
    selection_segment = baseline_row.get("selection_segment") or []
    test_segment = baseline_row.get("test_segment") or []
    start = selection_segment[0] if selection_segment else "—"
    end = test_segment[-1] if test_segment else "—"
    pool = str(baseline_row.get("selection_pool") or "—").upper()
    params = (
        f"Top{_esc(strategy.get('topk'))} / "
        f"d{_esc(strategy.get('n_drop'))} / "
        f"h{_esc(strategy.get('hold_thresh'))}"
    )
    metric_headers = "".join(f"<th>{label}</th>" for _, label, _ in METRICS)
    metric_cells = "".join(
        f'<td class="num">{_fmt(metrics.get(key), percent)}</td>'
        for key, _, percent in METRICS
    )
    return (
        '<section id="current-baseline"><h2>当前策略 Baseline</h2>'
        '<table class="baseline"><thead><tr>'
        '<th>Baseline</th><th>冻结模型</th><th>市场</th><th>全周期</th><th>策略参数</th>'
        f"{metric_headers}</tr></thead><tbody><tr>"
        f"<td>{_esc(baseline_row.get('baseline_ref'))}</td>"
        f"<td>{_esc(baseline_row.get('frozen_model_ref'))}</td>"
        f"<td>{_esc(pool)}</td><td>{_esc(start)} 至 {_esc(end)}</td>"
        f"<td>{params}</td>{metric_cells}</tr></tbody></table></section>"
    )


def _full_neighborhood_section(
    row: dict[str, Any], correction: dict[str, Any]
) -> str:
    if row.get("state") != "complete":
        raise ValueError("full-period neighborhood row must be complete")
    if row.get("evaluation_mode") != "full_history_in_sample":
        raise ValueError("full-period neighborhood row lacks evaluation disclosure")
    if row.get("selection_pool") != "csi1000" or row.get(
        "selection_segment"
    ) != ["2020-01-13", "2026-07-31"]:
        raise ValueError("full-period neighborhood selection contract differs")
    winner = row.get("full_winner_metrics") or {}
    strategy = row.get("selected_strategy") or {}
    top50 = row.get("robust_top50") or []
    if len(top50) != 50:
        raise ValueError("full-period neighborhood robust Top 50 is incomplete")
    selected_id = row.get("selected_candidate_id")
    if not selected_id or top50[0].get("candidate_id") != selected_id:
        raise ValueError("full-period neighborhood winner differs from robust ranking")
    if (
        correction.get("exp_id") != FULL_NEIGHBORHOOD_CORRECTION_EXP_ID
        or correction.get("state") != "correction"
        or correction.get("correction_of") != FULL_NEIGHBORHOOD_EXP_ID
    ):
        raise ValueError("full-period neighborhood requires its audit correction")
    if correction.get("full_result_sha256") != row.get("full_result_sha256"):
        raise ValueError("full-period correction does not match the completed result")
    baseline = correction.get("same_run_baseline") or {}
    corrected_winner = correction.get("robust_winner") or {}
    if not baseline.get("candidate_id") or corrected_winner.get("candidate_id") != selected_id:
        raise ValueError("full-period correction lacks the same-run comparison")

    metric_specs = (
        ("neighbor_ir_p25", "邻域 IR P25", False),
        ("excess_with_cost_information_ratio", "扣费超额 IR", False),
        ("excess_with_cost_annualized_return", "扣费超额年化", True),
        ("excess_with_cost_max_drawdown", "扣费最大回撤", True),
        ("annualized_one_way_turnover", "年化单边换手", False),
    )
    absolute = winner.get("absolute_portfolio") or {}
    winner_headers = "".join(
        f"<th>{label}</th>" for _, label, _ in metric_specs
    ) + "<th>绝对收益夏普</th><th>绝对收益卡玛</th><th>年化波动</th>"
    winner_cells = "".join(
        f'<td class="num">{_fmt(winner.get(key), percent)}</td>'
        for key, _, percent in metric_specs
    ) + "".join(
        f'<td class="num">{_fmt(absolute.get(key), percent)}</td>'
        for key, percent in (
            ("sharpe_ratio", False),
            ("calmar_ratio", False),
            ("annualized_volatility", True),
        )
    )
    params = (
        f"Top{_esc(strategy.get('topk'))} / d{_esc(strategy.get('n_drop'))} / "
        f"h{_esc(strategy.get('hold_thresh'))} / r{_fmt(strategy.get('risk_degree'))}"
    )
    winner_table = (
        '<table class="full-winner"><thead><tr><th>实验</th><th>候选</th><th>策略参数</th>'
        f"{winner_headers}</tr></thead><tbody><tr>"
        f"<td>{_esc(row.get('exp_id'))}</td><td>{_esc(selected_id)}</td>"
        f"<td>{params}</td>{winner_cells}</tr></tbody></table>"
    )
    comparison_specs = (
        ("excess_with_cost_information_ratio", "扣费超额 IR", False),
        ("excess_with_cost_annualized_return", "扣费超额年化", True),
        ("excess_with_cost_max_drawdown", "扣费最大回撤", True),
        ("annualized_one_way_turnover", "年化单边换手", False),
    )
    comparison_headers = "".join(
        f"<th>{label}</th>" for _, label, _ in comparison_specs
    )
    comparison_rows = "".join(
        "<tr>"
        f"<td>{_esc(label)}</td><td>{_esc(candidate.get('candidate_id'))}</td>"
        + "".join(
            f'<td class="num">{_fmt(candidate.get(key), percent)}</td>'
            for key, _, percent in comparison_specs
        )
        + "</tr>"
        for label, candidate in (
            ("B2-S v1.0 同运行基线", baseline),
            ("预登记稳健胜者", corrected_winner),
        )
    )
    comparison_table = (
        '<table class="same-run-excess-comparison"><thead><tr><th>角色</th><th>候选</th>'
        f"{comparison_headers}</tr></thead><tbody>{comparison_rows}</tbody></table>"
    )

    yearly_rows = "".join(
        f'<tr><td>{_esc(year)}</td><td class="num">{_fmt(value)}</td></tr>'
        for year, value in sorted((winner.get("yearly_ir") or {}).items())
    )
    yearly_table = (
        '<table class="winner-yearly-ir"><thead><tr><th>年度</th>'
        f"<th>扣费超额 IR</th></tr></thead><tbody>{yearly_rows}</tbody></table>"
    )
    ranking_rows = "".join(
        "<tr>"
        f'<td class="num">{rank}</td><td>{_esc(candidate.get("candidate_id"))}</td>'
        + "".join(
            f'<td class="num">{_fmt(candidate.get(key), percent)}</td>'
            for key, _, percent in metric_specs
        )
        + "</tr>"
        for rank, candidate in enumerate(top50, 1)
    )
    ranking_headers = "".join(
        f"<th>{label}</th>" for _, label, _ in metric_specs
    )
    ranking_table = (
        '<table class="robust-top50"><thead><tr><th>排名</th><th>候选</th>'
        f"{ranking_headers}</tr></thead><tbody>{ranking_rows}</tbody></table>"
    )
    return (
        '<section id="full-neighborhood"><h2>B2-S 全历史邻域比较</h2>'
        '<p class="winner-claim"><code>full_history_in_sample</code>：使用 CSI1000 '
        "2020-01-13 至 2026-07-31 全历史连续区间比较和选型；"
        "该胜者仅为研究候选，不是独立 holdout 结论，也不自动提升 B2-S。</p>"
        '<h3>同运行 B2-S 基线与稳健胜者（扣费超额）</h3>'
        '<p class="note">同一冻结 B6-M 预测、同一全历史区间、同一扣费超额指标。'
        'B2-S 基线在自身扣费超额 IR、年化与最大回撤上优于该稳健胜者；'
        '胜者自身指标更弱，但仅按预登记的轴向邻域 IR P25 规则入选，故不构成自动提升。</p>'
        f"{comparison_table}"
        '<div id="full-neighborhood-winner"><h3>全历史稳健胜者</h3>'
        f"{winner_table}<h3>胜者扣费分年度 IR</h3>{yearly_table}</div>"
        '<h3>稳健排名 Top 50</h3><p class="note">按轴向邻域扣费超额 IR '
        "25% 分位、候选自身 IR、年化、最大回撤、换手及候选 ID 依次并列排序。</p>"
        f"{ranking_table}</section>"
    )


def _full_neighborhood_status(row: dict[str, Any]) -> str:
    if row.get("state") != "preregistered":
        raise ValueError(
            f"unsupported full-period neighborhood state: {row.get('state')!r}"
        )
    if row.get("evaluation_mode") != "full_history_in_sample" or row.get(
        "selection_segment"
    ) != ["2020-01-13", "2026-07-31"]:
        raise ValueError("preregistered full-period neighborhood contract differs")
    return (
        '<section id="full-neighborhood-status"><h2>B2-S 全历史邻域比较（进行中）</h2>'
        '<div class="card"><p><b>实验：</b>'
        f"{_esc(row.get('exp_id'))}</p><p><b>状态：</b>{_esc(row.get('state'))}</p>"
        '<p class="note"><code>full_history_in_sample</code> 协议及 540 候选已预登记；'
        "结果尚未完成，因此不展示胜者或稳健 Top 50。</p></div></section>"
    )


def _artifact_link(path: Any) -> str:
    value = str(path or "").strip()
    if not value:
        return ""
    if value.startswith("backtest/experiments/"):
        href = value.removeprefix("backtest/experiments/")
    elif value.startswith("backtest/"):
        href = "../" + value.removeprefix("backtest/")
    elif Path(value).is_absolute():
        return _esc(value)
    else:
        href = value
    return f'<a href="{_esc(href)}">{_esc(Path(value).name)}</a>'


def _phase_s_audit_index(rows: Sequence[dict[str, Any]]) -> str:
    body = []
    artifact_keys = (
        "protocol_path",
        "full_result_path",
        "valid_result_path",
        "test_result_path",
        "prediction_manifest",
    )
    for row in sorted(rows, key=lambda item: str(item.get("exp_id") or "")):
        artifacts = "、".join(
            link
            for link in (_artifact_link(row.get(key)) for key in artifact_keys)
            if link
        ) or "—"
        segment = row.get("selection_segment") or []
        segment_text = " 至 ".join(str(item) for item in segment) if segment else "—"
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('exp_id'))}</td>"
            f"<td>{_esc(row.get('baseline_ref'))}</td>"
            f"<td>{_esc(row.get('state'))}</td>"
            f"<td>{_esc(row.get('evaluation_mode') or 'historical_audit')}</td>"
            f"<td>{_esc(segment_text)}</td>"
            f"<td>{_esc(row.get('conclusion'))}</td>"
            f"<td>{artifacts}</td></tr>"
        )
    return (
        '<section id="phase-s-audit-index"><h2>Phase S registry 审计索引</h2>'
        '<p class="note">覆盖 registry 中每一条 Phase S exp_id；历史 valid/test '
        "登记仅供审计，不作为当前选型表。</p>"
        '<table class="phase-s-audit"><thead><tr><th>实验</th><th>对照</th>'
        "<th>状态</th><th>评估模式</th><th>选型区间</th><th>结论</th><th>追踪产物</th>"
        f"</tr></thead><tbody>{''.join(body)}</tbody></table></section>"
    )


def build_html(rows: Sequence[dict]) -> str:
    phase_s_rows = [row for row in rows if str(row.get("phase") or "").upper() == "S"]
    b6_matches = [
        row
        for row in phase_s_rows
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
    baseline_matches = [
        row
        for row in phase_s_rows
        if row.get("exp_id") == CURRENT_STRATEGY_BASELINE_EXP_ID
    ]
    if len(baseline_matches) != 1:
        raise ValueError("report requires exactly one B2-S baseline row")
    sections = [
        _baseline_section(baseline_matches[0], b6),
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
    full_matches = [
        row for row in phase_s_rows if row.get("exp_id") == FULL_NEIGHBORHOOD_EXP_ID
    ]
    if len(full_matches) > 1:
        raise ValueError("report requires at most one full-period neighborhood row")
    if full_matches:
        full_row = full_matches[0]
        if full_row.get("state") == "complete":
            correction_matches = [
                row
                for row in phase_s_rows
                if row.get("exp_id") == FULL_NEIGHBORHOOD_CORRECTION_EXP_ID
            ]
            if len(correction_matches) != 1:
                raise ValueError(
                    "completed full-period neighborhood requires exactly one correction"
                )
            sections.append(_full_neighborhood_section(full_row, correction_matches[0]))
        else:
            sections.append(_full_neighborhood_status(full_row))
    sections.append(_phase_s_audit_index(phase_s_rows))
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
        f"<title>Phase S 统一研究报告</title><style>{css}</style></head><body>"
        '<h1>Phase S 统一研究报告（研究主目标池 CSI1000）</h1>'
        f'<div class="card"><p class="meta">生成时间：{generated}</p>'
        '<p><code>full_history_in_sample</code>：50 万元账户、当前费率与 CSI1000 '
        "benchmark；2020-01-13 至 2026-07-31 全历史连续区间允许用于策略比较。"
        "这些结果不属于独立 holdout 检验，也不自动改变 B2-S 或实盘配置。</p>"
        '<p class="note">稳定性表为扣费绝对收益口径；邻域选型表为扣费超额收益口径；夏普无风险利率取 0。</p></div>'
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
