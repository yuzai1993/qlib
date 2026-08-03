"""live 配置合并加载测试。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.live_config import load_live_config

NEW_LIVE_PATH = (
    REPO_ROOT / "live_trading" / "configs" /
    "csi1000_b6m_b2s_postclose.yaml"
)


def test_load_real_live_config_is_standalone():
    import yaml

    path = REPO_ROOT / "live_trading" / "configs" / "csi300_topk10_live.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "base_config" not in raw

    cfg = load_live_config(
        path,
        project_root=REPO_ROOT,
    )
    assert cfg["strategy"]["topk"] == 10
    assert cfg["strategy"]["n_drop"] == 2
    assert cfg["strategy"]["risk_degree"] == pytest.approx(0.95)
    assert cfg["exchange"]["trade_unit"] == 100
    assert cfg["handler"]["fit_start_time"] == "2016-01-02"
    assert cfg["model"]["model_path"].startswith("live_trading/models/b1_m/")
    assert "mlruns_dir" not in cfg["model"]
    assert cfg["model"]["experiment_id"] == "836973677275181001"
    assert cfg["live"]["strategy_id"] == "csi300_topk10"
    assert cfg["live"]["default_mode"] == "LIVE"  # 2026-07-14 起实盘开关打开
    assert cfg["fees"]["stamp_duty_rate"] == 0.0005
    assert "live_trading" in cfg["storage"]["db_path"]
    assert cfg["_config_id"] == "csi300_topk10_live"


def test_load_standalone_minimal_config(tmp_path):
    p = tmp_path / "standalone.yaml"
    p.write_text("live:\n  strategy_id: s1\n", encoding="utf-8")
    cfg = load_live_config(p, project_root=tmp_path)
    assert cfg["live"]["strategy_id"] == "s1"


def test_load_new_csi1000_paper_config():
    cfg = load_live_config(NEW_LIVE_PATH, project_root=REPO_ROOT)

    assert cfg["data"]["instruments"] == "csi1000"
    assert cfg["data"]["benchmark"] == "SH000852"
    assert cfg["account"]["opening_cash"] == pytest.approx(9_949_714.06)
    assert cfg["account"]["opening_value_adjustment"] == pytest.approx(
        -681_126.98
    )
    assert cfg["handler"]["class"] == "Alpha158Technical"
    assert cfg["handler"]["feature_groups"] == ["range"]
    assert cfg["strategy"] == {
        "class": "TopkDropoutStrategy",
        "topk": 30,
        "n_drop": 2,
        "initial_buy_count": 2,
        "risk_degree": 0.95,
        "hold_thresh": 20,
        "only_tradable": False,
        "forbid_all_trade_at_limit": False,
    }
    assert cfg["live"]["broker_environment"] == "SIMULATION"
    assert cfg["live"]["allow_real_money"] is False
    assert cfg["live"]["default_mode"] == "SIMULATE"
    assert cfg["live"]["after_hours_price_type"] == 49
    assert cfg["web"] == {"host": "127.0.0.1", "port": 8081}
    assert "schedule" not in cfg
    assert cfg["storage"]["db_path"].endswith(
        "csi1000_b6m_b2s_postclose.db"
    )
    assert cfg["provenance"]["strategy_baseline_config"].endswith(
        "strategy-stability/b6-m/topk-t30-d2-h20_csi1000_full.yaml"
    )
    assert cfg["provenance"]["strategy_baseline_sha256"] == (
        "1f580ac881aa9682e8f5f353683b970031622765c6cd565602f3dcb76e01183f"
    )


@pytest.mark.parametrize(
    "change,message",
    [
        (("live", "broker_environment", "REAL"), "broker_environment"),
        (("live", "allow_real_money", True), "allow_real_money"),
        (("account", "opening_cash", 0), "opening_cash"),
        (("strategy", "initial_buy_count", 0), "initial_buy_count"),
        (("strategy", "initial_buy_count", 31), "initial_buy_count"),
    ],
)
def test_simulation_config_safety_fields_fail_closed(tmp_path, change, message):
    import yaml

    config = {
        "account": {"opening_cash": 500_000.0},
        "strategy": {"topk": 30, "initial_buy_count": 2},
        "live": {
            "strategy_id": "paper",
            "broker_environment": "SIMULATION",
            "allow_real_money": False,
            "after_hours_price_type": 49,
        },
    }
    section, key, value = change
    config[section][key] = value
    path = tmp_path / "paper.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_live_config(path, project_root=tmp_path)


@pytest.mark.parametrize(
    "opening_cash,adjustment,message",
    [
        (500_000.0, float("inf"), "opening_value_adjustment"),
        (500_000.0, True, "opening_value_adjustment"),
        (500_000.0, -500_000.0, "economic opening value"),
        (500_000.0, -600_000.0, "economic opening value"),
    ],
)
def test_simulation_account_adjustment_fails_closed(
    tmp_path, opening_cash, adjustment, message,
):
    import yaml

    config = {
        "account": {
            "opening_cash": opening_cash,
            "opening_value_adjustment": adjustment,
        },
        "strategy": {"topk": 30, "initial_buy_count": 2},
        "live": {
            "strategy_id": "paper",
            "broker_environment": "SIMULATION",
            "allow_real_money": False,
            "after_hours_price_type": 49,
        },
    }
    path = tmp_path / "paper.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_live_config(path, project_root=tmp_path)


def test_simulation_account_accepts_negative_adjustment_with_positive_nav(tmp_path):
    import yaml

    config = {
        "account": {
            "opening_cash": 9_949_714.06,
            "opening_value_adjustment": -681_126.98,
        },
        "strategy": {"topk": 30, "initial_buy_count": 2},
        "live": {
            "strategy_id": "paper",
            "broker_environment": "SIMULATION",
            "allow_real_money": False,
            "after_hours_price_type": 49,
        },
    }
    path = tmp_path / "paper.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    loaded = load_live_config(path, project_root=tmp_path)

    assert loaded["account"]["opening_value_adjustment"] == pytest.approx(
        -681_126.98
    )


def _write_baseline_config(tmp_path, baseline):
    import yaml

    p = tmp_path / "baseline.yaml"
    p.write_text(
        yaml.safe_dump({"monitor": {"performance_baseline": baseline}}),
        encoding="utf-8",
    )
    return p


def test_load_valid_performance_baseline(tmp_path):
    baseline = {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": 10_000_000.0,
        "benchmark_close": 4786.78271484375,
    }
    cfg = load_live_config(
        _write_baseline_config(tmp_path, baseline), project_root=tmp_path,
    )
    assert cfg["monitor"]["performance_baseline"] == baseline


@pytest.mark.parametrize("baseline", [
    {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": 10_000_000.0,
    },
    {
        "first_snapshot_date": "20260716",
        "opening_total_value": 10_000_000.0,
        "benchmark_close": 4786.78,
    },
    {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": 0.0,
        "benchmark_close": 4786.78,
    },
    {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": 10_000_000.0,
        "benchmark_close": True,
    },
    {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": "ten million",
        "benchmark_close": 4786.78,
    },
])
def test_invalid_performance_baseline_fails_closed(tmp_path, baseline):
    with pytest.raises(ValueError, match="performance_baseline"):
        load_live_config(
            _write_baseline_config(tmp_path, baseline), project_root=tmp_path,
        )
