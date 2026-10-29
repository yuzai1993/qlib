"""A liquidation inventory is evidence for human review, never an order batch."""

import copy

import pandas as pd
import pytest

from live_trading.scripts.preview_bt_v5_cutover import build_liquidation_inventory


def test_inventory_keeps_all_old_holdings_and_odd_lots_without_assuming_proceeds():
    positions = {
        "SH600000": {"shares": 151, "avg_cost": 10.0},
        "SZ000001": {"shares": 200},
        "SZ000002": {"shares": 0},
    }
    before = copy.deepcopy(positions)
    scores = pd.Series({"SH600000": 9.0, "SH600001": 8.0, "SZ000001": float("nan")})
    result = build_liquidation_inventory(
        scores, positions, 12345.67, topk=5, risk_degree=1.0
    )
    assert positions == before
    assert result["manual_liquidation_inventory"] == [
        {
            "stock_code": "SH600000",
            "ledger_shares": 151,
            "broker_sellable_shares": None,
        },
        {
            "stock_code": "SZ000001",
            "ledger_shares": 200,
            "broker_sellable_shares": None,
        },
    ]
    assert result["cash_snapshot"] == 12345.67
    assert result["manual_liquidation_required"] is True
    assert result["post_liquidation_plan"]["available_cash"] is None
    assert result["post_liquidation_plan"]["buy_candidates"] == ["SH600000", "SH600001"]
    assert result["post_liquidation_plan"]["requires_confirmed_zero_holdings"] is True
    assert result["post_liquidation_plan"]["requires_no_open_orders"] is True
    assert result["publishable"] is False
    assert result["ready_for_activation"] is False
    assert "intents" not in result


def test_flat_snapshot_still_requires_broker_reconciliation_and_never_resets_cash():
    result = build_liquidation_inventory(
        pd.Series({"SH600000": 1.0}), {}, 56789.12, topk=5, risk_degree=1.0
    )
    assert result["manual_liquidation_inventory"] == []
    assert result["manual_liquidation_required"] is False
    assert result["cash_snapshot"] == 56789.12
    assert result["ready_for_activation"] is False
    assert result["post_liquidation_plan"]["available_cash"] is None


@pytest.mark.parametrize("shares", [-1, 1.5, True, float("nan")])
def test_inventory_rejects_invalid_ledger_quantities(shares):
    with pytest.raises(ValueError, match="shares"):
        build_liquidation_inventory(
            pd.Series(dtype=float),
            {"SH600000": {"shares": shares}},
            100.0,
            topk=5,
            risk_degree=1.0,
        )


@pytest.mark.parametrize("cash", [-1, float("nan"), float("inf"), True])
def test_inventory_rejects_invalid_cash(cash):
    with pytest.raises(ValueError, match="cash"):
        build_liquidation_inventory(
            pd.Series(dtype=float), {}, cash, topk=5, risk_degree=1.0
        )
