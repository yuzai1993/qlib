import copy
from pathlib import Path

import pytest
import yaml

from live_trading.modules.backtest_parity import (
    ParityError,
    validate_backtest_parity,
)
from live_trading.modules.live_config import load_live_config

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_PATH = REPO_ROOT / "live_trading/configs/csi300_topk10_live.yaml"
BACKTEST_PATH = REPO_ROOT / "backtest/configs/csi300_live_parity.yaml"


def _configs():
    live = load_live_config(LIVE_PATH, REPO_ROOT)
    backtest = yaml.safe_load(BACKTEST_PATH.read_text(encoding="utf-8"))
    return live, backtest


def _set_path(mapping, path, value):
    current = mapping
    parts = path.split(".")
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


def test_real_live_and_designated_backtest_configs_match():
    live, backtest = _configs()

    validate_backtest_parity(live, backtest)


@pytest.mark.parametrize(
    "side,path,value,reported_path",
    [
        ("live", "model.recorder_id", "wrong", "model.recorder_id"),
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
