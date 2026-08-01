from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import report_utils  # noqa: E402


def test_save_plotly_htmls_writes_self_contained_hoverable_chart(tmp_path):
    figure = go.Figure(
        go.Scatter(
            x=["2026-07-30", "2026-07-31"],
            y=[1.0, 1.1],
            hovertemplate="日期=%{x}<br>净值=%{y:.4f}<extra></extra>",
        )
    )

    paths = report_utils.save_plotly_htmls([figure], tmp_path, "report_graph")

    assert [path.name for path in paths] == ["report_graph.html"]
    payload = paths[0].read_text(encoding="utf-8")
    assert "plotly.js" in payload.lower()
    assert "hovertemplate" in payload
    assert '<script src="https://cdn.plot.ly' not in payload


def test_figures_html_embeds_interactive_html_and_keeps_png_compatibility():
    payload = report_utils._figures_html(
        {"report_graph": ["report_graph.html", "legacy.png"]}
    )

    assert '<iframe src="figures/report_graph.html"' in payload
    assert 'src="figures/legacy.png"' in payload
