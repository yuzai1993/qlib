from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import build_phase_m_v1_report as report  # noqa: E402


def _cell(ann: float, sharpe: float) -> dict:
    return {
        "net_ann": ann,
        "net_ann_vol": 0.33,
        "net_sharpe": sharpe,
        "ann": ann + 0.04,
        "net_ann_excess": ann - 0.10,
        "ann_excess": ann - 0.06,
        "turnover": 0.18,
        "n_days": 1454,
    }


def _eval_doc(*, ann=0.207, sharpe=0.61, year_ann=0.318, d_ann=0.162, rank_ic=0.0878):
    years = {
        y: {"3": {"5": _cell(year_ann if y == "2021" else 0.05, 0.40)}}
        for y in report.YEARS
    }
    official = {
        "head": {"3": {"5": _cell(ann, sharpe)}},
        "h5": {"rank_ic_mean": rank_ic},
        "mean_h": {"rank_ic_mean": rank_ic},
        "head_years": years,
        "head_regimes": {
            "D": {"3": {"5": _cell(d_ann, 0.59)}},
            "F": {"3": {"5": _cell(0.34, 0.56)}},
            "T": {"3": {"5": _cell(0.29, 0.80)}},
        },
    }
    return {
        "filters": {"st_filter": "daily", "min_listing_days": 60, "min_amount": 10_000_000},
        "pools": {
            "all": {
                "seed_mean": {
                    "head": {"3": {"5": _cell(0.99, 9.99)}},
                    "h5": {"rank_ic_mean": 0.1111},
                    "head_years": years,
                    "head_regimes": official["head_regimes"],
                },
                "ensemble": official,
            }
        },
    }


def _baseline(version: str, name: str, **kwargs):
    return {
        "version": version,
        "name": name,
        "exp_id": f"regime-adapt/{version}",
        "current": version == "v2",
        "doc": _eval_doc(**kwargs),
        "detail_report": "backtest/experiments/demo.html",
    }


def test_hub_lists_baseline_versions_in_first_block():
    html = report.render_hub(
        [
            _baseline("v1", "M0 H20", ann=0.207, sharpe=0.61),
            _baseline("v2", "M0 H20 ES", ann=0.225, sharpe=0.65),
        ],
        experiments=[],
    )
    assert "1. 历史 baseline" in html
    assert ">v1<" in html and ">v2<" in html
    assert "M0 H20 ES" in html
    assert "+20.7%" in html
    assert "+22.5%" in html
    assert "0.65" in html
    assert "每个方向" not in html


def test_hub_year_and_regime_tables_are_metric_by_version():
    html = report.render_hub(
        [_baseline("v1", "M0 H20", year_ann=0.318, d_ann=0.162)],
        experiments=[],
    )
    assert "2. 历史 baseline 分年" in html
    assert "3. 历史 baseline 分风格" in html
    assert "分年 · 扣费净年化" in html
    assert "扣费净超额" not in html
    assert "分风格 · 扣费夏普" in html
    assert "+31.8%" in html
    assert "+16.2%" in html
    for year in report.YEARS:
        assert f">{year}<" in html
    assert ">D<" in html and ">F<" in html and ">T<" in html


def test_hub_prefers_ensemble_over_seed_mean():
    html = report.render_hub(
        [_baseline("v2", "M0 H20 ES", ann=0.111, sharpe=0.42)],
        experiments=[],
    )
    assert "+11.1%" in html
    assert "0.42" in html
    assert "+99.0%" not in html
    assert "9.99" not in html


def test_hub_shows_global_rank_ic_from_ensemble_h5():
    html = report.render_hub(
        [_baseline("v4", "M0 H20 RankIC ES", ann=0.306, sharpe=0.53, rank_ic=0.0878)],
        experiments=[],
    )
    assert "全局 RankIC" in html
    assert "0.0878" in html
    assert "0.1111" not in html


def test_collect_appends_v3_from_registry_and_marks_it_current():
    rows = [
        {
            "exp_id": "regime-adapt/m0-h20-label-v4",
            "phase_m_protocol": "v1",
            "baseline_version": "v1",
            "eval_output": "",
        },
        {
            "exp_id": "regime-adapt/m0-h20-t5h5-es-v1",
            "phase_m_protocol": "v1",
            "baseline_version": "v2",
            "eval_output": "",
        },
        {
            "exp_id": "regime-adapt/m0-h5-t3h5es-v1",
            "phase_m_protocol": "v1",
            "baseline_version": "v3",
            "display_name": "M0 H5 t3h5es",
            "eval_output": "",
        },
        {
            "exp_id": "regime-adapt/m0-h1-t3h5es-v1",
            "phase_m_protocol": "v1",
            "eval_output": "",
        },
        {
            "exp_id": "regime-adapt/m0-h20-rankices-v1",
            "phase_m_protocol": "v1",
            "baseline_version": "v4",
            "display_name": "M0 H20 RankIC ES",
            "eval_output": "",
        },
    ]
    baselines, experiments = report.collect(rows)
    versions = [b["version"] for b in baselines]
    assert versions == ["v1", "v2", "v3", "v4"]
    assert baselines[-1]["current"] is True
    assert baselines[-1]["exp_id"] == "regime-adapt/m0-h20-rankices-v1"
    assert not any(b["current"] for b in baselines[:-1])
    assert [e["exp_id"] for e in experiments] == ["regime-adapt/m0-h1-t3h5es-v1"]


