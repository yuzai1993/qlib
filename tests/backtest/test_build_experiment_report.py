from __future__ import annotations

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
