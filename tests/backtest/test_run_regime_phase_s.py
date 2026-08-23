"""run_regime_phase_s：v4 臂与 k3h5 真阶梯配置。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import run_regime_phase_s as rps  # noqa: E402


def test_v4_arm_points_at_rankices_sessions():
    spec = rps.ARMS["m0h20rankices"]
    assert spec["sessions"][42] == "regimeadaptfast_m0h20_rankices_s42"
    assert spec["label_horizon"] == 20


def test_topk_f100_grid_strategies():
    s15 = rps.STRATEGIES["top15d3f100"]
    s5 = rps.STRATEGIES["top5d1f100"]
    s3 = rps.STRATEGIES["top3d1f100"]
    assert s15 == {
        "class": "TopkDropoutStrategy",
        "desc": s15["desc"],
        "topk": 15,
        "n_drop": 3,
        "hold_thresh": 1,
        "force_sell_rank": 100,
    }
    assert s5["topk"] == 5 and s5["n_drop"] == 1 and s5["force_sell_rank"] == 100
    assert s3["topk"] == 3 and s3["n_drop"] == 1 and s3["hold_thresh"] == 1
    assert s3["force_sell_rank"] == 100


def test_build_ensemble_config_writes_top3d1f100(tmp_path, monkeypatch):
    monkeypatch.setattr(rps, "CONFIG_DIR", tmp_path)
    path = rps.build_ensemble_config(
        "m0h20rankices",
        pool="all",
        strategy="top3d1f100",
        generate_figures=False,
        account=1_000_000,
        universe_filter=dict(rps.DEFAULT_UNIVERSE_FILTER, pool="all"),
    )
    text = path.read_text(encoding="utf-8")
    assert "TopkDropoutStrategy" in text
    assert "topk: 3" in text
    assert "n_drop: 1" in text
    assert "force_sell_rank: 100" in text


def test_ladder_k3h5_is_cohort_ladder():
    strat = rps.STRATEGIES["ladder_k3h5"]
    assert strat["class"] == "CohortLadderStrategy"
    assert strat["topk"] == 3
    assert strat["horizon"] == 5


def test_ladder_k3h5f100_adds_force_sell():
    strat = rps.STRATEGIES["ladder_k3h5f100"]
    assert strat["class"] == "CohortLadderStrategy"
    assert strat["topk"] == 3
    assert strat["horizon"] == 5
    assert strat["force_sell_rank"] == 100


def test_ladder_k3h5f100r_refills_after_force_sell():
    strat = rps.STRATEGIES["ladder_k3h5f100r"]
    assert strat["class"] == "CohortLadderStrategy"
    assert strat["topk"] == 3
    assert strat["horizon"] == 5
    assert strat["force_sell_rank"] == 100
    assert strat["refill_force_sell"] is True


def test_build_ensemble_config_writes_ladder_refill(tmp_path, monkeypatch):
    monkeypatch.setattr(rps, "CONFIG_DIR", tmp_path)
    path = rps.build_ensemble_config(
        "m0h20rankices",
        pool="all",
        strategy="ladder_k3h5f100r",
        generate_figures=False,
        account=1_000_000,
        universe_filter=dict(rps.DEFAULT_UNIVERSE_FILTER, pool="all"),
    )
    text = path.read_text(encoding="utf-8")
    assert "force_sell_rank: 100" in text
    assert "refill_force_sell: true" in text


def test_build_ensemble_config_writes_ladder_force_sell(tmp_path, monkeypatch):
    monkeypatch.setattr(rps, "CONFIG_DIR", tmp_path)
    path = rps.build_ensemble_config(
        "m0h20rankices",
        pool="all",
        strategy="ladder_k3h5f100",
        generate_figures=False,
        account=1_000_000,
        universe_filter=dict(rps.DEFAULT_UNIVERSE_FILTER, pool="all"),
    )
    text = path.read_text(encoding="utf-8")
    assert "CohortLadderStrategy" in text
    assert "horizon: 5" in text
    assert "force_sell_rank: 100" in text


def test_build_ensemble_config_writes_ladder_horizon(tmp_path, monkeypatch):
    monkeypatch.setattr(rps, "CONFIG_DIR", tmp_path)
    path = rps.build_ensemble_config(
        "m0h20rankices",
        pool="all",
        strategy="ladder_k3h5",
        generate_figures=False,
        account=1_000_000,
        universe_filter=dict(rps.DEFAULT_UNIVERSE_FILTER, pool="all"),
    )
    text = path.read_text(encoding="utf-8")
    assert "CohortLadderStrategy" in text
    assert "horizon: 5" in text
    assert "topk: 3" in text
    assert "n_drop:" not in text
