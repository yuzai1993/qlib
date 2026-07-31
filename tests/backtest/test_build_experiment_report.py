from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_experiment_report as report  # noqa: E402


def _pool_metrics(value: float) -> dict:
    return {
        pool: {
            "rank_ic_mean": value,
            "rank_icir": value,
            "ic_mean": value,
            "icir": value,
        }
        for pool in ("csi300", "csi500", "csi1000")
    }


def test_label_design_renders_formal_then_diagnostic_rows():
    rows = [
        {
            "exp_id": "baseline/b1-m",
            "direction": "baseline",
            "phase": "M",
            "date": "2026-07-25",
            "baseline_ref": "B1 v1.0",
            "conclusion": "baseline",
            "metrics_summary": _pool_metrics(0.02),
        },
        {
            "exp_id": "label-design/cum-h20",
            "direction": "label-design",
            "phase": "M",
            "date": "2026-07-25",
            "baseline_ref": "B1 v1.0",
            "hypothesis": "twenty-day label",
            "metrics_by_eval_label": {
                "eval_1d": _pool_metrics(0.03),
                "eval_self": _pool_metrics(0.99),
            },
        },
    ]

    html = report.build_html(rows)
    section = html.split("id='direction-label-design'", 1)[1]

    assert section.index("baseline/b1-m") < section.index(
        "label-design/cum-h20"
    )
    assert section.index("eval_1d") < section.index("eval_self")
    assert 'rowspan="2"' in section
    assert 'class="diagnostic"' in section
    assert section.count('class="best"') == 0
    assert "diagnostic best" not in section


def test_phase_m_report_orders_primary_csi1000_pool_first():
    assert report._test_pools() == ["csi1000", "csi300", "csi500"]

    columns = report._metric_columns_m(report._test_pools())
    rank_ic_pools = [
        pool
        for key, _label, pool, _primary in columns
        if key.startswith("rank_ic_mean@")
    ]

    assert rank_ic_pools == ["csi1000", "csi300", "csi500"]
    assert "优先关注研究主目标池 <b>CSI1000</b>" in report.PHASE_M_LEGEND_HTML


def test_forward_holdout_rows_are_diagnostic_and_not_best_highlighted():
    rows = [
        {
            "exp_id": "baseline/b5-m",
            "direction": "baseline",
            "phase": "M",
            "baseline_ref": "B5 v1.0",
            "conclusion": "baseline",
            "metrics_summary": _pool_metrics(0.02),
        },
        {
            "exp_id": "train-recency/rankic-winner-stale",
            "direction": "train-recency",
            "phase": "M",
            "baseline_ref": "B5 v1.0",
            "evaluation_comparable_to_baseline": False,
            "metrics_summary": _pool_metrics(0.03),
        },
        {
            "exp_id": "train-recency/rankic-winner-post2020",
            "direction": "train-recency",
            "phase": "M",
            "baseline_ref": "B5 v1.0",
            "evaluation_comparable_to_baseline": False,
            "metrics_summary": _pool_metrics(0.04),
        },
    ]

    section = report.build_html(rows).split("id='direction-train-recency'", 1)[1]
    stale_row = section.split("train-recency/rankic-winner-stale", 1)[1].split("</tr>", 1)[0]
    expanded_row = section.split("train-recency/rankic-winner-post2020", 1)[1].split("</tr>", 1)[0]

    assert 'class="diagnostic"' in section
    assert " best" not in stale_row
    assert " best" not in expanded_row


def test_current_b6_baseline_has_complete_phase_m_metrics():
    registry = ROOT / "backtest" / "experiments" / "registry.jsonl"
    rows = [json.loads(line) for line in registry.read_text().splitlines()]
    baseline = [
        row
        for row in rows
        if row.get("direction") == "baseline"
        and row.get("phase") == "M"
        and row.get("conclusion") == "baseline"
    ][-1]

    assert baseline["exp_id"] == "baseline/b6-m"
    assert baseline["baseline_ref"] == "B6 v1.0"

    for pool in ("csi1000", "csi300", "csi500"):
        metrics = baseline["metrics_summary"][pool]
        for metric in report.PHASE_M_METRIC_KEYS:
            assert metric in metrics, f"{pool} missing {metric}"


def test_phase_s_direction_never_injects_phase_m_model_baseline():
    rows = [
        {
            "exp_id": "baseline/b6-m",
            "direction": "baseline",
            "phase": "M",
            "baseline_ref": "B6 v1.0",
            "conclusion": "baseline",
            "metrics_summary": _pool_metrics(0.04),
        },
        {
            "exp_id": "strategy-sweep/b6-m",
            "direction": "strategy-sweep-b6-m",
            "phase": "S",
            "baseline_ref": "B1-S v1.0",
            "metrics_summary": {"csi1000": {"ir": 1.2, "ann": 0.1, "mdd": -0.2}},
        },
    ]

    section = report.build_html(rows).split("id='direction-strategy-sweep-b6-m'", 1)[1]

    assert "strategy-sweep/b6-m" in section
    assert "baseline/b6-m" not in section
    assert "1.2000" in section


def test_baseline_table_is_chronological_but_historical_b5_group_keeps_b5():
    rows = [
        {
            "exp_id": "baseline/b5-m",
            "direction": "baseline",
            "phase": "M",
            "date": "2026-07-27",
            "baseline_ref": "B5 v1.0",
            "conclusion": "baseline",
            "metrics_summary": _pool_metrics(0.02),
        },
        {
            "exp_id": "model-hyperparam/old",
            "direction": "model-hyperparam",
            "phase": "M",
            "date": "2026-07-30",
            "baseline_ref": "B5 v1.0",
            "metrics_summary": _pool_metrics(0.03),
        },
        {
            "exp_id": "baseline/b6-m",
            "direction": "baseline",
            "phase": "M",
            "date": "2026-07-31",
            "baseline_ref": "B6 v1.0",
            "conclusion": "baseline",
            "metrics_summary": _pool_metrics(0.04),
        },
    ]

    html = report.build_html(rows)
    baseline_section = html.split("id='direction-baseline'", 1)[1].split("<h2", 1)[0]
    historical_section = html.split("id='direction-model-hyperparam'", 1)[1]

    assert baseline_section.index("baseline/b5-m") < baseline_section.index("baseline/b6-m")
    assert baseline_section.rfind('<td class="exp-id">') < baseline_section.index(
        "baseline/b6-m"
    )
    assert historical_section.index("baseline/b5-m") < historical_section.index(
        "model-hyperparam/old"
    )
    assert "baseline/b6-m" not in historical_section
