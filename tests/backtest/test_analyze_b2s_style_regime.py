from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze_b2s_style_regime as regime  # noqa: E402


def _report(returns, bench, cost) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-13", periods=len(returns), name="datetime")
    return pd.DataFrame(
        {"return": returns, "bench": bench, "cost": cost}, index=index
    )


def test_daily_excess_with_cost_matches_qlib_definition():
    report = _report([0.01, 0.02, -0.03], [0.004, 0.005, -0.01], [0.001, 0.002, 0.001])

    excess = regime.daily_excess_with_cost(report)

    np.testing.assert_allclose(excess.values, [0.005, 0.013, -0.021])
    assert excess.index.equals(report.index)


def test_daily_excess_with_cost_requires_all_columns():
    report = _report([0.01], [0.004], [0.001]).drop(columns=["cost"])

    with pytest.raises(ValueError, match="cost"):
        regime.daily_excess_with_cost(report)


def test_select_bad_days_takes_worst_quantile_only():
    excess = pd.Series(
        [0.01, -0.05, 0.02, -0.10, 0.03, -0.01, 0.04, -0.02, 0.05, -0.03],
        index=pd.bdate_range("2020-01-13", periods=10, name="datetime"),
    )

    bad = regime.select_bad_days(excess, quantile=0.2)

    # 最差 20% = 两天：-0.10 与 -0.05
    assert list(bad) == [excess.index[3], excess.index[1]]


def test_cluster_events_merges_within_gap_and_splits_beyond_it():
    calendar = pd.bdate_range("2020-01-13", periods=40, name="datetime")
    # 位置 0,1,2 连续；位置 7 与 2 相隔 5 个交易日 -> 同簇；位置 20 远离 -> 新簇
    bad = pd.DatetimeIndex([calendar[0], calendar[1], calendar[2], calendar[7], calendar[20]])

    clusters = regime.cluster_events(bad, calendar, max_gap=5)

    assert len(clusters) == 2
    assert clusters[0]["start"] == str(calendar[0].date())
    assert clusters[0]["end"] == str(calendar[7].date())
    assert clusters[0]["n_bad_days"] == 4
    assert clusters[1]["n_bad_days"] == 1


def test_cluster_events_splits_when_gap_exceeds_threshold():
    calendar = pd.bdate_range("2020-01-13", periods=40, name="datetime")
    bad = pd.DatetimeIndex([calendar[0], calendar[6]])

    # 位置差为 6：max_gap=6 恰好合并，max_gap=5 必须拆开
    assert len(regime.cluster_events(bad, calendar, max_gap=6)) == 1
    assert len(regime.cluster_events(bad, calendar, max_gap=5)) == 2


def test_cluster_events_handles_empty_selection():
    calendar = pd.bdate_range("2020-01-13", periods=10, name="datetime")

    assert regime.cluster_events(pd.DatetimeIndex([]), calendar, max_gap=5) == []


def test_summarize_clusters_reports_loss_concentration():
    calendar = pd.bdate_range("2020-01-13", periods=30, name="datetime")
    excess = pd.Series(0.001, index=calendar)
    excess.iloc[[0, 1]] = -0.05
    excess.iloc[20] = -0.02
    bad = pd.DatetimeIndex([calendar[0], calendar[1], calendar[20]])
    clusters = regime.cluster_events(bad, calendar, max_gap=5)

    summary = regime.summarize_clusters(excess, clusters)

    assert summary["n_clusters"] == 2
    assert summary["n_bad_days"] == 3
    # 最大簇累计 -0.10，总坏日损失 -0.12
    assert summary["worst_cluster_excess_sum"] == pytest.approx(-0.10)
    assert summary["worst_cluster_share_of_bad_day_loss"] == pytest.approx(0.10 / 0.12)
    assert summary["clusters"][0]["excess_sum"] == pytest.approx(-0.10)


def test_effective_sample_warning_triggers_on_few_clusters():
    assert regime.effective_sample_verdict(4) == "insufficient"
    assert regime.effective_sample_verdict(9) == "insufficient"
    assert regime.effective_sample_verdict(10) == "marginal"
    assert regime.effective_sample_verdict(30) == "usable"


def _payload() -> dict:
    return {
        "diagnostic": "b2s_style_regime",
        "pool": "csi1000",
        "prediction": "backtest/experiments/x/csi1000_full.pkl",
        "prediction_sha256": "c" * 64,
        "segments": {
            "valid": {
                "segment": ["2020-01-13", "2021-07-15"],
                "cluster_summary": {
                    "n_clusters": 20,
                    "n_bad_days": 36,
                    "worst_cluster_share_of_bad_day_loss": 0.194,
                    "effective_sample_verdict": "marginal",
                },
                "style": {
                    "rank_ic": {
                        "raw": {"mean": 0.0494, "ir": 0.4305},
                        "ex_size": {"mean": 0.0424, "ir": 0.4682},
                    }
                },
            },
            "test": {
                "segment": ["2021-07-16", "2026-07-31"],
                "cluster_summary": {
                    "n_clusters": 60,
                    "n_bad_days": 122,
                    "worst_cluster_share_of_bad_day_loss": 0.136,
                    "effective_sample_verdict": "usable",
                },
                "style": {"rank_ic": {"raw": {"mean": 0.0509, "ir": 0.3276}}},
            },
        },
    }


def test_registry_row_is_a_non_selecting_diagnostic(tmp_path: Path):
    output = tmp_path / "diag.json"
    output.write_text("{}", encoding="utf-8")

    row = regime.build_registry_row(_payload(), output, result_dir="backtest/result/x")

    assert row["direction"] == "signal-style-diagnostic"
    assert row["phase"] == "S"
    assert row["conclusion"] == "diagnostic_no_selection"
    assert row["state"] == "complete"
    assert row["baseline_ref"] == "B2-S v1.0"
    assert row["frozen_model_ref"] == "B6 v1.0"
    # 诊断不得占用清理保留额度，也不得给出可比的策略指标
    assert row["cleanup_retention_eligible"] is False
    assert row["metrics_summary"] == {}
    assert row["diagnostic_result_sha256"] == regime.sha256_of(output)
    assert row["diagnostic_findings"]["valid"]["n_clusters"] == 20
    assert row["diagnostic_findings"]["valid"]["rank_icir_ex_size"] == 0.4682


def test_registry_row_records_selection_constraint(tmp_path: Path):
    output = tmp_path / "diag.json"
    output.write_text("{}", encoding="utf-8")

    row = regime.build_registry_row(_payload(), output, result_dir=None)

    assert "valid" in row["selection_constraint"]
    assert row["result_dirs"] == []


def test_upsert_registry_preserves_unrelated_lines_verbatim(tmp_path: Path):
    registry = tmp_path / "registry.jsonl"
    original = '{"exp_id":"existing/a","value":1}\n'
    registry.write_text(original, encoding="utf-8")

    regime.upsert_registry(registry, {"exp_id": "new/b", "value": 2})

    lines = registry.read_text(encoding="utf-8").splitlines(keepends=True)
    assert lines[0] == original
    assert lines[1] == '{"exp_id": "new/b", "value": 2}\n'
