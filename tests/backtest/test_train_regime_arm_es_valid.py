from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

from train_regime_arm import resolve_es_valid_source  # noqa: E402


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
