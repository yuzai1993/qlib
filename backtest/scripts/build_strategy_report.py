"""Historical Phase S renderer with a CLI routed to the unified report."""

from __future__ import annotations

import argparse
import html
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "backtest/experiments/registry.jsonl"
UNIFIED_REPORT = ROOT / "backtest/experiments/strategy_stability_report.html"
BASELINE_ID = "topk-t10-d2-h1"


def load_registry(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _metric(value: Any, percent: bool = False) -> str:
    if value is None:
        return "—"
    number = float(value)
    if not math.isfinite(number):
        return "无效"
    return f"{number:.2%}" if percent else f"{number:.4f}"


def _ordered_valid_rows(row: dict) -> list[dict]:
    rows = list(row.get("valid_results") or [])
    def ir_value(item: dict) -> float:
        try:
            value = float(item.get("excess_with_cost_information_ratio"))
        except (TypeError, ValueError):
            return float("-inf")
        return value if math.isfinite(value) else float("-inf")
    return sorted(
        rows,
        key=lambda item: (
            item.get("candidate_id") != BASELINE_ID,
            -ir_value(item),
            str(item.get("candidate_id") or ""),
        ),
    )


def _valid_table(row: dict) -> str:
    winner = row.get("selected_candidate_id")
    body = []
    for item in _ordered_valid_rows(row):
        row_classes = []
        if item.get("candidate_id") == winner:
            row_classes.append("valid-winner")
        if item.get("status") != "success":
            row_classes.append("invalid-result")
        classes = f' class="{" ".join(row_classes)}"' if row_classes else ""
        body.append(
            f"<tr{classes}><td>{_esc(item.get('candidate_id'))}</td>"
            f"<td>{_esc(item.get('status'))}</td>"
            f"<td>{_metric(item.get('excess_with_cost_information_ratio'))}</td>"
            f"<td>{_metric(item.get('excess_with_cost_annualized_return'), True)}</td>"
            f"<td>{_metric(item.get('excess_with_cost_max_drawdown'), True)}</td>"
            f"<td>{_metric(item.get('annualized_one_way_turnover'))}</td></tr>"
        )
    return (
        "<table><thead><tr><th>候选</th><th>状态</th><th>扣费超额 IR</th><th>扣费超额年化</th>"
        "<th>扣费最大回撤</th><th>年化单边换手</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _audit_list(row: dict) -> str:
    items = []
    for candidate in row.get("valid_results") or []:
        attempts = candidate.get("previous_attempts") or []
        if attempts:
            items.append(
                f"<li><code>{_esc(candidate.get('candidate_id'))}</code>："
                f"{len(attempts)} 次工程失败后重跑；最终状态 {_esc(candidate.get('status'))}</li>"
            )
        elif candidate.get("status") != "success":
            items.append(
                f"<li><code>{_esc(candidate.get('candidate_id'))}</code>："
                f"{_esc(candidate.get('status'))} — {_esc(candidate.get('error'))}</li>"
            )
    return "<p>无失败或重试记录。</p>" if not items else "<ul>" + "".join(items) + "</ul>"


def _test_table(row: dict) -> str:
    body = []
    for pool in ("csi1000", "csi300", "csi500"):
        candidates = row.get("test_results", {}).get(pool, [])
        candidates = sorted(
            candidates,
            key=lambda item: item.get("candidate_id") != BASELINE_ID,
        )
        for item in candidates:
            body.append(
                f"<tr><td>{pool.upper()}</td><td>{_esc(item.get('candidate_id'))}</td>"
                f"<td>{_metric(item.get('excess_with_cost_information_ratio'))}</td>"
                f"<td>{_metric(item.get('excess_with_cost_annualized_return'), True)}</td>"
                f"<td>{_metric(item.get('excess_with_cost_max_drawdown'), True)}</td></tr>"
            )
    return (
        "<table><thead><tr><th>测试池</th><th>策略</th><th>扣费超额 IR</th>"
        "<th>扣费超额年化</th><th>扣费最大回撤</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _yearly_table(row: dict) -> str:
    body = []
    for pool in ("csi1000", "csi300", "csi500"):
        for item in row.get("test_results", {}).get(pool, []):
            for year, value in sorted((item.get("yearly_ir") or {}).items()):
                body.append(
                    f"<tr><td>{pool.upper()}</td><td>{_esc(item.get('candidate_id'))}</td>"
                    f"<td>{_esc(year)}</td><td>{_metric(value)}</td></tr>"
                )
    return (
        "<table><thead><tr><th>测试池</th><th>策略</th><th>年度</th><th>扣费超额 IR</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
    )


def build_html(rows: Sequence[dict]) -> str:
    phase_s = {
        row.get("model_ref"): row
        for row in rows
        if row.get("phase") == "S"
        and str(row.get("exp_id") or "").startswith("strategy-sweep/")
        and row.get("model_ref") in ("b1-m", "b6-m")
    }
    sections = []
    for model_ref in ("b1-m", "b6-m"):
        row = phase_s.get(model_ref)
        if row is None:
            continue
        sections.append(
            f'<section id="{model_ref}"><h2>{model_ref.upper()}</h2>'
            f"<p>状态：{_esc(row.get('state'))}；模型：<code>{_esc(row.get('model_path'))}</code>；"
            f"SHA-256：<code>{_esc(row.get('model_sha256'))}</code></p>"
            f"<p>valid：{_esc(' ～ '.join(row.get('selection_segment') or []))}；"
            f"test：{_esc(' ～ '.join(row.get('test_segment') or []))}；"
            f"冻结胜者：<b>{_esc(row.get('selected_candidate_id') or '尚未选出')}</b></p>"
            "<h3>CSI1000 valid 全候选</h3>"
            + _valid_table(row)
            + "<h3>冻结胜者与 B1-S 基线 test 对比</h3>"
            + _test_table(row)
            + "<h3>test 分年度扣费超额 IR</h3>"
            + _yearly_table(row)
            + "<h3>失败与重试审计</h3>"
            + _audit_list(row)
            + "</section>"
        )
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Phase S 策略实验报告</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; margin: 24px auto; max-width: 1400px; color: #172033; }}
section {{ margin: 32px 0; }} table {{ border-collapse: collapse; width: 100%; margin: 12px 0 24px; }}
th, td {{ border: 1px solid #d8dee9; padding: 6px 8px; text-align: right; }} th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
th {{ background: #edf2f7; }} .valid-winner {{ background: #dcfce7; font-weight: 600; }} .invalid-result {{ background: #fff7ed; }} code {{ font-size: 12px; }}
</style></head><body><h1>Phase S 策略实验报告</h1>
<p>生成时间：{generated}；唯一数据源：<code>backtest/experiments/registry.jsonl</code>；账户：500,000 元。</p>
<p><b>选型声明：</b>仅使用 CSI1000 valid；test 不参与选型，仅评估已冻结胜者与 B1-S 基线。</p>
{''.join(sections)}</body></html>"""


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compatibility entry point for the unified Phase S report"
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    from build_strategy_stability_report import build_html as build_unified_html

    UNIFIED_REPORT.parent.mkdir(parents=True, exist_ok=True)
    rows = load_registry(args.registry)
    UNIFIED_REPORT.write_text(build_unified_html(rows), encoding="utf-8")
    print(
        f"{sum(row.get('phase') == 'S' for row in rows)} Phase S rows -> "
        f"{UNIFIED_REPORT}"
    )


if __name__ == "__main__":
    main()
