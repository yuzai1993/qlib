import pandas as pd
import pytest

from backtest.scripts.research_layout import resolve_research_path

from backtest.scripts.run_bt_v5_cutover_diagnostics import (
    combine_frozen_scores,
    validate_frozen_config,
)


def scores(dates, values):
    return pd.DataFrame(
        {"score": values},
        index=pd.MultiIndex.from_arrays(
            [pd.to_datetime(dates), ["SH600000"] * len(dates)],
            names=["datetime", "instrument"],
        ),
    )


def test_frozen_history_wins_and_forward_cannot_replace_selection_window():
    old = scores(["2026-07-30", "2026-07-31", "2026-08-03"], [1, 2, 999])
    new = scores(["2026-07-31", "2026-08-03"], [-99, 3])
    result = combine_frozen_scores(old, new)
    assert result.score.tolist() == [1, 2, 3]
    assert not result.index.has_duplicates


def test_missing_boundary_signal_is_rejected():
    with pytest.raises(ValueError, match="2026-07-31"):
        combine_frozen_scores(scores(["2026-07-30"], [1]), scores(["2026-08-03"], [2]))


def test_diagnostic_rejects_tuned_strategy_before_running():
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load(
        resolve_research_path("backtest/configs/alla_v2_top5d1f100_parity.yaml", root).read_text()
    )
    validate_frozen_config(config, risk_degree=1.0)
    config["strategy"]["kwargs"]["force_sell_rank"] = 50
    with pytest.raises(ValueError, match="frozen"):
        validate_frozen_config(config, risk_degree=1.0)


def test_explicit_risk_contract_preserves_legacy_replay():
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    original = resolve_research_path("backtest/experiments/bt_v5_cutover_20261029/config.yaml", root)
    before = original.read_bytes()
    legacy = yaml.safe_load(before)
    validate_frozen_config(legacy, risk_degree=0.9)
    with pytest.raises(ValueError, match="frozen"):
        validate_frozen_config(legacy, risk_degree=1.0)
    with pytest.raises(ValueError, match="risk"):
        validate_frozen_config(legacy, risk_degree=0.8)
    assert original.read_bytes() == before


def test_diagnostic_identity_requires_recorded_risk_and_uses_separate_paths():
    from backtest.scripts.register_bt_v5_cutover_diagnostics import diagnostic_identity

    old = diagnostic_identity({"risk_degree": 0.9})
    new = diagnostic_identity({"risk_degree": 1.0})
    assert old["exp_id"] == "diag/bt-v5-300k-current-cost-20261029"
    assert new["exp_id"] == "diag/bt-v5-300k-risk1-current-cost-20261029"
    for key in ("destination", "eval_output", "detail_report"):
        assert old[key] != new[key]
    with pytest.raises(ValueError, match="risk_degree"):
        diagnostic_identity({})


def test_cached_forward_and_benchmark_provenance_rejects_changed_inputs(tmp_path):
    import hashlib
    import json
    from backtest.scripts.run_bt_v5_cutover_diagnostics import load_frozen_reuse

    forward = scores(["2026-07-31", "2026-08-03"], [1, 2])
    forward.to_pickle(tmp_path / "forward_pred.pkl")
    pd.DataFrame({"bench": [0.02]}, index=pd.to_datetime(["2026-08-03"])).to_csv(
        tmp_path / "portfolio_daily.csv"
    )
    pd.DataFrame(
        {"bench": [0.01, 0.02]}, index=pd.to_datetime(["2019-01-02", "2026-08-03"])
    ).to_csv(tmp_path / "bench_ew_all.csv")
    summary = {
        "status": "completed",
        "requested_end": "2026-08-03",
        "source_pred_sha256": "history",
        "st_daily_sha256": "st",
        "model_manifest_sha256": "models",
        "forward_pred_sha256": hashlib.sha256(
            (tmp_path / "forward_pred.pkl").read_bytes()
        ).hexdigest(),
        "report_sha256": hashlib.sha256(
            (tmp_path / "portfolio_daily.csv").read_bytes()
        ).hexdigest(),
    }
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    actual, bench = load_frozen_reuse(tmp_path, summary)
    pd.testing.assert_frame_equal(actual, forward)
    assert bench.loc["2026-08-03"] == 0.02
    changed = {**summary, "st_daily_sha256": "changed"}
    with pytest.raises(ValueError, match="provenance"):
        load_frozen_reuse(tmp_path, changed)
    (tmp_path / "bench_ew_all.csv").write_text("datetime,bench\n2026-08-03,0.5\n")
    with pytest.raises(ValueError, match="benchmark"):
        load_frozen_reuse(tmp_path, summary)


