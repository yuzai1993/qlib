"""Offline contract tests for post-sale-cash Topk execution."""
import dataclasses
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from live_trading.modules.order_manager import OrderManager
from live_trading.modules.live_config import load_live_config
from live_trading.modules.backtest_parity import validate_backtest_parity, ParityError
from live_trading.modules.signal_schema import BatchHeader, validate_batch, SchemaError
from live_trading.modules.signal_publisher import SignalPublisher
from live_trading.modules.fill_importer import LiveRecorder
from live_trading.scripts.run_publish_signals import build_order_planner

ROOT = Path(__file__).resolve().parents[2]
PRICE_TYPE = "CLOSE_AUCTION_TOPK_CASH_V1"


def guarded_config():
    config = yaml.safe_load((ROOT / "live_trading/configs/csi1000_b6m_b2s_postclose_real.yaml").read_text())
    config["strategy"].update(topk=5, n_drop=1, hold_thresh=1, force_sell_rank=100,
                              initial_buy_count=None, risk_degree=0.9,
                              execution_policy="post_sell_cash_v1", max_positions=5)
    return config


def test_force_rank_exits_all_five_and_preserves_full_odd_lot():
    config = guarded_config()
    scores = pd.Series(range(120, 0, -1), index=[f"SH600{i:03d}" for i in range(120)])
    held = {code: {"shares": 151} for code in scores.index[110:115]}
    intents = OrderManager(config).generate_orders(scores, held, 1, {}, 300000)
    sells = [order for order in intents if order["direction"] == "SELL"]
    assert len(sells) == 5
    orders = build_order_planner(config).plan(intents, {}, "b", "2026-10-29")
    assert [order.quantity for order in orders if order.side == "SELL"] == [151] * 5
    assert all(order.price_type == PRICE_TYPE for order in orders)
    assert all(order.max_positions == 5 and order.cash_risk_degree == .9 for order in orders)


def test_guarded_config_accepts_unstaged_initialization(tmp_path):
    path = tmp_path / "guarded.yaml"
    path.write_text(yaml.safe_dump(guarded_config()))
    assert load_live_config(path)["strategy"]["initial_buy_count"] is None


@pytest.mark.parametrize("key,value", [("execution_policy", "unknown"), ("max_positions", 6),
                                       ("max_positions", True), ("initial_buy_count", 1),
                                       ("risk_degree", 1.1), ("risk_degree", float("nan")),
                                       ("force_sell_rank", 0)])
def test_guarded_config_rejects_invalid_execution_contract(tmp_path, key, value):
    config = guarded_config()
    config["strategy"][key] = value
    path = tmp_path / "guarded.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError):
        load_live_config(path)


def test_guarded_force_exit_missing_score_ignores_holding_threshold():
    config = guarded_config()
    config["strategy"]["hold_thresh"] = 5
    scores = pd.Series([3., 2., 1.], index=["SH600000", "SH600001", "SH600002"])
    positions = {"SH600009": {"shares": 99, "opened_trade_date": "2026-10-28"}}
    orders = OrderManager(config).generate_orders(scores, positions, 1, {}, 300000,
                                                   signal_date="2026-10-28", trade_dates=["2026-10-28"])
    assert [o["instrument"] for o in orders if o["direction"] == "SELL"] == ["SH600009"]


def test_guarded_new_target_config_passes_designated_parity():
    path = ROOT / "live_trading/configs/alla_v2_top5d1f100_postclose_real.yaml"
    config = load_live_config(path)
    backtest = yaml.safe_load((ROOT / config["parity"]["backtest_config"]).read_text())
    validate_backtest_parity(config, backtest)


def test_parity_checks_force_rank_and_execution_contract():
    live = load_live_config(
        ROOT / "live_trading/configs/alla_v2_top5d1f100_postclose_real.yaml"
    )
    backtest = yaml.safe_load((ROOT / live["parity"]["backtest_config"]).read_text())
    validate_backtest_parity(live, backtest)
    for section, key, value in (("kwargs", "force_sell_rank", 99), ("parity", "max_positions", 6)):
        target = backtest["strategy"][section] if section == "kwargs" else backtest[section]
        original = target[key]
        target[key] = value
        with pytest.raises(ParityError, match=key):
            validate_backtest_parity(live, backtest)
        target[key] = original


def test_guarded_wire_and_durable_plan_keep_contract(tmp_path):
    config = guarded_config()
    orders = build_order_planner(config).plan([
        {"instrument": "SH600000", "direction": "BUY", "target_value": 54000},
    ], {}, "batch", "2026-10-29")
    header = BatchHeader("batch", "guarded", "2026-10-29", "2026-10-28", "STOCK", "t", 0, "")
    header, wire, _ = SignalPublisher._render(header, orders)
    encoded = json.loads(wire.splitlines()[1])
    assert encoded["cash_risk_degree"] == .9
    assert encoded["max_positions"] == 5
    recorder = LiveRecorder(str(tmp_path / "ledger.db"), opening_cash=300000)
    recorder.record_publish_plan(header, orders)
    saved = recorder.get_orders("batch")
    assert saved[0]["cash_risk_degree"] == .9
    assert saved[0]["max_positions"] == 5
    recorder.record_publish_plan(header, orders)
    changed = [dataclasses.replace(orders[0], cash_risk_degree=.8)]
    with pytest.raises(SchemaError, match="conflicts"):
        recorder.record_publish_plan(header, changed)


def test_batch_rejects_mixed_guarded_and_legacy_orders():
    orders = build_order_planner(guarded_config()).plan([
        {"instrument": "SH600000", "direction": "BUY", "target_value": 54000},
        {"instrument": "SZ000001", "direction": "BUY", "target_value": 54000},
    ], {}, "batch", "2026-10-29")
    orders[1] = dataclasses.replace(orders[1], price_type="CLOSE_AUCTION_LIMIT")
    header = BatchHeader("batch", "guarded", "2026-10-29", "2026-10-28", "STOCK", "t", 2, "")
    with pytest.raises(SchemaError, match="guard|cash|policy|contract"):
        validate_batch(header, orders)
