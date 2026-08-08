"""Build the slim Phase S strategy report: baseline + B6 grid + diagnostics."""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from phase_s_protocol import CURRENT_STRATEGY_BASELINE_ID, strategy_grid
from strategy_neighborhood_protocol import (  # noqa: E402
    score_valid_candidates,
    strategy_neighborhood_grid,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = REPO_ROOT / "backtest/experiments/registry.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "backtest/experiments/strategy_stability_report.html"
CURRENT_STRATEGY_BASELINE_EXP_ID = "baseline/b4-s-on-b6-m"
LEGACY_STRATEGY_BASELINE_EXP_ID = "baseline/b3-s-on-b6-m"
FALLBACK_STRATEGY_BASELINE_EXP_ID = "baseline/b3-s-on-b6-m"
STABILITY_B6_EXP_ID = "strategy-stability-full-period/b6-m"
STABILITY_B6_EXP_ID_A10M = f"{STABILITY_B6_EXP_ID}-a10m"
FULL_NEIGHBORHOOD_EXP_ID = "strategy-neighborhood/b3-s-local-full-v1"
DEFAULT_NEIGHBORHOOD_RESULTS = (
    REPO_ROOT
    / "backtest/experiments/strategy-neighborhood/20260807_b3s_local_full/full_results.json"
)
BETA_OVERLAY_PREFIX = "strategy-beta-overlay/"
METRICS = (
    ("annualized_return", "扣费年化", True),
    ("sharpe_ratio", "夏普", False),
    ("alpha", "Alpha", True),
    ("beta", "Beta", False),
    ("benchmark_cumulative_return", "基准涨幅", True),
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
        year_items = (
            sorted((candidate.get("years") or {}).items())
            if yearly
            else [(None, candidate.get("full_period") or {})]
        )
        if yearly and not year_items:
            continue
        for year, metrics in year_items:
            cells = []
            if yearly:
                partial = "（部分年度）" if metrics.get("partial_year") else ""
                cells.append(f"<td>{_esc(year)}{partial}</td>")
            cells.extend(
                f'<td class="num">{_fmt(metrics.get(key), percent)}</td>'
                for key, _, percent in METRICS
            )
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
            body.append(
                f"<tr><td>{_esc(candidate.get('candidate_id'))}</td>"
                f"{''.join(cells)}<td>{_esc(status)}</td><td>{_esc(note)}</td></tr>"
            )
    class_attr = f' class="{table_class}"' if table_class else ""
    return (
        f"<table{class_attr}><thead><tr><th>策略</th>{prefix}{headers}"
        f"<th>状态</th><th>说明</th></tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


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


def _metric_cells_from_map(metrics: dict[str, Any]) -> str:
    return "".join(
        f'<td class="num">{_fmt(metrics.get(key), percent)}</td>'
        for key, _, percent in METRICS
    )


def _baseline_metrics(baseline_row: dict) -> dict[str, Any]:
    if baseline_row.get("full_period"):
        return dict(baseline_row["full_period"])
    summary = ((baseline_row.get("metrics_summary") or {}).get("csi1000_full") or {})
    return {
        "annualized_return": summary.get("ann"),
        "sharpe_ratio": summary.get("sharpe"),
        "alpha": summary.get("alpha"),
        "beta": summary.get("beta"),
        "benchmark_cumulative_return": summary.get("benchmark_cum"),
        "calmar_ratio": summary.get("calmar"),
        "annualized_volatility": summary.get("vol"),
        "max_drawdown": summary.get("mdd"),
        "annualized_one_way_turnover": summary.get("turnover"),
    }


def _neighborhood_candidate_id(baseline_row: dict) -> Optional[str]:
    strategy = baseline_row.get("strategy") or {}
    explicit = strategy.get("neighborhood_candidate_id")
    if explicit:
        return str(explicit)
    topk = strategy.get("topk")
    n_drop = strategy.get("n_drop")
    hold = strategy.get("hold_thresh")
    risk = strategy.get("risk_degree")
    if None in (topk, n_drop, hold, risk):
        # Historical B3-S row without risk_degree → r095 center of that grid.
        if (
            strategy.get("candidate_id") == "topk-t20-d2-h10"
            or baseline_row.get("baseline_ref") == "B3-S v1.0"
        ):
            return "topk-t20-d2-h10-r095"
        return None
    return (
        f"topk-t{int(topk)}-d{int(n_drop)}-h{int(hold)}-"
        f"r{int(round(float(risk) * 100)):03d}"
    )


def _load_neighborhood_by_id(
    phase_s_rows: Sequence[dict],
) -> dict[str, dict[str, Any]]:
    matches = [
        row for row in phase_s_rows if row.get("exp_id") == FULL_NEIGHBORHOOD_EXP_ID
    ]
    path: Optional[Path] = None
    if matches:
        raw = matches[0].get("full_result_path")
        if raw:
            path = Path(str(raw))
            if not path.is_absolute():
                path = REPO_ROOT / path
    if path is None or not path.exists():
        path = DEFAULT_NEIGHBORHOOD_RESULTS
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    scored, _ = score_valid_candidates(
        payload.get("all_rows") or [], strategy_neighborhood_grid()
    )
    return {str(row["candidate_id"]): row for row in scored}


def _baseline_block_rows(
    baseline_rows: Sequence[dict],
    *,
    neighborhood_by_id: Optional[dict[str, dict[str, Any]]] = None,
) -> str:
    neighborhood_by_id = neighborhood_by_id or {}
    metric_headers = "".join(f"<th>{label}</th>" for _, label, _ in METRICS)
    body = []
    for baseline_row in baseline_rows:
        strategy = baseline_row.get("strategy") or {}
        selection_segment = baseline_row.get("selection_segment") or []
        test_segment = baseline_row.get("test_segment") or []
        start = selection_segment[0] if selection_segment else "—"
        end = (
            test_segment[-1]
            if test_segment
            else (selection_segment[-1] if selection_segment else "—")
        )
        pool = str(baseline_row.get("selection_pool") or "—").upper()
        risk = strategy.get("risk_degree")
        params = (
            f"Top{_esc(strategy.get('topk'))} / "
            f"d{_esc(strategy.get('n_drop'))} / "
            f"h{_esc(strategy.get('hold_thresh'))}"
            + (f" / r{_fmt(risk)}" if risk is not None else "")
        )
        own_metrics = _baseline_metrics(baseline_row)
        nb_id = _neighborhood_candidate_id(baseline_row)
        nb_row = neighborhood_by_id.get(nb_id or "")
        # 邻域行：各列 = 轴向邻域内该绝对收益指标的 P25（不再单独挂「邻域 IR P25」列）
        nb_metrics = dict((nb_row or {}).get("neighbor_metrics_p25") or {})
        if not nb_metrics and baseline_row.get("neighbor_metrics_p25"):
            nb_metrics = dict(baseline_row["neighbor_metrics_p25"])
        own_cells = _metric_cells_from_map(own_metrics)
        nb_cells = _metric_cells_from_map(nb_metrics)
        body.append(
            "<tr>"
            f"<td>{_esc(baseline_row.get('baseline_ref'))}</td>"
            f"<td>{_esc(baseline_row.get('frozen_model_ref'))}</td>"
            f"<td>{_esc(pool)}</td><td>{_esc(start)} 至 {_esc(end)}</td>"
            f"<td>{params}</td>{own_cells}</tr>"
        )
        body.append(
            '<tr class="p25-row">'
            f"<td>{_esc(baseline_row.get('baseline_ref'))} · 邻域行</td>"
            f"<td>{_esc(baseline_row.get('frozen_model_ref'))}</td>"
            f"<td>{_esc(pool)}</td><td>{_esc(start)} 至 {_esc(end)}</td>"
            "<td>轴向邻域各指标 P25（稳健下界；晋升前必看）</td>"
            f"{nb_cells}</tr>"
        )
    return (
        '<section id="current-baseline"><h2>策略 Baseline</h2>'
        '<p class="note">上行 = 候选自身点指标；邻域行 = 轴向邻域内各同名绝对收益指标的 '
        "25% 分位（不是上行复制）。晋升策略 baseline 前必须先审查邻域行。</p>"
        '<table class="baseline"><thead><tr>'
        "<th>Baseline</th><th>冻结模型</th><th>市场</th><th>全周期</th><th>策略参数</th>"
        f"{metric_headers}</tr></thead><tbody>{''.join(body)}</tbody></table></section>"
    )


def _account_label(row: dict[str, Any]) -> str:
    account = row.get("account")
    if isinstance(account, (int, float)) and float(account) > 0:
        return f"{int(account) / 10000:.0f} 万元"
    return "—"


def _prefer_exp_rows(
    rows: Sequence[dict[str, Any]],
    preferred_id: str,
    fallback_id: str,
    *,
    ready: Optional[Any] = None,
) -> list[dict[str, Any]]:
    preferred = [row for row in rows if row.get("exp_id") == preferred_id]
    if ready is not None:
        preferred = [row for row in preferred if ready(row)]
    if preferred:
        return preferred
    return [row for row in rows if row.get("exp_id") == fallback_id]


def _absolute_from_neighborhood_item(item: dict[str, Any]) -> dict[str, Any]:
    absolute = dict(item.get("absolute_portfolio") or {})
    if absolute:
        return absolute
    return {
        "annualized_return": item.get("portfolio_annualized_return"),
        "sharpe_ratio": item.get("portfolio_information_ratio"),
        "alpha": item.get("alpha"),
        "beta": item.get("beta"),
        "benchmark_cumulative_return": item.get("benchmark_cumulative_return")
        or item.get("benchmark_cum_return"),
        "max_drawdown": item.get("portfolio_max_drawdown"),
        "annualized_one_way_turnover": item.get("annualized_one_way_turnover"),
    }


def _neighborhood_section(
    row: dict[str, Any], correction: Optional[dict[str, Any]]
) -> str:
    if row.get("state") != "complete":
        return (
            '<section id="full-neighborhood-status"><h2>B3-S 全历史邻域比较（进行中）</h2>'
            f'<p class="note">exp_id={_esc(row.get("exp_id"))}；'
            f"state={_esc(row.get('state'))}。</p></section>"
        )
    winner = row.get("full_winner_metrics") or {}
    strategy = row.get("selected_strategy") or {}
    top50 = row.get("robust_top50") or []
    if len(top50) < 1:
        raise ValueError("full-period neighborhood robust ranking is incomplete")
    selected_id = row.get("selected_candidate_id")
    absolute = _absolute_from_neighborhood_item(winner)
    headers = "<th>邻域 IR P25</th>" + "".join(
        f"<th>{label}</th>" for _, label, _ in METRICS
    )
    winner_cells = (
        f'<td class="num">{_fmt(winner.get("neighbor_ir_p25"))}</td>'
        + _metric_cells_from_map(absolute)
    )
    params = (
        f"Top{_esc(strategy.get('topk'))} / d{_esc(strategy.get('n_drop'))} / "
        f"h{_esc(strategy.get('hold_thresh'))} / r{_fmt(strategy.get('risk_degree'))}"
    )
    top_rows = []
    for item in top50[:20]:
        abs_metrics = _absolute_from_neighborhood_item(item)
        cells = (
            f'<td class="num">{_fmt(item.get("neighbor_ir_p25"))}</td>'
            + _metric_cells_from_map(abs_metrics)
        )
        top_rows.append(
            f"<tr><td>{_esc(item.get('candidate_id'))}</td>{cells}</tr>"
        )
    comparison = ""
    if correction is not None:
        baseline = correction.get("same_run_baseline") or {}
        robust = correction.get("robust_winner") or {}
        comparison = (
            "<h3>同运行 B3-S 基线对照（扣费超额）</h3>"
            '<table class="same-run-excess-comparison"><thead><tr>'
            "<th>序列</th><th>候选</th><th>扣费超额 IR</th><th>扣费超额年化</th>"
            "<th>扣费最大回撤</th><th>年化单边换手</th></tr></thead><tbody>"
            "<tr><td>B3-S 同运行基线</td>"
            f"<td>{_esc(baseline.get('candidate_id'))}</td>"
            f'<td class="num">{_fmt(baseline.get("excess_with_cost_information_ratio"))}</td>'
            f'<td class="num">{_fmt(baseline.get("excess_with_cost_annualized_return"), True)}</td>'
            f'<td class="num">{_fmt(baseline.get("excess_with_cost_max_drawdown"), True)}</td>'
            f'<td class="num">{_fmt(baseline.get("annualized_one_way_turnover"))}</td></tr>'
            "<tr><td>稳健胜者</td>"
            f"<td>{_esc(robust.get('candidate_id'))}</td>"
            f'<td class="num">{_fmt(robust.get("excess_with_cost_information_ratio"))}</td>'
            f'<td class="num">{_fmt(robust.get("excess_with_cost_annualized_return"), True)}</td>'
            f'<td class="num">{_fmt(robust.get("excess_with_cost_max_drawdown"), True)}</td>'
            f'<td class="num">{_fmt(robust.get("annualized_one_way_turnover"))}</td></tr>'
            "</tbody></table>"
        )
    account_text = _account_label(row)
    return (
        '<section id="full-neighborhood"><h2>B3-S 全历史邻域比较</h2>'
        f'<p class="note winner-claim"><code>full_history_in_sample</code>；'
        f"账户 {account_text}；围绕 B3-S（Top20/d2/h10）的 540 组轴向邻域，"
        "主排序为邻域扣费超额 IR P25。展示口径与 baseline 表一致（另附 P25）。"
        "不自动提升 baseline。</p>"
        "<h3>稳健胜者</h3>"
        '<table class="full-winner"><thead><tr><th>实验</th><th>候选</th><th>策略参数</th>'
        f"{headers}</tr></thead><tbody><tr>"
        f"<td>{_esc(row.get('exp_id'))}</td><td>{_esc(selected_id)}</td>"
        f"<td>{params}</td>{winner_cells}</tr></tbody></table>"
        f"{comparison}"
        "<h3>稳健 Top 20（按邻域 IR P25）</h3>"
        '<table class="robust-top50"><thead><tr><th>候选</th>'
        f"{headers}</tr></thead><tbody>{''.join(top_rows)}</tbody></table></section>"
    )


def _beta_section(rows: Sequence[dict[str, Any]]) -> str:
    beta_rows = [
        row
        for row in rows
        if str(row.get("exp_id") or "").startswith(BETA_OVERLAY_PREFIX)
        and row.get("state") == "complete"
    ]
    if not beta_rows:
        return ""
    body = []
    for row in beta_rows:
        metrics = row.get("metrics_summary") or {}
        baseline = metrics.get("baseline") or {}
        overlay = metrics.get("overlay_continuous") or {}
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('exp_id'))}</td>"
            f"<td>{_esc(row.get('baseline_ref'))}</td>"
            f"<td>{_esc((row.get('im_window') or ['—', '—'])[0])} 至 "
            f"{_esc((row.get('im_window') or ['—', '—'])[-1])}</td>"
            f'<td class="num">{_fmt(baseline.get("sharpe_ratio"))}</td>'
            f'<td class="num">{_fmt(overlay.get("sharpe_ratio"))}</td>'
            f'<td class="num">{_fmt(baseline.get("annualized_return"), True)}</td>'
            f'<td class="num">{_fmt(overlay.get("annualized_return"), True)}</td>'
            f"<td>{_esc(row.get('conclusion'))}</td>"
            "</tr>"
        )
    return (
        '<section id="beta-overlay"><h2>补 Beta（IM Overlay）</h2>'
        '<p class="note"><code>im_window_in_sample</code>；目标 β=1.0，滚动 60 日滞后估计；'
        "不自动晋升。</p>"
        "<table><thead><tr><th>实验</th><th>对照基线</th><th>IM 窗口</th>"
        "<th>股票腿夏普</th><th>Overlay 夏普</th>"
        "<th>股票腿年化</th><th>Overlay 年化</th><th>结论</th>"
        f"</tr></thead><tbody>{''.join(body)}</tbody></table></section>"
    )


