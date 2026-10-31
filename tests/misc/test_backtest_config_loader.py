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
TRAINING_YAML = CONFIGS / "csi300_lgbm_train_start_2006.yaml"


@pytest.mark.parametrize("loader", [cl.load_config, cl.resolve_config_path])
@pytest.mark.parametrize("config", [None, "", " \t\n"])
def test_config_requires_explicit_path(loader, config):
    with pytest.raises(cl.ConfigError, match="--config"):
        loader(config)


def test_load_config_without_argument_requires_explicit_path():
    with pytest.raises(cl.ConfigError, match="--config"):
        cl.load_config()


def test_run_backtest_requires_config(monkeypatch, capsys):
    import run_backtest as rb

    monkeypatch.setattr(sys, "argv", ["run_backtest.py"])
    with pytest.raises(SystemExit) as exc:
        rb.parse_args()
    assert exc.value.code == 2
    assert "--config" in capsys.readouterr().err


def test_load_explicit_training_config():
    cfg = cl.load_config(str(TRAINING_YAML))
    assert cfg["run"]["mode"] == "train_only"
    assert cfg["data"]["handler"]["class"] == "Alpha158"
    assert cfg["segments"]["test"] == ["2023-09-18", "2026-07-22"]
    assert cfg["backtest"]["start_time"] == "2023-09-18"
    assert cfg["backtest"]["end_time"] == "2026-07-22"
    assert cfg["strategy"]["topk"] == 10
    assert cfg["strategy"]["n_drop"] == 2
    assert "ProcessInf" in str(cfg["data"]["handler"]["infer_processors"])
    assert Path(cfg["_config_path"]) == TRAINING_YAML
    assert "test_start" not in cfg["run"]
    assert "test_end" not in cfg["run"]
    assert cfg["run"]["generate_figures"] is False
    assert cfg["run"]["from_session"] is None


def test_segments_test_aligns_backtest_and_extends_handler_end():
    raw = yaml.safe_load(TRAINING_YAML.read_text(encoding="utf-8"))
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
    raw = yaml.safe_load(TRAINING_YAML.read_text(encoding="utf-8"))
    raw["segments"]["test"] = ["2025-01-01", "2026-03-10"]
    cfg = cl.align_dates_from_segments(cl.validate_run_section(raw))
    assert cfg["data"]["handler"]["start_time"] == "2003-01-02"


def test_backtest_only_requires_a_model_source():
    raw = yaml.safe_load(TRAINING_YAML.read_text(encoding="utf-8"))
    raw["run"]["mode"] = "backtest_only"
    raw["run"]["from_session"] = None
    with pytest.raises(cl.ConfigError, match="model source"):
        cl.validate_run_section(raw)


def test_train_only_does_not_require_strategy_or_backtest():
    raw = yaml.safe_load(TRAINING_YAML.read_text(encoding="utf-8"))
    raw["run"]["mode"] = "train_only"
    raw["run"]["n_runs"] = 5
    raw.pop("strategy")
    raw.pop("backtest")

    cfg = cl.align_dates_from_segments(cl.validate_run_section(raw))

    assert cfg["run"]["mode"] == "train_only"
    assert cfg["run"]["n_runs"] == 5
    assert "strategy" not in cfg
    assert "backtest" not in cfg
    assert cfg["data"]["handler"]["end_time"] == cfg["segments"]["test"][1]


def test_tracked_parity_model_loads_without_session_or_mlruns(monkeypatch):
    import run_backtest as rb

    raw = yaml.safe_load(TRAINING_YAML.read_text(encoding="utf-8"))
    live = yaml.safe_load(
        (ROOT / "live_trading" / "configs" / "csi300_topk10_live.yaml").read_text(encoding="utf-8")
    )
    raw["run"]["mode"] = "backtest_only"
    raw["run"].pop("from_session", None)
    raw["parity"] = {
        "model_path": live["model"]["model_path"],
        "model_sha256": live["model"]["sha256"],
    }
    cfg = cl.align_dates_from_segments(cl.validate_run_section(raw))
    monkeypatch.setattr(
        cl,
        "resolve_session_dir",
        lambda *_: pytest.fail("tracked source tried to resolve a result session"),
    )
    monkeypatch.setattr(
        cl,
        "load_session_model_info",
        lambda *_args, **_kwargs: pytest.fail(
            "tracked source tried to read mlruns metadata"
        ),
    )

    source = cl.resolve_backtest_model_source(cfg, project_root=ROOT)
    model = rb.load_backtest_model(source)

    assert source["source_kind"] == "tracked"
    assert source["model_path"].is_relative_to(ROOT)
    assert model.model.num_feature() == 158


