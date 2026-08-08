from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from beta_overlay_core import (  # noqa: E402
    apply_beta_overlay,
    discrete_lots,
    overlay_from_gap,
    report_from_port,
    rolling_beta_lagged,
    slice_im_window,
)
from strategy_stability_metrics import summarize_period  # noqa: E402


def test_rolling_beta_is_lagged_one_day():
    idx = pd.bdate_range("2024-01-02", periods=80)
    rng = np.random.default_rng(0)
    bench = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
    net = 0.5 * bench + rng.normal(0, 0.005, len(idx))
    beta = rolling_beta_lagged(net, bench, window=60)
    assert pd.isna(beta.iloc[59])  # 需要 60 点 + shift
    assert pd.notna(beta.iloc[60])


def test_overlay_port_equals_net_plus_gap_times_fut():
    idx = pd.bdate_range("2024-01-02", periods=5)
    net = pd.Series([0.01] * 5, index=idx)
    bench = pd.Series([0.02] * 5, index=idx)
    fut = pd.Series([0.03] * 5, index=idx)
    port = overlay_from_gap(net, gap=pd.Series([0.4] * 5, index=idx), fut_ret=fut)
    assert abs(port.iloc[0] - (0.01 + 0.4 * 0.03)) < 1e-12


def test_discrete_lots_round():
    lots = discrete_lots(
        gap=pd.Series([0.5]),
        account_value=pd.Series([2_800_000.0]),
        settle=pd.Series([7000.0]),
        multiplier=200,
    )
    # 0.5*2.8e6 / (7000*200) = 1.0
    assert int(lots.iloc[0]) == 1
    assert pd.api.types.is_integer_dtype(lots.dtype)


def test_slice_im_window():
    idx = pd.to_datetime(["2022-07-21", "2022-07-22", "2026-07-31", "2026-08-01"])
    frame = pd.DataFrame({"x": [1, 2, 3, 4]}, index=idx)
    out = slice_im_window(frame)
    assert list(out.index.date) == [
        pd.Timestamp("2022-07-22").date(),
        pd.Timestamp("2026-07-31").date(),
    ]


def test_report_from_port_is_accepted_by_stability_metrics():
    idx = pd.bdate_range("2024-01-02", periods=4)
    base = pd.DataFrame(
        {
            "return": [0.0] * 4,
            "cost": [0.0] * 4,
            "bench": [0.001] * 4,
            "turnover": [0.1] * 4,
        },
        index=idx,
    )
    report = report_from_port(pd.Series([0.001, 0.002, -0.001, 0.003], index=idx), base)
    summary = summarize_period(report)
    assert summary["n_days"] == 4
    assert summary["sharpe_ratio"] is not None
