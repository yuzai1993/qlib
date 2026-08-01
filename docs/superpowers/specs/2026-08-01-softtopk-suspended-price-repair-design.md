# SoftTopk Suspended-Price Repair Design

## Goal

Repair the SoftTopk full-period portfolio NaN chain and rerun only the ten previously invalid CSI1000 strategy diagnostics from 2020-01-13 through 2026-07-31 with the frozen B1-M and B6-M predictions.

## Approved scope

- Keep the model artifacts, predictions, strategy grid, 500,000 account, fees, benchmark, dates, and metrics unchanged.
- Change only the holding-valuation behavior when the current deal price is missing or non-finite.
- Preserve the previous invalid attempts in the result audit trail.
- Do not select a winner, change a research baseline, or modify the live strategy.

## Considered approaches

1. **Recommended: fall back at the valuation source.** Treat both `None` and non-finite deal prices as unavailable and value the holding at its recorded position price. This matches the helper's existing contract and prevents NaN from entering portfolio value.
2. Guard only downstream cash and order amounts. This would mask the bad valuation and make the resulting allocation economically ambiguous.
3. Skip suspended holdings entirely. This would understate portfolio value and change strategy behavior, so it is rejected.

## Implementation

`_calculate_current_stock_value` will use the recorded holding price when `get_deal_price` returns `None`, `NaN`, or infinity. A regression test will reproduce the observed `NaN` return and must fail before the production change.

The stability runner will retain its normal rule that terminal `invalid` outcomes are not retried. A new explicit `--retry-invalid` switch will opt into repairing those rows. The registry finalizer will accept an explicit `--repair-reason` for a `complete -> complete` update, record the prior result path and SHA-256 in `repair_history`, and continue to reject accidental completed-row overwrites.

## Data flow and audit

The existing frozen full-period prediction artifacts and their manifest SHA-256 values remain the input. Each repaired row replaces the invalid row in `full_results.json`; `previous_attempts` retains the invalid result and error. The two existing registry entries are updated in place with new result hashes and an appended repair history. The standalone stability HTML is regenerated only from registry data.

## Verification

- Unit regression: `None`, `NaN`, and infinite deal prices use the recorded holding price.
- Runner regression: invalid rows are retried only with explicit opt-in.
- Registry regression: completed diagnostics require a repair reason and preserve the previous result identity.
- Experiment verification: all 40 rows are successful; every requested full-period metric is finite; each repaired row covers 1,587 trading dates and carries prior-attempt evidence.
- Cleanup verification: dry-run has no errors, then apply; only formal retained artifacts remain.

