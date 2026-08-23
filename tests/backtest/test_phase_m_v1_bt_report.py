from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import build_phase_m_v1_bt_report as report  # noqa: E402


def _ens(ann: float, sharpe: float, *, year_ann=0.1, d_ann=0.2, session="backtest/result/dummy"):
    return {
        "session_dir": session,
        "full_period": {
            "annualized_return": ann,
            "sharpe_ratio": sharpe,
            "alpha": 0.12,
            "beta": 0.95,
            "max_drawdown": -0.3,
            "calmar_ratio": 1.0,
            "annualized_volatility": 0.28,
            "annualized_one_way_turnover": 49.0,
            "cumulative_return": 1.5,
            "benchmark_annualized_return": 0.1,
        },
        "years": {
            "2025": {
                "annualized_return": year_ann,
                "sharpe_ratio": 1.1,
                "alpha": 0.08,
                "beta": 0.9,
                "max_drawdown": -0.15,
                "annualized_one_way_turnover": 48.0,
            }
        },
        "regimes": {
            "D": {"annualized_return": d_ann, "sharpe_ratio": 0.8, "alpha": 0.05, "beta": 1.0, "max_drawdown": -0.2},
            "F": {"annualized_return": 0.05, "sharpe_ratio": 0.3, "alpha": 0.01, "beta": 1.1, "max_drawdown": -0.25},
            "T": {"annualized_return": 0.15, "sharpe_ratio": 0.6, "alpha": 0.04, "beta": 0.85, "max_drawdown": -0.18},
        },
        "figures": {"report_graph": ["nav.html"]},
    }


def test_hub_uses_ensemble_not_seed_mean():
    baselines = [
        {
            "bt_version": "v1",
            "display_name": "M0 H20 top5d1",
            "doc": {"ensemble": _ens(0.28, 0.95, year_ann=0.42, session="backtest/result/ens_v1")},
        }
    ]
    html = report.render_hub(baselines, experiments=[])
    assert "五种子均值信号" in html
    assert "+28.0%" in html
    assert "0.95" in html
    assert "run_01/report.html" in html
    assert "种子算术平均" not in html
    assert "BT v2" in html or "当前对照锚点" in html


def test_hub_marks_current_bt_version():
    baselines = [
        {
            "bt_version": "v1",
            "display_name": "M0 H20 top5d1",
            "current": False,
            "doc": {"ensemble": _ens(0.24, 0.84, year_ann=0.12, session="backtest/result/ens_v1")},
        },
        {
            "bt_version": "v2",
            "display_name": "M0 H20 ES top5d1",
            "current": True,
            "doc": {"ensemble": _ens(0.28, 0.95, year_ann=0.19, session="backtest/result/ens_v2")},
        },
    ]
    html = report.render_hub(baselines, experiments=[])
    assert "v2 · 当前" in html
    assert "class='current'" in html


def test_hub_header_anchor_follows_current_baseline():
    # 页头曾把锚点写死成 BT v2，晋升 v3 后没跟上；锚点与策略参数必须由 current 派生
    baselines = [
        {
            "bt_version": "v2",
            "display_name": "M0 H20 ES top5d1",
            "current": False,
            "strategy": "TopkDropout top5 n_drop=1 hold_thresh=1",
            "doc": {"ensemble": _ens(0.24, 0.84)},
        },
        {
            "bt_version": "v3",
            "display_name": "M0 H20 ES hold5 + 掉出前100必卖",
            "current": True,
            "strategy": "TopkDropout top5 n_drop=1 hold_thresh=5 force_sell_rank=100",
            "doc": {"ensemble": _ens(0.30, 1.06)},
        },
    ]

    html = report.render_hub(baselines, experiments=[])

    assert "当前对照锚点是 <b>BT v3 · M0 H20 ES hold5 + 掉出前100必卖</b>" in html
    assert "force_sell_rank=100" in html
    assert "BT v2 · " not in html


