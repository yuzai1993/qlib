from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import run_pred_backtest  # noqa: E402


def test_load_configured_pred_label_fetches_only_configured_raw_label():
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-07-30"), "SH600000"),
            (pd.Timestamp("2026-07-31"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    pred = pd.DataFrame({"score": [0.2, 0.3]}, index=index)
    label = pd.DataFrame(
        {"Ref($close, -41)/Ref($close, -1)-1": [0.01, 0.02]}, index=index
    )
    seen = {}

    def feature_loader(instruments, fields, start_time, end_time, freq):
        seen.update(
            instruments=instruments,
            fields=fields,
            start_time=start_time,
            end_time=end_time,
            freq=freq,
        )
        return label

    cfg = {
        "model": {"class": "must-not-be-instantiated"},
        "data": {
            "instruments": "csi1000",
            "handler": {
                "class": "Handler",
                "module_path": "example.handler",
                "label": [
                    ["Ref($close, -41)/Ref($close, -1)-1"],
                    ["LABEL0"],
                ],
            },
        },
        "segments": {
            "train": ["2016-01-02", "2020-01-10"],
            "valid": ["2020-01-13", "2021-07-15"],
            "test": ["2020-01-13", "2026-07-31"],
        },
        "dataset": {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {},
        },
    }

    pred_label = run_pred_backtest.load_configured_pred_label(
        cfg,
        pred,
        instrument_resolver=lambda market: ["SH600000"],
        feature_loader=feature_loader,
    )

    assert seen == {
        "instruments": ["SH600000"],
        "fields": ["Ref($close, -41)/Ref($close, -1)-1"],
        "start_time": pd.Timestamp("2026-07-30"),
        "end_time": pd.Timestamp("2026-07-31"),
        "freq": "day",
    }
    assert pred_label.columns.tolist() == ["label", "score"]
    assert pred_label["label"].tolist() == [0.01, 0.02]
    assert pred_label["score"].tolist() == [0.2, 0.3]


def test_load_configured_pred_label_rejects_missing_raw_label():
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-07-31"), "SH600000")],
        names=["datetime", "instrument"],
    )
    pred = pd.DataFrame({"score": [0.3]}, index=index)
    cfg = {
        "model": {"class": "unused"},
        "data": {
            "instruments": "csi1000",
            "handler": {
                "class": "Handler",
                "module_path": "example.handler",
                "label": [["Ref($close, -2)/Ref($close, -1)-1"], ["LABEL0"]],
            },
        },
        "segments": {
            "train": ["2016-01-02", "2020-01-10"],
            "valid": ["2020-01-13", "2021-07-15"],
            "test": ["2020-01-13", "2026-07-31"],
        },
    }

    try:
        run_pred_backtest.load_configured_pred_label(
            cfg,
            pred,
            instrument_resolver=lambda market: ["SH600000"],
            feature_loader=lambda instruments, fields, **kwargs: None,
        )
    except ValueError as exc:
        assert "label" in str(exc).lower()
    else:
        raise AssertionError("missing label must be rejected")
