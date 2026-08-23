import copy
from pathlib import Path

import pytest
import yaml

from live_trading.modules.backtest_parity import (
    ParityError,
    validate_backtest_parity,
    validate_configured_backtest,
)
from live_trading.modules.live_config import load_live_config

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_PATH = REPO_ROOT / "live_trading/configs/csi300_topk10_live.yaml"
BACKTEST_PATH = REPO_ROOT / "backtest/configs/csi300_live_parity.yaml"
NEW_LIVE_PATH = (
    REPO_ROOT / "live_trading/configs/csi1000_b6m_b2s_postclose.yaml"
)
NEW_BACKTEST_PATH = (
    REPO_ROOT / "backtest/configs/csi1000_b6m_b2s_postclose_parity.yaml"
)
REAL_LIVE_PATH = (
    REPO_ROOT / "live_trading/configs/csi1000_b6m_b2s_postclose_real.yaml"
)
REAL_BACKTEST_PATH = (
    REPO_ROOT / "backtest/configs/csi1000_b6m_b2s_postclose_real_parity.yaml"
)
LADDER_LIVE_PATH = (
    REPO_ROOT / "live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml"
)
LADDER_BACKTEST_PATH = (
    REPO_ROOT / "backtest/configs/alla_v4_ladder_k3h5_parity.yaml"
)


def _configs():
    live = load_live_config(LIVE_PATH, REPO_ROOT)
    backtest = yaml.safe_load(BACKTEST_PATH.read_text(encoding="utf-8"))
    return live, backtest


def _new_configs():
    live = load_live_config(NEW_LIVE_PATH, REPO_ROOT)
    backtest = yaml.safe_load(NEW_BACKTEST_PATH.read_text(encoding="utf-8"))
    return live, backtest