def test_hub_header_shows_v4_ladder_when_current():
    baselines = [
        {
            "bt_version": "v3",
            "display_name": "M0 H20 ES hold5 + 掉出前100必卖",
            "current": False,
            "strategy": "TopkDropout top5 n_drop=1 hold_thresh=5 force_sell_rank=100",
            "doc": {"ensemble": _ens(0.30, 1.06)},
        },
        {
            "bt_version": "v4",
            "display_name": "v4 RankIC ES 真阶梯 k3×h5",
            "current": True,
            "strategy": "CohortLadder topk=3 horizon=5",
            "doc": {"ensemble": _ens(0.26, 1.04)},
        },
    ]

    html = report.render_hub(baselines, experiments=[])

    assert "当前对照锚点是 <b>BT v4 · v4 RankIC ES 真阶梯 k3×h5</b>" in html
    assert "CohortLadder topk=3 horizon=5" in html
    assert "v4 · 当前" in html
    assert "BT v3 · " not in html


def test_hub_header_tolerates_missing_current_baseline():
    html = report.render_hub(
        [{"bt_version": "v1", "display_name": "x", "doc": {"ensemble": _ens(0.1, 0.5)}}],
        experiments=[],
    )

    assert "未设定" in html


def test_hub_year_and_regime_tables_are_metric_by_version():
    baselines = [
        {
            "bt_version": "v1",
            "display_name": "M0 H20 top5d1",
            "doc": {"ensemble": _ens(0.28, 0.95, year_ann=0.42, d_ann=0.33)},
        }
    ]
    html = report.render_hub(baselines, experiments=[])
    assert "分年 · 累乘年化" in html
    assert "分风格 · 累乘年化" in html
    assert "+42.0%" in html
    assert "+33.0%" in html
    assert ">D<" in html and ">F<" in html and ">T<" in html


def test_write_all_skips_experiments_without_detail_report(tmp_path, monkeypatch):
    """registry 允许 detail_report 为空（只上总表、不出详情页），不能因此崩。"""
    baseline = {
        "bt_version": "v2",
        "display_name": "M0 H20 ES top5d1",
        "doc": {"ensemble": _ens(0.23, 0.86)},
    }
    experiments = [
        {
            "exp_id": "with-page",
            "display_name": "有详情页",
            "baseline_ref": "v2",
            "detail_report": "out/with_page.html",
            "doc": {"ensemble": _ens(0.25, 0.99)},
        },
        {
            "exp_id": "no-page",
            "display_name": "无详情页",
            "baseline_ref": "v2",
            "detail_report": None,
            "doc": {"ensemble": _ens(0.22, 0.96)},
        },
    ]
    monkeypatch.setattr(report, "EXP_ROOT", tmp_path)
    monkeypatch.setattr(report, "HUB_OUT", tmp_path / "hub.html")
    monkeypatch.setattr(report, "load_registry", lambda: [])
    monkeypatch.setattr(
        report, "catalog_from_registry", lambda _rec: ([baseline], experiments)
    )
    (tmp_path / "out").mkdir()

    written = report.write_all()

    assert tmp_path / "out" / "with_page.html" in written
    assert not any("no-page" in str(path) for path in written)


def test_experiment_report_compares_against_baseline_not_history():
    baseline = {
        "bt_version": "v1",
        "display_name": "M0 H20 top5d1",
        "doc": {"ensemble": _ens(0.28, 0.95, year_ann=0.20)},
    }
    experiment = {
        "exp_id": "regime-adapt/m0h20es-all-top5d1-bt",
        "display_name": "M0 H20 ES",
        "doc": {"ensemble": _ens(0.31, 1.05, year_ann=0.25, session="backtest/result/ens_es")},
    }
    html = report.render_experiment(experiment, baseline)
    assert "实验组对比基准组" in html
    assert "M0 H20 ES" in html
    assert "M0 H20 top5d1" in html
    assert "+31.0%" in html
    assert "+28.0%" in html
    assert "历史baseline" not in html.replace(" ", "").replace("基线", "baseline")
    assert "分年 · 累乘年化" in html
    assert "分风格 · 累乘年化" in html
    assert "主年化是扣费净值累乘后再折年" in html
