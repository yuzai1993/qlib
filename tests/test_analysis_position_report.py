import numpy as np
import pandas as pd

from qlib.contrib.report.analysis_position.report import _calculate_mdd, _calculate_report_data


def test_calculate_mdd_uses_relative_wealth_drawdown_from_initial_capital():
    cumulative_return = pd.Series([-0.10, 0.20, 0.05])

    actual = _calculate_mdd(cumulative_return)

    expected = pd.Series([-0.10, 0.0, -0.125])
    pd.testing.assert_series_equal(actual, expected)


def test_calculate_report_data_uses_relative_wealth_for_excess_returns():
    dates = pd.date_range("2024-01-02", periods=2, name="date")
    report = pd.DataFrame(
        {
            "return": [0.10, -0.05],
            "bench": [0.05, 0.02],
            "cost": [0.01, 0.01],
            "turnover": [0.20, 0.30],
        },
        index=dates,
    )

    actual = _calculate_report_data(report)

    portfolio_wealth = np.array([1.10, 1.10 * 0.95])
    net_portfolio_wealth = np.array([1.09, 1.09 * 0.94])
    benchmark_wealth = np.array([1.05, 1.05 * 1.02])
    np.testing.assert_allclose(
        actual["cum_ex_return_wo_cost"],
        portfolio_wealth / benchmark_wealth - 1,
    )
    np.testing.assert_allclose(
        actual["cum_ex_return_w_cost"],
        net_portfolio_wealth / benchmark_wealth - 1,
    )
