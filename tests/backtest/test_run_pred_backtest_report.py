from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_prepare_signal_and_port_cfg_resolves_ew_bench_and_filters(tmp_path, monkeypatch):
    csv = tmp_path / "bench.csv"
    csv.write_text("datetime,ret\n2020-08-03,0.01\n2020-08-04,-0.02\n", encoding="utf-8")
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2020-08-03"), "SH600000"),
            (pd.Timestamp("2020-08-03"), "SZ000001"),
        ],
        names=["datetime", "instrument"],
    )
    pred = pd.DataFrame({"score": [1.0, 2.0]}, index=index)
    filtered = pred.copy()
    filtered.iloc[1, 0] = float("nan")
    stats = SimpleNamespace(as_dict=lambda: {"n_keep": 1, "n_raw": 2})
    seen = {}

    def fake_filter(frame, spec):
        seen["pool"] = spec.pool
        seen["min_amount"] = spec.min_amount
        return filtered, stats

    monkeypatch.setattr(run_pred_backtest, "filter_pred", fake_filter)
    cfg = {
        "data": {
            "benchmark": {"equal_weight_csv": str(csv)},
            "instruments": "all",
            "handler": {"class": "H", "module_path": "m"},
        },
        "strategy": {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy.signal_strategy",
            "topk": 5,
            "n_drop": 1,
            "hold_thresh": 1,
            "kwargs": {"risk_degree": 0.9},
        },
        "backtest": {
            "account": 1_000_000,
            "exchange_kwargs": {"deal_price": "close", "limit_threshold": 0.095},
        },
        "universe_filter": {
            "pool": "all",
            "min_amount": 10_000_000,
            "min_listing_days": 60,
        },
    }

    port_cfg, out_pred, filter_stats = run_pred_backtest.prepare_signal_and_port_cfg(cfg, pred)

    assert isinstance(port_cfg["backtest"]["benchmark"], pd.Series)
    assert port_cfg["backtest"]["benchmark"].iloc[0] == 0.01
    assert seen == {"pool": "all", "min_amount": 10_000_000}
    assert filter_stats == {"n_keep": 1, "n_raw": 2}
    assert pd.isna(out_pred.iloc[1, 0])
    assert port_cfg["strategy"]["kwargs"]["signal"] is out_pred
