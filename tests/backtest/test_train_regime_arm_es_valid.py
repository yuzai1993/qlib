from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.scripts.train.train_regime_arm import (
    resolve_es_valid_source,
)  # noqa: E402
from backtest.scripts.train import train_regime_arm  # noqa: E402


def test_auto_head_metrics_use_eval_window():
    assert resolve_es_valid_source("top3_h5_net_ann", "auto") == "eval_window"
    assert resolve_es_valid_source("top5_h5_net_ann", "auto") == "eval_window"


def test_auto_rankic_uses_eval_window():
    """v4 起 RankIC 早停默认对齐评估窗，不再悄悄走 v1 的 499 日分层集。"""
    assert resolve_es_valid_source("daily_rank_ic", "auto") == "eval_window"


def test_explicit_stratified70_keeps_v1_valid_frame():
    assert resolve_es_valid_source("daily_rank_ic", "stratified70") == "stratified70"


def test_explicit_eval_window_keeps_rankic_on_v3_valid_frame():
    assert resolve_es_valid_source("daily_rank_ic", "eval_window") == "eval_window"


def test_rejects_unknown_es_valid():
    with pytest.raises(ValueError, match="es_valid"):
        resolve_es_valid_source("daily_rank_ic", "full")


@pytest.mark.parametrize("horizon", [1, 2, 3, 5, 7, 10, 15, 20, 30, 40])
def test_label_horizon_accepts_existing_and_common_cohort_targets(horizon):
    assert train_regime_arm.validate_label_horizon(horizon) == horizon


@pytest.mark.parametrize(
    "horizon", [0, -1, 4, 6, 8, 14, 21, 39, 41, True, 5.0, "5", None]
)
def test_label_horizon_rejects_unsupported_or_non_integer_targets(horizon):
    with pytest.raises(ValueError, match="label-horizon"):
        train_regime_arm.validate_label_horizon(horizon)


def test_cli_rejects_label_horizon_before_qlib_initialization(monkeypatch):
    import qlib

    def forbidden_init(*args, **kwargs):
        raise AssertionError("invalid horizon reached data initialization")

    monkeypatch.setattr(qlib, "init", forbidden_init)
    monkeypatch.setattr(
        sys,
        "argv",
        ["train_regime_arm.py", "--arm", "m0", "--seed", "42", "--label-horizon", "8"],
    )
    with pytest.raises(ValueError, match="label-horizon"):
        train_regime_arm.main()


def test_legacy_unbounded_validation_cache_is_not_reused(monkeypatch, tmp_path):
    import pandas as pd
    import qlib

    old_cache = tmp_path / "m0" / "valid_frame_t5h5es.pkl"
    old_cache.parent.mkdir()
    pd.to_pickle({"frame": pd.DataFrame({"x": [1.0]}), "tradable": None}, old_cache)
    old_bytes = old_cache.read_bytes()

    def build_bounded(*args, **kwargs):
        raise RuntimeError("fresh exit-bounded validation requested")

    def reject_training(*args, **kwargs):
        raise AssertionError("unbounded validation reached training assembly")

    monkeypatch.setattr(qlib, "init", lambda **kwargs: None)
    monkeypatch.setattr(train_regime_arm, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(train_regime_arm, "build_t5h5es_valid_frame", build_bounded)
    monkeypatch.setattr(train_regime_arm, "load_train_matrix", reject_training)
    monkeypatch.setattr(sys, "argv", [
        "train_regime_arm.py", "--arm", "m0", "--seed", "42", "--model", "single",
        "--label-horizon", "20", "--es-metric", "top5_h5_net_ann", "--es-valid", "eval_window",
    ])
    with pytest.raises(RuntimeError, match="fresh exit-bounded"):
        train_regime_arm.main()
    assert old_cache.read_bytes() == old_bytes


def test_head_early_stop_excludes_labels_exiting_after_validation_window(monkeypatch):
    """Available provider labels after valid end must not leak into ES selection."""
    import numpy as np
    import pandas as pd
    import qlib.data
    import qlib.data.dataset
    from types import SimpleNamespace
    from backtest.scripts.evaluate import eval_ic_multi_pool as evaluation
    from backtest.scripts.prepare import prepare_regime_train_chunks as preparation

    calendar = pd.bdate_range("2020-08-03", periods=12)
    index = pd.MultiIndex.from_product(
        [calendar, [f"SH600{i:03d}" for i in range(20)]],
        names=["datetime", "instrument"],
    )
    features = pd.DataFrame({"feature0": np.arange(len(index), dtype="float32")}, index=index)
    labels = pd.Series(np.arange(len(index), dtype="float32") / 1000, index=index)

    class Dataset:
        def __init__(self, **kwargs):
            pass

        def prepare(self, *args, **kwargs):
            return features.copy()

    def calendar_in_window(*args, **kwargs):
        assert kwargs["end_time"] == str(calendar[-1].date())
        return calendar

    monkeypatch.setattr(train_regime_arm, "EVAL_START", str(calendar[0].date()))
    monkeypatch.setattr(train_regime_arm, "VALID_END", str(calendar[-1].date()))
    monkeypatch.setattr(qlib.data, "D", SimpleNamespace(calendar=calendar_in_window))
    monkeypatch.setattr(qlib.data.dataset, "DatasetH", Dataset)
    monkeypatch.setattr(preparation, "build_handler", lambda *a, **k: object())
    monkeypatch.setattr(evaluation, "_fetch_label", lambda *a, **k: labels.copy())
    for name in ["_stock_only_mask", "_listing_age_mask"]:
        monkeypatch.setattr(evaluation, name, lambda idx, *a: pd.Series(True, index=idx))
    monkeypatch.setattr(train_regime_arm, "_st_keep_for_eval", lambda idx: pd.Series(True, index=idx))
    for name in ["amount_mask", "entry_tradable_mask"]:
        monkeypatch.setattr(evaluation, name, lambda *a, **k: pd.Series(True, index=index))

    frame, tradable = train_regime_arm.build_t5h5es_valid_frame("m0")
    expected = index[index.get_level_values("datetime") <= calendar[-7]]
    assert frame.index.equals(expected)
    assert tradable.index.equals(expected)
    assert frame[("label", "LABEL0")].equals(labels.loc[expected].rename(("label", "LABEL0")))