def test_failed_attempt_registration_preserves_existing_rows(tmp_path, monkeypatch):
    import json
    from backtest.scripts import register_bt_v5_cutover_diagnostics as register

    monkeypatch.setattr(register, "ROOT", tmp_path)
    reports = []
    monkeypatch.setattr(register, "write_all", lambda: reports.append(True) or [])
    result = tmp_path / "backtest/result/failed_run"
    result.mkdir(parents=True)
    summary = {
        "status": "failed",
        "risk_degree": 1.0,
        "error": "cache guard failed",
        "hypothesis": "fixed risk1 replay",
        "requested_end": "2026-10-28",
    }
    (result / "summary.json").write_text(json.dumps(summary))
    registry = tmp_path / "backtest/experiments/registry.jsonl"
    registry.parent.mkdir(parents=True)
    old = '{"exp_id":"original","metrics":{"unchanged":1}}\n'
    registry.write_text(old)
    register.register_failure(result, summary, attempt=1)
    assert registry.read_text().startswith(old)
    record = json.loads(registry.read_text().splitlines()[-1])
    assert record["exp_id"] == "diag/bt-v5-300k-risk1-current-cost-20261029-failed-1"
    assert record["state"] == "failed" and record["metrics"] == {}
    assert record["detail_report"] == (
        "backtest/experiments/phase_m_v1_bt_v5_cutover_risk1_failed_1.html"
    )
    assert (
        json.loads((tmp_path / record["eval_output"]).read_text())["ensemble"][
            "full_period"
        ]
        == {}
    )
    assert reports == [True]


@pytest.mark.parametrize(
    "directory_name,risk",
    [
        ("bt_v5_cutover_20261029", 0.9),
        ("bt_v5_cutover_risk1_20261029", 1.0),
    ],
)
def test_registered_metrics_recompute_from_durable_daily_returns(directory_name, risk):
    import hashlib
    import json
    from pathlib import Path
    from backtest.scripts.strategy_stability_metrics import summarize_period
    import yaml

    root = Path(__file__).resolve().parents[2]
    directory = root / "backtest/experiments" / directory_name
    summary = json.loads(resolve_research_path(directory / "summary.json", root).read_text())
    cfg = yaml.safe_load(resolve_research_path(directory / "config.yaml", root).read_text())
    assert cfg["strategy"]["kwargs"]["risk_degree"] == risk
    if risk == 1.0:
        assert summary["risk_degree"] == risk
        assert summary["reused_frozen_result"].endswith("bt_v5_cutover_300k_20261029")
        assert (
            summary["source_pred_sha256"]
            == "3d6f768930a4b9ac63622ee039ded617b68addfac56f44d77002a0188572021e"
        )
    daily = resolve_research_path(directory / "portfolio_daily.csv", root)
    assert hashlib.sha256(daily.read_bytes()).hexdigest() == summary["report_sha256"]
    report = pd.read_csv(daily, index_col=0, parse_dates=True)
    for frame, expected in (
        (report.loc[:"2026-07-31"], summary["historical_current_cost"]["full_period"]),
        (report.loc["2026-08-03":], summary["forward_carried_positions"]),
    ):
        actual = summarize_period(frame)
        for key, value in expected.items():
            assert (
                actual[key] == pytest.approx(value, abs=1e-10)
                if value is not None
                else actual[key] is None
            )
    assert len(report.loc["2026-08-03":]) == 57
    assert len(report) == 1511
