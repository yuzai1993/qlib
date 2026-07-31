from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_backtest  # noqa: E402
import run_pred_backtest  # noqa: E402
from eval_protocol import yearly_ir  # noqa: E402


def test_extract_metrics_adds_turnover_and_cumulative_cost_diagnostics():
    report = pd.DataFrame(
        {
            "turnover": [0.2, 0.4],
            "total_cost": [10.0, 25.0],
        },
        index=pd.to_datetime(["2020-01-13", "2020-01-14"]),
    )

    metrics = run_backtest.extract_metrics(pd.DataFrame(), report)

    assert metrics["annualized_one_way_turnover"] == pytest.approx(37.5)
    assert metrics["cumulative_trade_cost"] == pytest.approx(25.0)


def test_skip_pred_copy_keeps_only_immutable_source_reference(tmp_path):
    source = tmp_path / "source.pkl"
    source.write_bytes(b"frozen-prediction")
    session = tmp_path / "session"
    session.mkdir()

    artifact = run_pred_backtest.prepare_pred_artifact(
        source,
        pd.DataFrame({"score": [1.0]}),
        session,
        copy_name="external_pred.pkl",
        skip_copy=True,
    )

    assert artifact == {
        "source_pred": str(source.resolve()),
        "source_pred_sha256": hashlib.sha256(b"frozen-prediction").hexdigest(),
        "saved_pred": None,
    }
    assert list(session.iterdir()) == []


def test_parse_args_accepts_skip_pred_copy():
    args = run_pred_backtest.parse_args(
        ["--pred", "pred.pkl", "--config", "candidate.yaml", "--skip-pred-copy"]
    )

    assert args.skip_pred_copy is True


def test_load_pred_source_returns_resolved_path_used_by_artifact_metadata(tmp_path):
    source = tmp_path / "pred.pkl"
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2020-01-13"), "SH600000")],
        names=["datetime", "instrument"],
    )
    pd.DataFrame({"score": [1.0]}, index=index).to_pickle(source)

    resolved, frame = run_pred_backtest.load_pred_source(source)

    assert resolved == source.resolve()
    assert list(frame.columns) == ["score"]
    assert frame.index.names == ["datetime", "instrument"]


def test_yearly_ir_subtracts_daily_cost(tmp_path):
    report = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]),
            "return": [0.02, 0.01, -0.01],
            "bench": [0.0, 0.0, 0.0],
            "cost": [0.01, 0.01, 0.01],
        }
    )
    path = tmp_path / "report.csv"
    report.to_csv(path, index=False)

    actual = yearly_ir(path).loc[2025]

    from qlib.contrib.evaluate import risk_analysis

    expected = risk_analysis(pd.Series([0.01, 0.0, -0.02]), freq="day").loc[
        "information_ratio", "risk"
    ]
    assert actual == pytest.approx(expected)