def test_collect_uses_declared_portable_eval_without_original_result(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "EXP_ROOT", tmp_path)
    portable = tmp_path / "backtest/experiments/model_metrics/v4.json"
    portable.parent.mkdir(parents=True)
    portable.write_text(json.dumps(_eval_doc(ann=0.306, rank_ic=0.0878)))
    row = {
        "exp_id": "regime-adapt/m0-h20-rankices-v1",
        "phase_m_protocol": "v1",
        "baseline_version": "v4",
        "eval_output": "backtest/result/removed/v4.json",
        "portable_eval_output": str(portable.relative_to(tmp_path)),
    }

    baselines, _ = report.collect([row])
    assert report.primary_cell(baselines[-1]["doc"])["net_ann"] == 0.306
    assert report.official_rank_ic(baselines[-1]["doc"]) == 0.0878


def test_collect_does_not_fallback_when_declared_portable_eval_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "EXP_ROOT", tmp_path)
    original = tmp_path / "backtest/result/original/v4.json"
    original.parent.mkdir(parents=True)
    original.write_text(json.dumps(_eval_doc(ann=0.999)))
    row = {
        "exp_id": "regime-adapt/m0-h20-rankices-v1",
        "phase_m_protocol": "v1",
        "baseline_version": "v4",
        "eval_output": str(original.relative_to(tmp_path)),
        "portable_eval_output": "backtest/experiments/model_metrics/missing.json",
    }

    for declared in ["backtest/experiments/model_metrics/missing.json", "", None]:
        row["portable_eval_output"] = declared
        baselines, _ = report.collect([row])
        assert baselines[-1]["doc"] is None


def test_explicit_older_current_model_overrides_higher_historical_version():
    v2 = {"exp_id": report.BASELINE_VERSIONS[1]["exp_id"], "phase_m_protocol": "v1",
          "baseline_version": "v2", "current_baseline": True}
    v4 = {"exp_id": "regime-adapt/m0-h20-rankices-v1", "phase_m_protocol": "v1", "baseline_version": "v4"}
    baselines, _ = report.collect([v2, v4])
    assert [b["version"] for b in baselines if b["current"]] == ["v2"]
    assert {b["version"] for b in baselines} == {"v1", "v2", "v4"}


def test_missing_explicit_current_source_remains_missing_without_promoting_v4(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "EXP_ROOT", tmp_path)
    source = tmp_path / "backtest/experiments/v4.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(_eval_doc(ann=.3057)))
    v2 = {"exp_id": report.BASELINE_VERSIONS[1]["exp_id"], "phase_m_protocol": "v1",
          "baseline_version": "v2", "current_baseline": True,
          "portable_eval_output": "backtest/experiments/missing-v2.json"}
    v4 = {"exp_id": "regime-adapt/m0-h20-rankices-v1", "phase_m_protocol": "v1", "baseline_version": "v4",
          "portable_eval_output": "backtest/experiments/v4.json"}
    baselines, _ = report.collect([v2, v4])
    current = [b for b in baselines if b["current"]]
    assert len(current) == 1 and current[0]["version"] == "v2" and current[0]["doc"] is None


def test_historical_receipt_supplies_v1_baseline_without_an_experiment_row(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "EXP_ROOT", tmp_path)
    source = tmp_path / "backtest/experiments/v1.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(_eval_doc(ann=.2933)))
    v1 = {"exp_id": report.BASELINE_VERSIONS[0]["exp_id"], "phase_m_protocol": "v1",
          "baseline_version": "v1", "portable_eval_output": "backtest/experiments/v1.json",
          "detail_report": "backtest/experiments/reports/label-study.html"}
    baselines, experiments = report.collect([], historical_rows=[v1])
    assert report.primary_cell(baselines[0]["doc"])["net_ann"] == .2933
    assert baselines[0]["detail_report"] == v1["detail_report"]
    assert experiments == []


def test_higher_archived_version_does_not_override_active_legacy_current():
    active = {"exp_id": report.BASELINE_VERSIONS[1]["exp_id"], "phase_m_protocol": "v1",
              "baseline_version": "v2"}
    archived = {"exp_id": "regime-adapt/archived-v9", "phase_m_protocol": "v1",
                "baseline_version": "v9", "current_baseline": True}
    baselines, _ = report.collect([active], historical_rows=[archived])
    assert [b["version"] for b in baselines if b["current"]] == ["v2"]
    assert "v9" in {b["version"] for b in baselines}


def test_archived_only_versions_do_not_select_a_current_baseline():
    archived = {"exp_id": "regime-adapt/archived-v9", "phase_m_protocol": "v1",
                "baseline_version": "v9", "current_baseline": True}
    baselines, _ = report.collect([], historical_rows=[archived])
    assert not any(b["current"] for b in baselines)


def test_hub_experiments_are_records_with_detail_links():
    html = report.render_hub(
        [_baseline("v2", "M0 H20 ES")],
        experiments=[
            {
                "display_name": "某个实验",
                "exp_id": "regime-adapt/foo",
                "hypothesis": "只改早停",
                "detail_report": "backtest/experiments/foo_report.html",
                "date": "2026-08-19",
            }
        ],
    )
    assert "4. 历史实验记录" in html
    assert "某个实验" in html
    assert "foo_report.html" in html
    assert "regime-adapt（M0 训练标签期限）" not in html