def _account_diag_section(rows: Sequence[dict[str, Any]]) -> str:
    matches = [
        row
        for row in rows
        if str(row.get("exp_id") or "").startswith("strategy-account-diag/")
        and row.get("state") == "complete"
    ]
    if not matches:
        return ""
    body = []
    for row in matches:
        metrics = _baseline_metrics(row)
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('exp_id'))}</td>"
            f"<td>{_esc((row.get('strategy') or {}).get('candidate_id'))}</td>"
            f"<td>{_account_label(row)}</td>"
            f"{_metric_cells_from_map(metrics)}"
            f"<td>{_esc(row.get('conclusion'))}</td>"
            "</tr>"
        )
    headers = "".join(f"<th>{label}</th>" for _, label, _ in METRICS)
    return (
        '<section id="account-diag"><h2>账户规模诊断</h2>'
        '<p class="note">同一策略在不同初始资金下的扣费绝对收益口径对照。</p>'
        f"<table><thead><tr><th>实验</th><th>策略</th><th>账户</th>{headers}"
        f"<th>结论</th></tr></thead><tbody>{''.join(body)}</tbody></table></section>"
    )


def _extended_history_section(rows: Sequence[dict[str, Any]]) -> str:
    matches = [
        row
        for row in rows
        if str(row.get("exp_id") or "").startswith("strategy-extended-history/")
        and row.get("state") == "complete"
    ]
    if not matches:
        return ""
    body = []
    for row in matches:
        metrics = _baseline_metrics(row)
        segment = row.get("test_segment") or row.get("selection_segment") or ["—", "—"]
        report = row.get("report_html") or ""
        link = (
            f'<a href="{_esc(report)}">report</a>' if report else "—"
        )
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('exp_id'))}</td>"
            f"<td>{_esc(segment[0])} 至 {_esc(segment[-1])}</td>"
            f"{_metric_cells_from_map(metrics)}"
            f"<td>{link}</td>"
            "</tr>"
        )
    headers = "".join(f"<th>{label}</th>" for _, label, _ in METRICS)
    return (
        '<section id="extended-history"><h2>扩展历史回测（带图）</h2>'
        '<p class="note"><code>extended_history_in_sample</code>；'
        "用于观察更长样本，不作为默认选型口径。</p>"
        f"<table><thead><tr><th>实验</th><th>区间</th>{headers}"
        f"<th>报告</th></tr></thead><tbody>{''.join(body)}</tbody></table></section>"
    )


