from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_phase_s_predictions as prediction  # noqa: E402


def test_normalize_prediction_orders_and_names_index_levels():
    index = pd.MultiIndex.from_tuples(
        [("SH600000", pd.Timestamp("2020-01-13"))],
        names=["instrument", "datetime"],
    )

    score = prediction.normalize_prediction(pd.DataFrame({"pred": [1.5]}, index=index))

    assert score.name == "score"
    assert score.index.names == ["datetime", "instrument"]
    assert score.index[0] == (pd.Timestamp("2020-01-13"), "SH600000")


def test_prediction_validation_rejects_duplicate_index():
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2020-01-13"), "SH600000")] * 2,
        names=["datetime", "instrument"],
    )

    with pytest.raises(ValueError, match="duplicate"):
        prediction.validate_prediction_index(
            pd.Series([1.0, 2.0], index=index),
            pd.DatetimeIndex(["2020-01-13"]),
        )


def test_prediction_validation_rejects_missing_trading_date():
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2020-01-13"), "SH600000")],
        names=["datetime", "instrument"],
    )

    with pytest.raises(ValueError, match="missing"):
        prediction.validate_prediction_index(
            pd.Series([1.0], index=index),
            pd.DatetimeIndex(["2020-01-13", "2020-01-14"]),
        )


def test_prediction_validation_returns_literal_coverage_summary():
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2020-01-13"), "SH600000"),
            (pd.Timestamp("2020-01-14"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )

    coverage = prediction.validate_prediction_index(
        pd.Series([1.0, 2.0], index=index),
        pd.DatetimeIndex(["2020-01-13", "2020-01-14"]),
    )

    assert coverage == {
        "start": "2020-01-13",
        "end": "2020-01-14",
        "n_dates": 2,
        "n_rows": 2,
    }


def test_manifest_entry_records_model_config_prediction_sha_and_data_version(tmp_path):
    pred = tmp_path / "pred.pkl"
    pred.write_bytes(b"prediction")
    config = tmp_path / "model.yaml"
    config.write_bytes(b"config")
    frozen = SimpleNamespace(
        model_ref="b1-m",
        manifest_path=tmp_path / "manifest.json",
        model_path=tmp_path / "trained_model",
        model_sha256="model-sha",
        source_config=config,
    )

    entry = prediction.build_prediction_manifest_entry(
        pred,
        frozen,
        pool="csi1000",
        segment="valid",
        coverage={"start": "2020-01-13", "end": "2021-07-15", "n_dates": 365, "n_rows": 1000},
        data_version="2026-07-31",
    )

    assert entry["model_ref"] == "b1-m"
    assert entry["model_sha256"] == "model-sha"
    assert entry["config_sha256"] == hashlib.sha256(b"config").hexdigest()
    assert entry["prediction_sha256"] == hashlib.sha256(b"prediction").hexdigest()
    assert entry["data_version"] == "2026-07-31"
    assert entry["coverage"]["end"] == "2021-07-15"
