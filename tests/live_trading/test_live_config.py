"""live 配置合并加载测试。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.live_config import load_live_config
from live_trading.modules.execution_profile import get_execution_profile

NEW_LIVE_PATH = (
    REPO_ROOT / "live_trading" / "configs" /
    "csi1000_b6m_b2s_postclose.yaml"
)
REAL_LIVE_PATH = (
    REPO_ROOT / "live_trading" / "configs" /
    "csi1000_b6m_b2s_postclose_real.yaml"
)
PROBE_LIVE_PATH = (
    REPO_ROOT / "live_trading" / "configs" /
    "csi1000_pr49_one_lot_probe.yaml"
)
LADDER_LIVE_PATH = (
    REPO_ROOT / "live_trading" / "configs" /
    "alla_v4_ladder_k3h5_postclose_real.yaml"
)
OBSERVATION_LADDER_LIVE_PATH = (
    REPO_ROOT / "live_trading" / "configs" /
    "alla_v4_ladder_k1h5_postclose_real.yaml"
)


def test_execution_profiles_define_the_qmt_and_signal_price_contracts():
    close_auction = get_execution_profile("CLOSE_AUCTION")
    fixed_price = get_execution_profile("AFTER_HOURS_FIXED_PRICE")

    assert close_auction.qmt_price_type == 11
    assert close_auction.signal_price_type == "CLOSE_AUCTION_LIMIT"
    assert fixed_price.qmt_price_type == 49
    assert fixed_price.signal_price_type == "AFTER_HOURS_CLOSE"


def test_load_operator_probe_config_is_isolated_from_strategy_publishing():
    cfg = load_live_config(PROBE_LIVE_PATH, project_root=REPO_ROOT)

    assert cfg["live"]["kind"] == "OPERATOR_PROBE"
    assert cfg["live"]["strategy_id"] == "csi1000_pr49_one_lot_probe"
    assert cfg["live"]["main_strategy_id"] == \
        "csi1000_b6m_b2s_postclose_real"
    assert cfg["live"]["execution_session"] == "AFTER_HOURS_FIXED_PRICE"
    assert cfg["live"]["close_auction_price_type"] == 49
    assert cfg["live"]["bridge_root"] == "/Volumes/qmt_bridge/pr49_probe"
    assert cfg["live"]["max_orders_per_day"] == 100
    assert cfg["live"]["submit_after"] == "15:00:05"
    assert cfg["live"]["cancel_at"] == "15:28:00"
    assert cfg["live"]["finalize_at"] == "15:30:00"
    assert cfg["live"]["snapshot_after"] == "15:31:00"
    assert cfg["storage"]["db_path"] == (
        "live_trading/data/csi1000_b6m_b2s_postclose_real.db"
    )
    assert "model" not in cfg
    assert "parity" not in cfg


def test_operator_probe_requires_explicit_main_strategy_binding(tmp_path):
    import yaml

    config = yaml.safe_load(PROBE_LIVE_PATH.read_text(encoding="utf-8"))
    config["live"].pop("main_strategy_id", None)
    path = tmp_path / "probe.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match="main_strategy_id"):
        load_live_config(path, project_root=tmp_path)


def test_operator_probe_rejects_decoy_main_strategy_binding(tmp_path):
    import yaml

    config = yaml.safe_load(PROBE_LIVE_PATH.read_text(encoding="utf-8"))
    config["live"]["main_strategy_id"] = "paused_decoy"
    path = tmp_path / "probe.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match="csi1000_b6m_b2s_postclose_real"):
        load_live_config(path, project_root=tmp_path)


def _strategy_config(**live_overrides):
    live = {
        "kind": "STRATEGY",
        "strategy_id": "main",
        "broker_environment": "REAL",
        "allow_real_money": True,
        "default_mode": "LIVE",
        "execution_session": "CLOSE_AUCTION",
        "close_auction_price_type": 11,
        "submit_after": "14:57:05",
        "cancel_at": "15:00:05",
        "finalize_at": "15:00:30",
        "snapshot_after": "15:01:00",
    }
    live.update(live_overrides)
    return {
        "account": {"opening_cash": 1_000_000.0},
        "strategy": {"topk": 30, "initial_buy_count": 2},
        "live": live,
    }


def _write_strategy_config(tmp_path, config):
    import yaml

    path = tmp_path / "main.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_strategy_config_rejects_price_type_from_the_other_profile(tmp_path):
    path = _write_strategy_config(
        tmp_path, _strategy_config(close_auction_price_type=49),
    )

    with pytest.raises(ValueError, match="price_type"):
        load_live_config(path, project_root=tmp_path)


def test_strategy_config_accepts_after_hours_fixed_price(tmp_path):
    """v4 真阶梯把主策略搬到盘后固定价，不再是 operator probe 专属。"""
    path = _write_strategy_config(tmp_path, _strategy_config(
        execution_session="AFTER_HOURS_FIXED_PRICE",
        close_auction_price_type=49,
        submit_after="15:00:05",
        cancel_at="15:28:00",
        finalize_at="15:30:00",
        snapshot_after="15:31:00",
    ))

    cfg = load_live_config(path, project_root=tmp_path)

    assert cfg["live"]["execution_session"] == "AFTER_HOURS_FIXED_PRICE"


def test_strategy_config_rejects_half_switched_execution_session(tmp_path):
    """只改 session 不改价类型与时点，必须 fail-closed。"""
    path = _write_strategy_config(
        tmp_path, _strategy_config(execution_session="AFTER_HOURS_FIXED_PRICE"),
    )

    with pytest.raises(ValueError, match="price_type"):
        load_live_config(path, project_root=tmp_path)


def test_strategy_config_rejects_unknown_execution_session(tmp_path):
    path = _write_strategy_config(
        tmp_path, _strategy_config(execution_session="MORNING_AUCTION"),
    )

    with pytest.raises(ValueError):
        load_live_config(path, project_root=tmp_path)


def test_ladder_strategy_needs_horizon_instead_of_initial_buy_count(tmp_path):
    """阶梯每天加一层、h 天后满仓，建仓爬坡是结构性的，没有 initial_buy_count。"""
    config = _strategy_config()
    config["strategy"] = {"class": "CohortLadderStrategy", "topk": 3, "horizon": 5}
    path = _write_strategy_config(tmp_path, config)

    cfg = load_live_config(path, project_root=tmp_path)

    assert cfg["strategy"]["horizon"] == 5


@pytest.mark.parametrize("strategy", [
    {"class": "CohortLadderStrategy", "topk": 3},
    {"class": "CohortLadderStrategy", "topk": 3, "horizon": 0},
    {"class": "CohortLadderStrategy", "horizon": 5},
])
def test_ladder_strategy_rejects_missing_or_bad_horizon(tmp_path, strategy):
    config = _strategy_config()
    config["strategy"] = strategy
    path = _write_strategy_config(tmp_path, config)

    with pytest.raises(ValueError, match="horizon|topk"):
        load_live_config(path, project_root=tmp_path)


@pytest.mark.parametrize(
    "field,wrong_value",
    [
        ("submit_after", "14:57:06"),
        ("cancel_at", "15:00:06"),
        ("finalize_at", "15:00:31"),
        ("snapshot_after", "15:01:01"),
    ],
)
def test_strategy_timing_fields_are_required_and_profile_bound(
    tmp_path, field, wrong_value,
):
    import yaml

    config = {
        "account": {"opening_cash": 1_000_000.0},
        "strategy": {"topk": 30, "initial_buy_count": 2},
        "live": {
            "kind": "STRATEGY",
            "strategy_id": "main",
            "broker_environment": "REAL",
            "allow_real_money": True,
            "default_mode": "LIVE",
            "execution_session": "CLOSE_AUCTION",
            "close_auction_price_type": 11,
            "submit_after": "14:57:05",
            "cancel_at": "15:00:05",
            "finalize_at": "15:00:30",
            "snapshot_after": "15:01:00",
        },
    }
    path = tmp_path / "strategy.yaml"

    config["live"].pop(field)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=field):
        load_live_config(path, project_root=tmp_path)

    config["live"][field] = wrong_value
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=field):
        load_live_config(path, project_root=tmp_path)


@pytest.mark.parametrize(
    "field,wrong_value",
    [
        ("submit_after", "15:05:01"),
        ("cancel_at", "15:28:01"),
        ("finalize_at", "15:30:01"),
        ("snapshot_after", "15:31:01"),
    ],
)
def test_operator_probe_timing_fields_are_required_and_profile_bound(
    tmp_path, field, wrong_value,
):
    import yaml

    config = {
        "account": {"opening_cash": 1_000_000.0},
        "live": {
            "kind": "OPERATOR_PROBE",
            "strategy_id": "csi1000_pr49_one_lot_probe",
            "main_strategy_id": "csi1000_b6m_b2s_postclose_real",
            "bridge_root": "/Volumes/qmt_bridge/pr49_probe",
            "broker_environment": "REAL",
            "allow_real_money": True,
            "default_mode": "LIVE",
            "execution_session": "AFTER_HOURS_FIXED_PRICE",
            "close_auction_price_type": 49,
            "submit_after": "15:00:05",
            "cancel_at": "15:28:00",
            "finalize_at": "15:30:00",
            "snapshot_after": "15:31:00",
            "max_orders_per_day": 100,
        },
    }
    path = tmp_path / "probe.yaml"

    config["live"].pop(field)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=field):
        load_live_config(path, project_root=tmp_path)

    config["live"][field] = wrong_value
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=field):
        load_live_config(path, project_root=tmp_path)


@pytest.mark.parametrize(
    "change,message",
    [
        (("live", "close_auction_price_type", 11), "price_type"),
        (("live", "bridge_root", "/Volumes/qmt_bridge"), "bridge_root"),
        (("live", "strategy_id", "not_the_probe"), "strategy_id"),
    ],
)
def test_operator_probe_rejects_cross_profile_or_shared_bridge(
    tmp_path, change, message,
):
    import yaml

    config = {
        "account": {"opening_cash": 1_000_000.0},
        "live": {
            "kind": "OPERATOR_PROBE",
            "strategy_id": "csi1000_pr49_one_lot_probe",
            "main_strategy_id": "csi1000_b6m_b2s_postclose_real",
            "bridge_root": "/Volumes/qmt_bridge/pr49_probe",
            "broker_environment": "REAL",
            "allow_real_money": True,
            "default_mode": "LIVE",
            "execution_session": "AFTER_HOURS_FIXED_PRICE",
            "close_auction_price_type": 49,
            "submit_after": "15:00:05",
            "cancel_at": "15:28:00",
            "finalize_at": "15:30:00",
            "snapshot_after": "15:31:00",
            "max_orders_per_day": 100,
        },
    }
    section, key, value = change
    config[section][key] = value
    path = tmp_path / "probe.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_live_config(path, project_root=tmp_path)


@pytest.mark.parametrize(
    "changes,message",
    [
        (
            {
                "broker_environment": "SIMULATION",
                "allow_real_money": False,
                "default_mode": "SIMULATE",
            },
            "OPERATOR_PROBE requires REAL/LIVE",
        ),
        ({"max_orders_per_day": 99}, "max_orders_per_day"),
    ],
)
def test_operator_probe_requires_real_live_and_one_lot_limit(
    tmp_path, changes, message,
):
    import yaml

    config = {
        "account": {"opening_cash": 1_000_000.0},
        "live": {
            "kind": "OPERATOR_PROBE",
            "strategy_id": "csi1000_pr49_one_lot_probe",
            "main_strategy_id": "csi1000_b6m_b2s_postclose_real",
            "bridge_root": "/Volumes/qmt_bridge/pr49_probe",
            "broker_environment": "REAL",
            "allow_real_money": True,
            "default_mode": "LIVE",
            "execution_session": "AFTER_HOURS_FIXED_PRICE",
            "close_auction_price_type": 49,
            "submit_after": "15:00:05",
            "cancel_at": "15:28:00",
            "finalize_at": "15:30:00",
            "snapshot_after": "15:31:00",
            "max_orders_per_day": 100,
        },
    }
    config["live"].update(changes)
    path = tmp_path / "probe.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_live_config(path, project_root=tmp_path)


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
    assert cfg["monitor"]["benchmark_name"] == "中证1000"
    assert cfg["account"]["opening_cash"] == pytest.approx(9_949_714.06)
    assert cfg["account"]["opening_value_adjustment"] == pytest.approx(
        -681_126.98
    )
    assert cfg["handler"]["class"] == "Alpha158Technical"
    assert cfg["handler"]["feature_groups"] == ["range"]
    assert cfg["strategy"] == {
        "class": "TopkDropoutStrategy",
            "topk": 22,
        "n_drop": 2,
        "initial_buy_count": 2,
            "risk_degree": 0.90,
            "hold_thresh": 2,
        "only_tradable": False,
        "forbid_all_trade_at_limit": False,
    }
    assert cfg["live"]["broker_environment"] == "SIMULATION"
    assert cfg["live"]["allow_real_money"] is False
    assert cfg["live"]["default_mode"] == "SIMULATE"
    assert cfg["live"]["execution_session"] == "CLOSE_AUCTION"
    assert cfg["live"]["close_auction_price_type"] == 11
    assert cfg["live"]["submit_after"] == "14:57:05"
    assert cfg["web"] == {"host": "127.0.0.1", "port": 8081}
    assert "schedule" not in cfg
    assert cfg["storage"]["db_path"].endswith(
        "csi1000_b6m_b2s_postclose.db"
    )
    assert cfg["provenance"]["strategy_baseline_config"].endswith(
        "baseline-strategy/b4-s/topk-t22-d2-h2_csi1000_full.yaml"
    )
    assert cfg["provenance"]["strategy_baseline_sha256"] == (
        "73290d5981c955b6d2f860667e1c4ae9fa27f8c896cbf25783a2810b333df93a"
    )


def test_load_csi1000_real_config_is_isolated_and_live_only():
    cfg = load_live_config(REAL_LIVE_PATH, project_root=REPO_ROOT)

    assert cfg["account"] == {
        "opening_cash": 1_000_000.0,
        "opening_value_adjustment": 0.0,
    }
    assert cfg["live"]["strategy_id"] == (
        "csi1000_b6m_b2s_postclose_real"
    )
    assert cfg["live"]["broker_environment"] == "REAL"
    assert cfg["live"]["allow_real_money"] is True
    assert cfg["live"]["default_mode"] == "LIVE"
    assert cfg["strategy"]["topk"] == 22
    assert cfg["strategy"]["hold_thresh"] == 2
    assert cfg["strategy"]["risk_degree"] == pytest.approx(0.90)
    assert cfg["live"]["execution_session"] == "CLOSE_AUCTION"
    assert cfg["live"]["close_auction_price_type"] == 11
    assert cfg["live"]["submit_after"] == "14:57:05"
    assert cfg["storage"]["db_path"].endswith(
        "csi1000_b6m_b2s_postclose_real.db"
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
            "execution_session": "CLOSE_AUCTION",
            "close_auction_price_type": 11,
            "submit_after": "14:57:05",
            "cancel_at": "15:00:05",
            "finalize_at": "15:00:30",
            "snapshot_after": "15:01:00",
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
            "execution_session": "CLOSE_AUCTION",
            "close_auction_price_type": 11,
            "submit_after": "14:57:05",
            "cancel_at": "15:00:05",
            "finalize_at": "15:00:30",
            "snapshot_after": "15:01:00",
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
            "execution_session": "CLOSE_AUCTION",
            "close_auction_price_type": 11,
            "submit_after": "14:57:05",
            "cancel_at": "15:00:05",
            "finalize_at": "15:00:30",
            "snapshot_after": "15:01:00",
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


def _ladder_config():
    return load_live_config(LADDER_LIVE_PATH, project_root=REPO_ROOT)


def test_observation_ladder_is_k1h5_full_risk_three_hundred_thousand():
    config = load_live_config(OBSERVATION_LADDER_LIVE_PATH, project_root=REPO_ROOT)

    assert config["strategy"]["class"] == "CohortLadderStrategy"
    assert config["strategy"]["topk"] == 1
    assert config["strategy"]["horizon"] == 5
    assert config["strategy"]["risk_degree"] == 1.0
    assert config["account"]["opening_cash"] == 300_000.0
    assert config["live"]["execution_session"] == "AFTER_HOURS_FIXED_PRICE"
    assert config["live"]["strategy_id"] == "alla_v4_ladder_k1h5_postclose_real"


def test_ladder_live_config_matches_bt_v4_parameters():
    config = _ladder_config()

    assert config["strategy"]["class"] == "CohortLadderStrategy"
    assert config["strategy"]["topk"] == 3
    assert config["strategy"]["horizon"] == 5
    assert config["strategy"]["risk_degree"] == 0.90
    assert config["strategy"]["only_tradable"] is False
    assert config["strategy"]["forbid_all_trade_at_limit"] is False
    assert config["data"]["instruments"] == "all"
    assert config["live"]["execution_session"] == "AFTER_HOURS_FIXED_PRICE"
    assert config["live"]["strategy_id"] == "alla_v4_ladder_k3h5_postclose_real"


def test_ladder_live_config_declares_five_ensemble_members():
    config = _ladder_config()

    members = config["model"]["members"]
    assert [m["seed"] for m in members] == [42, 1000, 2000, 3000, 4000]
    assert config["model"]["ensemble"] == "daily_zscore_mean"
    for member in members:
        assert len(member["sha256"]) == 64
        assert (REPO_ROOT / member["model_path"]).is_file()


def test_ladder_live_config_member_hashes_match_the_tracked_artifacts():
    """配置里的哈希必须与磁盘上的 artifact 一致，否则加载时才 fail-closed。"""
    import hashlib

    for member in _ladder_config()["model"]["members"]:
        payload = (REPO_ROOT / member["model_path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == member["sha256"], member


def test_ladder_live_config_declares_all_four_universe_filters():
    spec = _ladder_config()["universe_filter"]

    assert spec["st_daily"] == "scripts/data_collector/tushare/st_daily.csv"
    assert spec["min_amount"] == 10_000_000
    assert spec["min_listing_days"] == 60
    assert spec["min_recent_trading_days"] == 60
    assert spec["pool"] == "all"


def test_ladder_live_config_marks_live_only_deviations():
    strategy = _ladder_config()["strategy"]

    assert strategy["netting"] == "live_only"
    assert strategy["absorb_broker_excess"] == "live_only"
    assert strategy["no_buyable_substitution"] == "live_only"


def test_live_filter_pipe_entries_build_real_qlib_filters():
    """NameDFilter.from_config 直接下标取 filter_start_time / filter_end_time，
    缺键即 KeyError。必须真的构造一次，只断言 kwarg 被透传是查不出来的。"""
    from qlib.data import filter as qlib_filter

    entries = _ladder_config()["handler"]["filter_pipe"]
    assert entries, "ladder config must declare a filter_pipe"
    for entry in entries:
        builder = getattr(qlib_filter, entry["filter_type"])
        assert builder.from_config(entry) is not None


def test_live_filter_pipe_matches_the_bt_v4_backtest_verbatim():
    import yaml

    baseline = (
        REPO_ROOT / "backtest" / "configs" / "regime-adapt" / "phase-s"
        / "bt_m0h20rankices_all_ladder_k3h5_ensemble.yaml"
    )
    backtest = yaml.safe_load(baseline.read_text(encoding="utf-8"))

    assert (
        _ladder_config()["handler"]["filter_pipe"]
        == backtest["data"]["handler"]["instruments"]["filter_pipe"]
    )


@pytest.mark.parametrize(
    "name",
    [
        "alla_v4_ladder_k1h5_postclose_real",
        "alla_v4_ladder_k3h5_postclose_real",
        "csi1000_pr49_one_lot_probe",
    ],
)
def test_after_hours_configs_declare_the_adaptive_submission_start(name):
    """两个引用 AFTER_HOURS_FIXED_PRICE 的配置必须跟着 profile 一起改，
    否则 live_config 的逐项比对会把它们 fail-closed 掉。"""
    import yaml

    path = REPO_ROOT / "live_trading" / "configs" / (name + ".yaml")
    with open(path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    assert config["live"]["execution_session"] == "AFTER_HOURS_FIXED_PRICE"
    assert config["live"]["submit_after"] == "15:00:05"
