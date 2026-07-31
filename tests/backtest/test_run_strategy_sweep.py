from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config_loader  # noqa: E402
import phase_s_protocol as protocol  # noqa: E402
import run_strategy_sweep as sweep  # noqa: E402

BASE_CONFIG = ROOT / "backtest/configs/train-data/csi1000-full-v2/td_csi1000_full_v2_lgbm_s2000.yaml"


def _base() -> dict:
    return yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))


def test_valid_config_uses_csi1000_dates_500k_and_live_costs():
    candidate = next(
        row
        for row in protocol.strategy_grid("b1-m")
        if row["candidate_id"] == protocol.BASELINE_CANDIDATE_ID
    )

    cfg = sweep.build_sweep_config(
        _base(), candidate, pool="csi1000", segment="valid"
    )

    assert cfg["run"]["mode"] == "pred_backtest"
    assert cfg["segments"]["test"] == ["2020-01-13", "2021-07-15"]
    assert cfg["data"]["instruments"] == "csi1000"
    assert cfg["data"]["benchmark"] == "SH000852"
    assert cfg["backtest"]["account"] == 500000
    assert cfg["backtest"]["exchange_kwargs"] == {
        "freq": "day",
        "deal_price": "close",
        "limit_threshold": 0.095,
        "open_cost": 0.00021,
        "close_cost": 0.00071,
        "min_cost": 5.0,
        "trade_unit": 100,
    }
    assert cfg["strategy"]["kwargs"] == {
        "risk_degree": 0.95,
        "only_tradable": False,
        "forbid_all_trade_at_limit": False,
    }


def test_soft_topk_config_uses_precomputed_absolute_impact_limit():
    candidate = next(
        row
        for row in protocol.strategy_grid("b6-m")
        if row["candidate_id"] == "soft-t20-i050"
    )

    cfg = sweep.build_sweep_config(
        _base(), candidate, pool="csi1000", segment="valid"
    )

    assert cfg["strategy"] == {
        "class": "SoftTopkStrategy",
        "module_path": "qlib.contrib.strategy.cost_control",
        "topk": 20,
        "kwargs": {
            "trade_impact_limit": pytest.approx(0.02375),
            "risk_degree": 0.95,
        },
    }


def test_pred_backtest_mode_is_accepted_and_aligned(tmp_path):
    candidate = protocol.strategy_grid("b1-m")[0]
    cfg = sweep.build_sweep_config(
        _base(), candidate, pool="csi1000", segment="valid"
    )
    path = tmp_path / "candidate.yaml"
    path.write_text(yaml.safe_dump(copy.deepcopy(cfg)), encoding="utf-8")

    loaded = config_loader.load_config(str(path))

    assert loaded["run"]["mode"] == "pred_backtest"
    assert loaded["backtest"]["start_time"] == "2020-01-13"
    assert loaded["backtest"]["end_time"] == "2021-07-15"


def test_backtest_command_references_frozen_prediction_without_copy(tmp_path):
    command = sweep.build_backtest_command(
        Path(sys.executable),
        ROOT / "backtest/scripts/run_pred_backtest.py",
        tmp_path / "pred.pkl",
        tmp_path / "candidate.yaml",
        "valid-b1",
    )

    assert command == [
        sys.executable,
        str(ROOT / "backtest/scripts/run_pred_backtest.py"),
        "--pred",
        str(tmp_path / "pred.pkl"),
        "--config",
        str(tmp_path / "candidate.yaml"),
        "--note",
        "valid-b1",
        "--skip-pred-copy",
    ]
