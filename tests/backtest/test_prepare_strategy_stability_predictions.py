from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

from generate_phase_s_predictions import prediction_index_sha256  # noqa: E402
from prepare_strategy_stability_predictions import compose_prediction  # noqa: E402


def _write_pred(path: Path, tuples: list[tuple[str, str]], values: list[float]):
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(date), instrument) for date, instrument in tuples],
        names=["datetime", "instrument"],
    )
    frame = pd.DataFrame({"score": values}, index=index).sort_index()
    frame.to_pickle(path)
    return frame


def _entry(path: Path, frame: pd.DataFrame, *, segment: str):
    return {
        "model_ref": "b6-m",
        "pool": "csi1000",
        "segment": segment,
        "path": str(path),
        "prediction_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "coverage": {
            "start": str(frame.index.get_level_values("datetime").min().date()),
            "end": str(frame.index.get_level_values("datetime").max().date()),
            "n_dates": int(frame.index.get_level_values("datetime").nunique()),
            "n_rows": len(frame),
            "index_sha256": prediction_index_sha256(frame.index),
        },
    }


def test_compose_prediction_is_sorted_unique_and_audits_both_sources(tmp_path):
    valid_path = tmp_path / "valid.pkl"
    test_path = tmp_path / "test.pkl"
    valid = _write_pred(valid_path, [("2020-01-13", "A"), ("2021-07-15", "A")], [1.0, 2.0])
    test = _write_pred(test_path, [("2021-07-16", "A"), ("2026-07-31", "A")], [3.0, 4.0])

    full, audit = compose_prediction(
        valid_path,
        test_path,
        _entry(valid_path, valid, segment="valid"),
        _entry(test_path, test, segment="test"),
    )

    assert full.index.is_monotonic_increasing
    assert not full.index.has_duplicates
    assert str(full.index.get_level_values("datetime").min().date()) == "2020-01-13"
    assert str(full.index.get_level_values("datetime").max().date()) == "2026-07-31"
    assert audit["coverage"]["n_rows"] == 4
    assert [source["segment"] for source in audit["sources"]] == ["valid", "test"]


def test_compose_prediction_rejects_source_sha_mismatch(tmp_path):
    valid_path = tmp_path / "valid.pkl"
    test_path = tmp_path / "test.pkl"
    valid = _write_pred(valid_path, [("2020-01-13", "A")], [1.0])
    test = _write_pred(test_path, [("2021-07-16", "A")], [2.0])
    valid_entry = _entry(valid_path, valid, segment="valid")
    valid_entry["prediction_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="SHA"):
        compose_prediction(valid_path, test_path, valid_entry, _entry(test_path, test, segment="test"))


def test_compose_prediction_rejects_overlap_and_identity_mismatch(tmp_path):
    valid_path = tmp_path / "valid.pkl"
    test_path = tmp_path / "test.pkl"
    valid = _write_pred(valid_path, [("2020-01-13", "A")], [1.0])
    test = _write_pred(test_path, [("2020-01-13", "A")], [2.0])
    valid_entry = _entry(valid_path, valid, segment="valid")
    test_entry = _entry(test_path, test, segment="test")

    with pytest.raises(ValueError, match="overlap"):
        compose_prediction(valid_path, test_path, valid_entry, test_entry)

    test_entry["pool"] = "csi300"
    with pytest.raises(ValueError, match="identity"):
        compose_prediction(valid_path, test_path, valid_entry, test_entry)
