from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import build_regime_m0_label_report as report  # noqa: E402


def _cell(ann: float, sharpe: float, *, vol: float = 0.40, to: float = 0.30) -> dict:
    return {
        "net_ann": ann,
        "net_ann_vol": vol,
        "net_sharpe": sharpe,
        "ann": ann + 0.05,
        "turnover": to,
        "n_days": 1451,
    }


def _grid(scale: float) -> dict:
    head = {}
    for k in report.KS:
        head[k] = {h: _cell(0.10 * scale, 0.40) for h in report.HS}
    years = {
        y: {
            k: {h: _cell(0.12 * scale, 0.50) for h in report.HS} for k in report.KS
        }
        for y in ("2021", "2026")
    }
    regimes = {
        r: {k: {h: _cell(0.08 * scale, 0.30) for h in report.HS} for k in report.KS}
        for r in report.REGS
    }
    return {
        "filters": {"st_filter": "daily", "min_listing_days": 60, "min_amount": 10_000_000},
        "pools": {
            "all": {
                "seed_mean": {"head": {"5": {"5": _cell(0.99, 9.99)}}},
                "ensemble": {"head": head, "head_years": years, "head_regimes": regimes},
            }
        },
    }


def test_grid_is_k1to5_by_h2345():
    assert report.KS == ["1", "2", "3", "4", "5"]
    assert report.HS == ["2", "3", "4", "5"]


def test_report_renders_new_grid_from_ensemble():
    html = report.render(
        {
            "m0h20": _grid(2.0),
            "m0h5": _grid(1.0),
        }
    )
    assert "网格 top∈{1,2,3,4,5} × h∈{2,3,4,5}" in html
    assert ">k=1<" in html and ">k=4<" in html and ">k=5<" in html
    assert ">h4<" in html
    assert "k=15" not in html
    assert "k=50" not in html
    assert ">h10<" not in html
    assert "主格 top5 × h5" not in html
    assert "+10.0%" in html
    assert "+20.0%" in html
    assert "+99.0%" not in html
    assert "M0 H20 t3h5es" in html
    assert "M0 H5 t3h5es" in html


def test_year_and_regime_slices_are_primary_cell_only():
    html = report.render(
        {
            "m0h20": _grid(2.0),
            "m0h5": _grid(1.0),
        }
    )
    assert "主格 top3 × h5 分风格" in html
    assert "主格 top3 × h5 分年" in html
    assert "不再叉乘 k×h" in html
    assert "<h3>h=2</h3>" not in html
    assert "<h3>h=3</h3>" not in html
    assert "<h3>h=4</h3>" not in html
    assert "<th>k</th>" not in html
    assert "每个 h 一张表" not in html
    assert "行是臂×k" not in html
