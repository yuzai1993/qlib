"""config_loader 策略 kwargs 与 eval_protocol 单元测试。"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import yaml
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config_loader as cl  # noqa: E402
import eval_protocol as ep  # noqa: E402

DEFAULT_YAML = ROOT / "backtest" / "configs" / "csi300_live_parity.yaml"


def _base_cfg(**strategy_overrides):
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    if "hold_thresh" in strategy_overrides:
        raw["strategy"]["kwargs"].pop("hold_thresh", None)
    raw["strategy"].update(strategy_overrides)
    return cl.align_dates_from_segments(cl.validate_run_section(copy.deepcopy(raw)))


def test_soft_topk_no_n_drop_required():
    cfg = _base_cfg(
        **{
            "class": "SoftTopkStrategy",
            "module_path": "qlib.contrib.strategy.cost_control",
            "topk": 10,
            "kwargs": {"trade_impact_limit": 0.05, "risk_degree": 0.95},
        }
    )
    cfg["strategy"].pop("n_drop", None)

    pac = cl.build_port_analysis_config(cfg)
    kw = pac["strategy"]["kwargs"]

    assert "n_drop" not in kw
    assert kw["topk"] == 10
    assert kw["trade_impact_limit"] == 0.05
    assert kw["risk_degree"] == 0.95


def test_topk_dropout_hold_thresh_passthrough():
    cfg = _base_cfg(hold_thresh=3)

    pac = cl.build_port_analysis_config(cfg)
    kw = pac["strategy"]["kwargs"]

    assert kw["topk"] == 10
    assert kw["n_drop"] == 2
    assert kw["hold_thresh"] == 3


def test_pairwise_win_count_rows_and_dict():
    rows_a = [
        {"seed": 1, "excess_with_cost_information_ratio": 0.6},
        {"seed": 2, "excess_with_cost_information_ratio": 0.4},
    ]
    rows_b = [
        {"seed": 1, "excess_with_cost_information_ratio": 0.5},
        {"seed": 2, "excess_with_cost_information_ratio": 0.5},
    ]
    out_rows = ep.pairwise_win_count(rows_a, rows_b, metric="ir")
    assert out_rows["n"] == 2
    assert out_rows["wins"] == 1
    assert out_rows["diff_mean"] == 0.0
    assert out_rows["diffs"] == [pytest.approx(0.1), pytest.approx(-0.1)]

    dict_a = {1: {"ir": 0.7}, 2: {"ir": 0.3}}
    dict_b = {1: {"ir": 0.5}, 2: {"ir": 0.5}}
    out_dict = ep.pairwise_win_count(dict_a, dict_b, metric="ir")
    assert out_dict["n"] == 2
    assert out_dict["wins"] == 1
    assert out_dict["diff_mean"] == pytest.approx(0.0)
    assert out_dict["diffs"] == [pytest.approx(0.2), pytest.approx(-0.2)]


def test_write_seed_ensemble_comparison(tmp_path: Path):
    seed_metrics = {}
    for seed, ir in [(42, 0.6), (1000, 0.4)]:
        mpath = tmp_path / f"s{seed}" / "metrics.json"
        mpath.parent.mkdir()
        mpath.write_text(
            json.dumps(
                {
                    "status": "success",
                    "excess_with_cost_information_ratio": ir,
                    "excess_with_cost_annualized_return": 0.1,
                    "excess_with_cost_max_drawdown": -0.2,
                }
            ),
            encoding="utf-8",
        )
        seed_metrics[seed] = mpath

    ensemble_path = tmp_path / "ensemble" / "metrics.json"
    ensemble_path.parent.mkdir()
    ensemble_path.write_text(
        json.dumps(
            {
                "status": "success",
                "excess_with_cost_information_ratio": 0.55,
                "excess_with_cost_annualized_return": 0.09,
                "excess_with_cost_max_drawdown": -0.18,
            }
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    paths = ep.write_seed_ensemble_comparison(
        out_dir,
        list(seed_metrics.items()),
        ensemble_path,
        group_name="cum_h10",
    )
    assert paths["csv"].is_file()
    assert paths["md"].is_file()
    md = paths["md"].read_text(encoding="utf-8")
    assert "cum_h10 seed 均值" in md
    assert "cum_h10 ensemble" in md
    assert "0.5000" in md