def test_invalid_mode():
    raw = yaml.safe_load(TRAINING_YAML.read_text(encoding="utf-8"))
    raw["run"]["mode"] = "foo"
    with pytest.raises(cl.ConfigError, match="run.mode"):
        cl.validate_run_section(raw)


@pytest.mark.parametrize(
    "config",
    [
        "csi300_lgbm_train_start_2006.yaml",
        "csi300_lgbm_train_start_2006",
        "backtest/configs/csi300_lgbm_train_start_2006.yaml",
        str(TRAINING_YAML),
    ],
)
def test_resolve_config_by_explicit_name_or_path(config):
    p = cl.resolve_config_path(config)
    assert p.is_file()
    assert p == TRAINING_YAML


def test_build_task_structure():
    cfg = cl.load_config(str(TRAINING_YAML))
    task = cl.build_task(cfg)
    assert task["model"]["class"] == "LGBModel"
    assert task["dataset"]["kwargs"]["handler"]["class"] == "Alpha158"
    assert task["dataset"]["kwargs"]["handler"]["kwargs"]["instruments"] == "csi300"
    assert task["dataset"]["kwargs"]["segments"]["train"][0] == "2006-01-02"


def test_build_task_override_handler_class():
    cfg = cl.load_config(str(TRAINING_YAML))
    task = cl.build_task(cfg, handler_class="Alpha158NoVWAP")
    assert task["dataset"]["kwargs"]["handler"]["class"] == "Alpha158NoVWAP"
    assert task["dataset"]["kwargs"]["handler"]["kwargs"]["fit_end_time"] == "2020-01-10"


def test_build_task_supports_custom_dataset_with_standard_handler_and_segments():
    cfg = cl.load_config(str(TRAINING_YAML))
    cfg["dataset"] = {
        "class": "LiquiditySegmentDatasetH",
        "module_path": "backtest.datasets.liquidity_segment",
        "kwargs": {
            "liquidity_bucket": "high",
            "n_buckets": 3,
            "lookback": 20,
            "lag": 1,
        },
    }

    task = cl.build_task(cfg)

    assert task["dataset"]["class"] == "LiquiditySegmentDatasetH"
    assert task["dataset"]["module_path"] == (
        "backtest.datasets.liquidity_segment"
    )
    assert task["dataset"]["kwargs"]["liquidity_bucket"] == "high"
    assert task["dataset"]["kwargs"]["handler"]["class"] == "Alpha158"
    assert task["dataset"]["kwargs"]["segments"]["valid"] == (
        "2020-01-13",
        "2023-09-15",
    )


def test_invalid_date_range():
    raw = yaml.safe_load(TRAINING_YAML.read_text(encoding="utf-8"))
    raw["segments"]["test"] = ["2026-01-01", "2025-01-01"]
    with pytest.raises(cl.ConfigError, match="测试区间非法"):
        cl.align_dates_from_segments(cl.validate_run_section(raw))


def test_legacy_test_start_end_ignored():
    raw = yaml.safe_load(TRAINING_YAML.read_text(encoding="utf-8"))
    raw["run"]["test_start"] = "2099-01-01"
    raw["run"]["test_end"] = "2099-12-31"
    raw["segments"]["test"] = ["2024-01-01", "2025-01-01"]
    cfg = cl.align_dates_from_segments(cl.validate_run_section(copy.deepcopy(raw)))
    assert "test_start" not in cfg["run"]
    assert cfg["backtest"]["start_time"] == "2024-01-01"
    assert cfg["backtest"]["end_time"] == "2025-01-01"


def test_training_config_preserves_research_identity():
    cfg = cl.load_config(str(TRAINING_YAML))
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


def test_training_config_builds_explicit_portfolio_settings():
    cfg = cl.load_config(str(TRAINING_YAML))
    portfolio = cl.build_port_analysis_config(cfg)

    assert portfolio["strategy"]["kwargs"] == {
        "topk": 10,
        "n_drop": 2,
        "risk_degree": 0.95,
        "hold_thresh": 1,
        "only_tradable": False,
        "forbid_all_trade_at_limit": False,
    }
    assert portfolio["backtest"]["benchmark"] == "SH000300"
    assert portfolio["backtest"]["exchange_kwargs"]["deal_price"] == "close"