def build_html(rows: Sequence[dict]) -> str:
    phase_s_rows = [row for row in rows if str(row.get("phase") or "").upper() == "S"]
    b6_matches = _prefer_exp_rows(
        phase_s_rows,
        STABILITY_B6_EXP_ID_A10M,
        STABILITY_B6_EXP_ID,
        ready=lambda row: row.get("conclusion") == "diagnostic_no_selection",
    )
    b6_matches = [
        row
        for row in b6_matches
        if row.get("conclusion") == "diagnostic_no_selection"
    ]
    if len(b6_matches) != 1:
        raise ValueError("report requires exactly one B6-M stability diagnostic row")
    b6_row = b6_matches[0]
    b6 = _ordered(b6_row)
    expected_ids = {item["candidate_id"] for item in strategy_grid("b6-m")}
    actual_ids = {item.get("candidate_id") for item in b6}
    if actual_ids != expected_ids or len(b6) != len(expected_ids):
        raise ValueError("B6-M stability diagnostic candidate set is incomplete")

    current_baselines = [
        row
        for row in phase_s_rows
        if row.get("exp_id") == CURRENT_STRATEGY_BASELINE_EXP_ID
        and row.get("state") == "baseline"
    ]
    if not current_baselines:
        current_baselines = [
            row
            for row in phase_s_rows
            if row.get("exp_id") == FALLBACK_STRATEGY_BASELINE_EXP_ID
            and row.get("state") == "baseline"
        ]
    if len(current_baselines) != 1:
        raise ValueError("report requires exactly one current strategy baseline row")
    current = current_baselines[0]
    legacy_baselines = [
        row
        for row in phase_s_rows
        if row.get("exp_id") == LEGACY_STRATEGY_BASELINE_EXP_ID
        and row.get("state") == "baseline"
        and row.get("exp_id") != current.get("exp_id")
    ]
    # Show current first, then demoted historical baseline if still present.
    baseline_block = [current, *legacy_baselines]
    neighborhood_by_id = _load_neighborhood_by_id(phase_s_rows)
    yearly_candidate = {
        "candidate_id": (current.get("strategy") or {}).get("candidate_id")
        or CURRENT_STRATEGY_BASELINE_ID,
        "years": current.get("years") or {},
        "status": "success",
    }
    account_text = _account_label(b6_row)
    sections = [
        _baseline_block_rows(baseline_block, neighborhood_by_id=neighborhood_by_id),
        '<section class="model" id="b6-m"><h2>B6-M 策略对照</h2>'
        f'<p class="note">账户：{account_text}；exp_id={_esc(b6_row.get("exp_id"))}。'
        "扣费绝对收益口径；Alpha/Beta 相对 CSI1000 基准、rf=0。"
        "该表为历史稳定性网格，不等同于当前 baseline。</p>"
        "<h3>全周期连续组合（2020-01-13 至 2026-07-31）</h3>"
        + _benchmark_context(b6)
        + _table(b6, table_class="full-period")
        + "</section>",
        '<section id="baseline-yearly"><h2>Baseline 自然年拆分</h2>'
        f'<p class="note">仅展示当前 baseline '
        f"{_esc(CURRENT_STRATEGY_BASELINE_ID)}；部分年度单独标注。</p>"
        + _table([yearly_candidate], yearly=True, table_class="yearly")
        + "</section>",
    ]
    neighborhood_matches = [
        row for row in phase_s_rows if row.get("exp_id") == FULL_NEIGHBORHOOD_EXP_ID
    ]
    if len(neighborhood_matches) > 1:
        raise ValueError("report requires at most one B3-S neighborhood row")
    if neighborhood_matches:
        neighborhood = neighborhood_matches[0]
        correction = None
        if neighborhood.get("state") == "complete":
            correction_id = f"{FULL_NEIGHBORHOOD_EXP_ID}-correction-v1"
            correction_matches = [
                row for row in phase_s_rows if row.get("exp_id") == correction_id
            ]
            if len(correction_matches) != 1:
                raise ValueError(
                    "completed neighborhood requires exactly one correction row"
                )
            correction = correction_matches[0]
        sections.append(_neighborhood_section(neighborhood, correction))
    sections.append(_beta_section(phase_s_rows))
    sections.append(_account_diag_section(phase_s_rows))
    sections.append(_extended_history_section(phase_s_rows))
    css = """
body{font-family:-apple-system,'PingFang SC',sans-serif;max-width:1500px;margin:24px auto;color:#172033;background:#f7f8fa}
h1{font-size:24px}h2{margin-top:34px;border-bottom:2px solid #355f9d;padding-bottom:7px}h3{font-size:15px;margin-top:20px}
.meta,.note{color:#596579;font-size:13px}.card{background:#fff;border:1px solid #dfe4eb;border-radius:8px;padding:14px 18px}
table{border-collapse:collapse;width:100%;background:#fff;margin:10px 0 24px;font-size:12px}th,td{border:1px solid #dfe4eb;padding:5px 7px}th{background:#edf2f8;white-space:nowrap}td.num{text-align:right;font-variant-numeric:tabular-nums}tbody tr:first-child{background:#fff7dc}tbody tr:nth-child(even){background:#f8fafc}
tr.p25-row{background:#eef7ff !important;font-style:italic}
"""
    generated = datetime.now().isoformat(timespec="seconds")
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Phase S 统一研究报告</title><style>{css}</style></head><body>"
        "<h1>Phase S 统一研究报告（研究主目标池 CSI1000）</h1>"
        f'<div class="card"><p class="meta">生成时间：{generated}</p>'
        f"<p><code>full_history_in_sample</code>：{account_text}账户、当前费率与 CSI1000 "
        "benchmark；2020-01-13 至 2026-07-31 全历史连续区间允许用于策略比较。"
        "这些结果不属于独立 holdout 检验。</p>"
        '<p class="note">稳定性表为扣费绝对收益口径；夏普无风险利率取 0；'
        "Alpha/Beta 为组合扣费收益相对基准的 CAPM 估计。"
        "策略晋升 baseline 前必须先审查邻域行（轴向邻域绝对指标 P25）。</p></div>"
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
    rows = [
        json.loads(line)
        for line in args.registry.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(rows), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
