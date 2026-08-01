# Strategy Stability Baseline Table Design

## Goal

Add a standalone current-baseline table at the beginning of
`backtest/experiments/strategy_stability_report.html`, before all stability
experiment tables. The rest of the B6-M full-period, yearly, and Top30
neighborhood sections remain unchanged.

## Data sources

The report builder will require exactly one current strategy baseline registry
row with `exp_id=baseline/b2-s-on-b6-m` and exactly one B6-M full-period
stability diagnostic row. Baseline identity and protocol fields come from the
registry baseline row; the six full-period performance metrics come from the
diagnostic result whose candidate ID matches the baseline strategy ID.

The table will display:

- baseline version and frozen model;
- pool and full-period date range;
- strategy parameters (`topk`, `n_drop`, `hold_thresh`);
- after-cost annualized return, Sharpe ratio, Calmar ratio, annualized
  volatility, maximum drawdown, and annualized one-way turnover.

Missing or ambiguous baseline inputs are errors rather than silently selecting
another row. Missing individual metric values render as an em dash, consistent
with the existing report.

## Rendering

The first report section is `当前策略 Baseline`, and its table is the first
`<table>` in the document. It contains one B2-S row and uses the existing metric
formatting rules. Existing stability sections retain their current ordering and
content.

## Verification

Extend `tests/backtest/test_build_strategy_stability_report.py` to verify that:

1. the first table is the baseline table;
2. it contains the B2-S/B6-M identity, period, parameters, and six metrics;
3. missing or duplicate current-baseline registry rows are rejected;
4. all existing full-period, yearly, and neighborhood assertions still pass.

Regenerate `backtest/experiments/strategy_stability_report.html` from the
registry after the tests pass.
