from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_im_continuous import select_active_contracts, settle_to_settle_returns  # noqa: E402


def test_select_active_picks_higher_volume():
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
            "合约代码": ["IM2401", "IM2402"],
            "成交量": [100, 500],
            "持仓量": [10, 20],
            "今结算": [5000.0, 4990.0],
        }
    )
    active = select_active_contracts(raw)
    assert list(active["contract"]) == ["IM2402"]


def test_settle_returns_use_held_contract_not_new_active():
    # Day1 hold IM2401; Day2 active switches to IM2402 but return still on IM2401
    panel = pd.DataFrame(
        {"IM2401": [100.0, 102.0, 101.0], "IM2402": [99.0, 100.0, 103.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )
    held = pd.Series(
        [pd.NA, "IM2401", "IM2402"],
        index=panel.index,
        dtype=object,
    )
    rets = settle_to_settle_returns(panel, held)
    assert abs(rets.iloc[1] - 0.02) < 1e-12  # 102/100-1