def _ladder_configs():
    live = load_live_config(LADDER_LIVE_PATH, REPO_ROOT)
    backtest = yaml.safe_load(LADDER_BACKTEST_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(live), copy.deepcopy(backtest)


def _set_path(mapping, path, value):
    current = mapping
    parts = path.split(".")
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


def test_real_live_and_designated_backtest_configs_match():
    live, backtest = _configs()

    validate_backtest_parity(live, backtest)


def test_new_csi1000_live_and_parity_configs_match():
    live, backtest = _new_configs()

    validate_backtest_parity(live, backtest)


def test_csi1000_real_live_and_designated_parity_configs_match():
    live = load_live_config(REAL_LIVE_PATH, REPO_ROOT)
    backtest = yaml.safe_load(
        REAL_BACKTEST_PATH.read_text(encoding="utf-8")
    )

    assert live["parity"]["backtest_config"] == str(
        REAL_BACKTEST_PATH.relative_to(REPO_ROOT)
    )
    validate_backtest_parity(live, backtest)


def test_parity_uses_economic_opening_value_after_account_adjustment():
    live, backtest = _new_configs()
    live = copy.deepcopy(live)
    backtest = copy.deepcopy(backtest)
    live["account"] = {
        "opening_cash": 9_949_714.06,
        "opening_value_adjustment": -681_126.98,
    }
    backtest["backtest"]["account"] = 9_268_587.08

    validate_backtest_parity(live, backtest)


@pytest.mark.parametrize(
    "side,path,value,reported_path",
    [
        ("live", "model.recorder_id", "wrong", "model.recorder_id"),
        ("live", "model.model_path", "wrong", "model.model_path"),
        ("live", "model.sha256", "0" * 64, "model.sha256"),
        ("live", "data.instruments", "csi500", "data.instruments"),
        ("live", "handler.fit_start_time", "2007-01-01", "handler.fit_start_time"),
        ("live", "strategy.topk", 20, "strategy.topk"),
        ("live", "strategy.risk_degree", 0.8, "strategy.risk_degree"),
        ("live", "strategy.only_tradable", True, "strategy.only_tradable"),
        (
            "live",
            "monitor.performance_baseline.opening_total_value",
            9_000_000.0,
            "backtest.account",
        ),
        ("live", "fees.commission_rate", 0.0003, "backtest.open_cost"),
        ("backtest", "backtest.exchange_kwargs.limit_threshold", 0.1,
         "exchange.limit_threshold"),
        ("backtest", "strategy.kwargs.forbid_all_trade_at_limit", True,
         "strategy.forbid_all_trade_at_limit"),
    ],
)
def test_parity_gate_reports_each_critical_drift(
    side, path, value, reported_path,
):
    live, backtest = _configs()
    live = copy.deepcopy(live)
    backtest = copy.deepcopy(backtest)
    _set_path(live if side == "live" else backtest, path, value)

    with pytest.raises(ParityError, match=reported_path.replace(".", r"\.")):
        validate_backtest_parity(live, backtest)


def test_live_config_points_to_designated_backtest():
    live, _ = _configs()

    assert live["parity"]["backtest_config"] == (
        "backtest/configs/csi300_live_parity.yaml"
    )


@pytest.mark.parametrize(
    "path,value,reported_path",
    [
        ("strategy.initial_buy_count", 3, "strategy.initial_buy_count"),
        ("handler.feature_groups", ["momentum"], "handler.feature_groups"),
        ("account.opening_cash", 600_000.0, "backtest.account"),
        ("account.opening_value_adjustment", 1.0, "backtest.account"),
    ],
)
def test_new_parity_gate_reports_initialization_and_handler_drift(
    path, value, reported_path,
):
    live, backtest = _new_configs()
    live = copy.deepcopy(live)
    _set_path(live, path, value)

    with pytest.raises(ParityError, match=reported_path.replace(".", r"\.")):
        validate_backtest_parity(live, backtest)


def test_publish_checks_parity_before_account_or_durable_side_effects(monkeypatch):
    from types import SimpleNamespace

    from live_trading.modules.backtest_parity import ParityError
    from live_trading.scripts import run_publish_signals as publish

    monkeypatch.setattr(
        publish,
        "parse_args",
        lambda: SimpleNamespace(
            config="test", trade_date="2026-07-23", mode="SIMULATE",
            dry_run=True, seq=1,
        ),
    )
    monkeypatch.setattr(
        publish,
        "load_live_config",
        lambda *args: {"live": {"strategy_id": "test"}},
    )
    monkeypatch.setattr(
        publish,
        "validate_configured_backtest",
        lambda *args: (_ for _ in ()).throw(ParityError("drift")),
    )
    monkeypatch.setattr(
        publish,
        "resolve_account_id",
        lambda *args: pytest.fail("account resolution ran before parity gate"),
    )

    with pytest.raises(ParityError, match="drift"):
        publish.main()


def test_ladder_pair_passes_parity_as_shipped():
    live, backtest = _ladder_configs()

    validate_backtest_parity(live, backtest)


def test_ladder_config_resolves_its_parity_backtest_from_disk():
    live, _ = _ladder_configs()

    assert (
        validate_configured_backtest(live, REPO_ROOT) == LADDER_BACKTEST_PATH
    )


def test_ladder_horizon_mismatch_is_caught():
    live, backtest = _ladder_configs()
    backtest["strategy"]["horizon"] = 4

    with pytest.raises(ParityError, match=r"strategy\.horizon"):
        validate_backtest_parity(live, backtest)


def test_ladder_never_compares_topk_dropout_only_fields():
    """真阶梯没有 n_drop / hold_thresh；比对它们只会拿 <missing> 和 <missing>
    相等，看似通过实则什么都没查，还会掩盖配置里真的写错了这些字段的情况。"""
    live, backtest = _ladder_configs()
    live["strategy"]["n_drop"] = 99

    validate_backtest_parity(live, backtest)


def test_unknown_strategy_class_is_rejected_not_silently_passed():
    live, backtest = _ladder_configs()
    live["strategy"]["class"] = "SomeFutureStrategy"
    backtest["strategy"]["class"] = "SomeFutureStrategy"

    with pytest.raises(ParityError, match="unknown strategy class"):
        validate_backtest_parity(live, backtest)


def test_member_set_is_order_insensitive_but_content_sensitive():
    live, backtest = _ladder_configs()
    backtest["parity"]["model_members"] = list(
        reversed(backtest["parity"]["model_members"])
    )

    validate_backtest_parity(live, backtest)

    backtest["parity"]["model_members"][0]["sha256"] = "0" * 64
    with pytest.raises(ParityError, match=r"model\.members"):
        validate_backtest_parity(live, backtest)


def test_member_count_mismatch_is_caught():
    live, backtest = _ladder_configs()
    backtest["parity"]["model_members"] = (
        backtest["parity"]["model_members"][:4]
    )

    with pytest.raises(ParityError, match=r"model\.member_count"):
        validate_backtest_parity(live, backtest)


def test_ensemble_live_against_single_model_backtest_fails_closed():
    live, backtest = _ladder_configs()
    del backtest["parity"]["model_members"]
    backtest["parity"]["model_path"] = "whatever"

    with pytest.raises(ParityError, match=r"model\.members"):
        validate_backtest_parity(live, backtest)


@pytest.mark.parametrize(
    "key,bad",
    [
        ("st_daily", "scripts/other.csv"),
        ("min_amount", 5_000_000),
        ("min_listing_days", 30),
        ("min_recent_trading_days", 30),
        ("pool", "csi1000"),
    ],
)
def test_each_universe_filter_key_is_compared(key, bad):
    live, backtest = _ladder_configs()
    backtest["universe_filter"][key] = bad

    with pytest.raises(ParityError, match=r"universe_filter\." + key):
        validate_backtest_parity(live, backtest)


def test_universe_filter_missing_on_one_side_only_fails_closed():
    live, backtest = _ladder_configs()
    del backtest["universe_filter"]

    with pytest.raises(ParityError, match="universe_filter"):
        validate_backtest_parity(live, backtest)


def test_after_hours_channel_requires_close_deal_price():
    """盘后固定价恒以收盘价撮合。回测改 vwap 则通道语义不再对应，必须 fail。"""
    live, backtest = _ladder_configs()
    backtest["backtest"]["exchange_kwargs"]["deal_price"] = "vwap"

    with pytest.raises(ParityError, match="deal_price"):
        validate_backtest_parity(live, backtest)


def test_execution_session_mismatch_is_caught():
    live, backtest = _ladder_configs()
    backtest["parity"]["execution_session"] = "CLOSE_AUCTION"

    with pytest.raises(ParityError, match="execution_session"):
        validate_backtest_parity(live, backtest)


def test_signal_price_type_is_derived_from_the_profile_not_trusted():
    """live 配置里没有 signal_price_type 字段；它必须由 profile 推出来再比，
    否则改了 execution_session 却忘了改 parity 配置就查不出来。"""
    live, backtest = _ladder_configs()
    backtest["parity"]["signal_price_type"] = "CLOSE_AUCTION_LIMIT"

    with pytest.raises(ParityError, match="signal_price_type"):
        validate_backtest_parity(live, backtest)


@pytest.mark.parametrize(
    "marker",
    ["netting", "absorb_broker_excess", "no_buyable_substitution"],
)
def test_live_only_deviation_missing_on_live_side_fails_closed(marker):
    live, backtest = _ladder_configs()
    del live["strategy"][marker]

    with pytest.raises(ParityError, match=marker):
        validate_backtest_parity(live, backtest)


@pytest.mark.parametrize(
    "marker",
    ["netting", "absorb_broker_excess", "no_buyable_substitution"],
)
def test_live_only_deviation_missing_on_backtest_side_fails_closed(marker):
    live, backtest = _ladder_configs()
    del backtest["parity"][marker]

    with pytest.raises(ParityError, match=marker):
        validate_backtest_parity(live, backtest)


_LEGACY_PAIRS = [
    (NEW_LIVE_PATH, NEW_BACKTEST_PATH),
    (REAL_LIVE_PATH, REAL_BACKTEST_PATH),
    (LIVE_PATH, BACKTEST_PATH),
]


@pytest.mark.parametrize("live_path,backtest_path", _LEGACY_PAIRS)
def test_shipped_topk_dropout_pairs_still_pass(live_path, backtest_path):
    live = load_live_config(live_path, REPO_ROOT)
    backtest = yaml.safe_load(backtest_path.read_text(encoding="utf-8"))

    validate_backtest_parity(live, backtest)


def test_topk_dropout_still_compares_its_own_fields():
    live = load_live_config(REAL_LIVE_PATH, REPO_ROOT)
    backtest = yaml.safe_load(REAL_BACKTEST_PATH.read_text(encoding="utf-8"))
    backtest["strategy"]["kwargs"]["hold_thresh"] = 99

    with pytest.raises(ParityError, match=r"strategy\.hold_thresh"):
        validate_backtest_parity(live, backtest)


def test_live_config_without_execution_session_still_passes():
    """csi300_topk10_live 没有 live.execution_session；两侧都没有才放行。"""
    live = load_live_config(LIVE_PATH, REPO_ROOT)
    backtest = yaml.safe_load(BACKTEST_PATH.read_text(encoding="utf-8"))

    assert "execution_session" not in live["live"]
    validate_backtest_parity(live, backtest)


def test_publish_price_universe_uses_same_stable_tie_break_as_strategy():
    import pandas as pd

    from live_trading.scripts.run_publish_signals import get_price_instruments

    scores = pd.Series(
        [1.0, 1.0, 1.0, 0.5],
        index=["SZ000002", "SH600001", "SH600000", "SH600003"],
    )

    instruments = get_price_instruments(
        scores,
        current_positions={"SZ000002": {"shares": 100}},
        topk=1,
    )

    assert instruments == ["SH600000", "SH600001", "SZ000002"]
