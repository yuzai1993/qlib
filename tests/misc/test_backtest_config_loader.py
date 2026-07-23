"""backtest config_loader 单元测试。"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config_loader as cl  # noqa: E402

CONFIGS = ROOT / "backtest" / "configs"
DEFAULT_YAML = CONFIGS / "csi300_live_parity.yaml"


def test_load_default_config_is_live_parity_baseline():
    cfg = cl.load_config()
    assert cfg["run"]["mode"] == "backtest_only"
    assert cfg["data"]["handler"]["class"] == "Alpha158"
    assert cfg["segments"]["test"] == ["2023-09-18", "2026-07-22"]
    assert cfg["backtest"]["start_time"] == "2023-09-18"
    assert cfg["backtest"]["end_time"] == "2026-07-22"
    assert cfg["strategy"]["topk"] == 10
    assert cfg["strategy"]["n_drop"] == 2
    assert "ProcessInf" in str(cfg["data"]["handler"]["infer_processors"])
    assert Path(cfg["_config_path"]).name == "csi300_live_parity.yaml"
    assert "test_start" not in cfg["run"]
    assert "test_end" not in cfg["run"]
    assert cfg["run"]["generate_figures"] is False
    assert cfg["run"]["from_session"] == "20260711_223223_train_start_2006"


def test_segments_test_aligns_backtest_and_extends_handler_end():
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["segments"]["test"] = ["2024-01-01", "2026-06-01"]
    raw["data"]["handler"]["end_time"] = "2026-03-10"
    orig_start = raw["data"]["handler"]["start_time"]

    cfg = cl.align_dates_from_segments(cl.validate_run_section(copy.deepcopy(raw)))

    assert cfg["segments"]["test"] == ["2024-01-01", "2026-06-01"]
    assert cfg["backtest"]["start_time"] == "2024-01-01"
    assert cfg["backtest"]["end_time"] == "2026-06-01"
    assert cfg["data"]["handler"]["end_time"] == "2026-06-01"
    assert cfg["data"]["handler"]["start_time"] == orig_start


def test_handler_start_not_narrowed_when_test_starts_later():
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["segments"]["test"] = ["2025-01-01", "2026-03-10"]
    cfg = cl.align_dates_from_segments(cl.validate_run_section(raw))
    assert cfg["data"]["handler"]["start_time"] == "2003-01-02"


def test_backtest_only_requires_from_session():
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["run"]["mode"] = "backtest_only"
    raw["run"]["from_session"] = None
    with pytest.raises(cl.ConfigError, match="from_session"):
        cl.validate_run_section(raw)


def test_invalid_mode():
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["run"]["mode"] = "foo"
    with pytest.raises(cl.ConfigError, match="run.mode"):
        cl.validate_run_section(raw)


def test_resolve_config_by_name():
    p = cl.resolve_config_path("csi300_live_parity.yaml")
    assert p.is_file()
    assert p.name == "csi300_live_parity.yaml"


def test_build_task_structure():
    cfg = cl.load_config()
    task = cl.build_task(cfg)
    assert task["model"]["class"] == "LGBModel"
    assert task["dataset"]["kwargs"]["handler"]["class"] == "Alpha158"
    assert task["dataset"]["kwargs"]["handler"]["kwargs"]["instruments"] == "csi300"
    assert task["dataset"]["kwargs"]["segments"]["train"][0] == "2006-01-02"


def test_build_task_override_handler_class():
    cfg = cl.load_config()
    task = cl.build_task(cfg, handler_class="Alpha158NoVWAP")
    assert task["dataset"]["kwargs"]["handler"]["class"] == "Alpha158NoVWAP"
    assert task["dataset"]["kwargs"]["handler"]["kwargs"]["fit_end_time"] == "2020-01-10"


def test_invalid_date_range():
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["segments"]["test"] = ["2026-01-01", "2025-01-01"]
    with pytest.raises(cl.ConfigError, match="测试区间非法"):
        cl.align_dates_from_segments(cl.validate_run_section(raw))


def test_legacy_test_start_end_ignored():
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["run"]["test_start"] = "2099-01-01"
    raw["run"]["test_end"] = "2099-12-31"
    raw["segments"]["test"] = ["2024-01-01", "2025-01-01"]
    cfg = cl.align_dates_from_segments(cl.validate_run_section(copy.deepcopy(raw)))
    assert "test_start" not in cfg["run"]
    assert cfg["backtest"]["start_time"] == "2024-01-01"
    assert cfg["backtest"]["end_time"] == "2025-01-01"


def test_production_baseline_identity():
    cfg = cl.load_config()
    assert cfg["data"]["instruments"] == "csi300"
    assert cfg["data"]["benchmark"] == "SH000300"
    assert cfg["data"]["handler"]["class"] == "Alpha158"
    assert cfg["segments"]["train"] == ["2006-01-02", "2020-01-10"]
    assert cfg["strategy"]["class"] == "TopkDropoutStrategy"
    assert cfg["strategy"]["topk"] == 10
    assert cfg["strategy"]["n_drop"] == 2
    assert cfg["backtest"]["account"] == 10_000_000
    assert cfg["backtest"]["exchange_kwargs"]["open_cost"] == 0.00021
    assert cfg["backtest"]["exchange_kwargs"]["close_cost"] == 0.00071


def test_training_config_backtests_with_current_production_portfolio():
    baseline = cl.load_config()
    training = cl.load_config("csi300_lgbm_train_start_2006.yaml")
    assert training["strategy"] == baseline["strategy"]
    assert training["backtest"] == baseline["backtest"]
