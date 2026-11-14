import numpy as np
import pandas as pd
from backtest.market_style.data import mask_index_prelaunch


def test_index_backfilled_history_is_not_available_before_publication():
    dates = pd.to_datetime(["2014-10-16", "2014-10-17", "2014-10-20"])
    series = pd.Series([99.0, 100.0, 101.0], index=dates)
    masked = mask_index_prelaunch(series, "SH000852")
    assert pd.isna(masked.iloc[0])
    assert masked.iloc[1:].tolist() == [100.0, 101.0]


from backtest.market_style.data import daily_aggregates, expand_features, read_bin_column


def test_binary_calendar_offset_is_respected(tmp_path):
    path = tmp_path / "close.day.bin"
    np.array([2, 10, 11, 12], dtype="<f4").tofile(path)
    result = read_bin_column(path, 6)
    np.testing.assert_allclose(result, [np.nan, np.nan, 10, 11, 12, np.nan], equal_nan=True)


def synthetic_panel():
    days = pd.bdate_range("2019-01-01", periods=80)
    names = ["SH600001", "SZ000001", "SZ000002", "SZ300001"]
    t = np.arange(len(days))[:, None]
    close = pd.DataFrame(10 * np.exp(t * np.array([0.001, -0.001, 0.002, -0.002])), index=days, columns=names)
    amount = pd.DataFrame(np.tile([1e7, 2e7, 3e7, 4e7], (len(days), 1)), index=days, columns=names)
    return {"close": close, "amount": amount, "open": close * 0.999, "high": close * 1.01, "low": close * 0.99}


def test_realized_spread_uses_previous_close_ranking():
    panel = synthetic_panel()
    data = daily_aggregates(panel, min_stocks=2)
    # Four-stock fixture: ranks .25/.5/.75/1; upper >=.7 contains two stocks.
    expected = np.exp(0.001) - (np.exp(0.002) + np.exp(-0.002)) / 2
    assert abs(data.loc[data.index[40], "small_big"] - expected) < 1e-10
    panel["amount"].iloc[40, 0] = 1e12
    changed = daily_aggregates(panel, min_stocks=2)
    assert abs(changed.iloc[40].small_big - expected) < 1e-10


def test_future_prices_cannot_change_earlier_aggregate_or_features():
    panel = synthetic_panel()
    first = expand_features(daily_aggregates(panel, min_stocks=2))[0]
    changed = {k: v.copy() for k, v in panel.items()}
    changed["close"].iloc[60:] *= 1.5
    second = expand_features(daily_aggregates(changed, min_stocks=2))[0]
    pd.testing.assert_frame_equal(first.iloc[:60], second.iloc[:60])


def test_suspended_stock_is_not_treated_as_flat_return():
    panel = synthetic_panel()
    panel["close"].iloc[40, 0] = np.nan
    data = daily_aggregates(panel, min_stocks=2)
    assert data.iloc[40]["n_stocks"] == 3
