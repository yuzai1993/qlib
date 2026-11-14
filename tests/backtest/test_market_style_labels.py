import numpy as np
import pandas as pd
import pytest

from backtest.market_style.labels import make_targets, smooth_monthly, assign_semantics, aggregate_monthly


def test_monthly_aggregation_accepts_short_holiday_month_and_audits_initial_warmup():
    days = pd.bdate_range("2004-01-02", periods=13)
    daily = pd.DataFrame(0.01, index=days, columns=["mkt", "small_big", "lowvol_highvol", "momentum"])
    daily.loc[days[0], "lowvol_highvol"] = np.nan
    monthly, coverage = aggregate_monthly(daily)
    assert monthly.iloc[0].mkt == pytest.approx(0.13)
    assert monthly.iloc[0].lowvol_highvol == pytest.approx(0.12)
    assert coverage[0]["sessions"] == 13
    assert coverage[0]["valid_days"]["lowvol_highvol"] == 12
    daily.loc[days[5], "momentum"] = np.nan
    with pytest.raises(ValueError, match="internal missing"):
        aggregate_monthly(daily)


def test_targets_follow_next_trading_day_and_do_not_invent_holidays():
    days = pd.to_datetime(["2020-07-30", "2020-07-31", "2020-08-03", "2020-08-04"])
    monthly = pd.Series(["D", "P"], index=pd.period_range("2020-07", periods=2, freq="M"))
    result = make_targets(days, monthly)
    assert result.index.tolist() == days[:-1].tolist()
    assert result.label.tolist() == ["D", "P", "P"]
    assert result.target_date.tolist() == days[1:].tolist()


def test_centered_smoothing_preserves_center_when_three_way_tie():
    assert smooth_monthly(pd.Series([0, 1, 2, 2, 1])).tolist() == [0, 1, 2, 2, 1]
    assert smooth_monthly(pd.Series([0, 1, 0])).tolist() == [0, 0, 0]


def test_semantic_mapping_uses_centers_not_cluster_numbers():
    centers = pd.DataFrame(
        [
            [-0.2, 0.04, 0.1, 0.09, -0.06],
            [0.04, 0.01, -0.04, -0.03, 0.03],
            [0.12, 0.03, 0.06, 0, -0.08],
            [-0.01, 0.01, 0.03, 0.02, -0.02],
        ],
        index=[3, 1, 0, 2],
        columns=["mkt", "mkt_vol", "small_big", "lowvol_highvol", "momentum"],
    )
    assert assign_semantics(centers) == {3: "P", 1: "T", 0: "F", 2: "D"}
    centers.loc[3, "mkt"] = 0.2
    with pytest.raises(ValueError, match="panic"):
        assign_semantics(centers)
