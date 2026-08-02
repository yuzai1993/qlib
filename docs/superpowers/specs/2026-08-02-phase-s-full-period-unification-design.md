# Phase S Full-Period Unification Design

## Goal

Replace the short Phase S valid-only selection window with one continuous CSI1000 full-history comparison from 2020-01-13 through 2026-07-31, and make `backtest/experiments/strategy_stability_report.html` the single Phase S report.

## Scope and interpretation

- Phase M remains unchanged: its train, valid, test, five-seed, IC, and RankIC rules continue to apply.
- Phase S continues to freeze the tracked B6-M seed-4000 artifact and never retrains the model.
- Phase S no longer treats 2021-07-16 through 2026-07-31 as an unopened holdout. Strategy parameters may be compared and selected on the combined 2020-01-13 through 2026-07-31 interval.
- Every Phase S result and report must label this as a full-history comparison, not an out-of-sample estimate.
- The primary research pool remains CSI1000. Cross-pool runs are optional diagnostics unless a future experiment protocol explicitly requires them.
- The research baseline remains B2-S until the user explicitly promotes another strategy. This migration does not change live B1/B1-S configuration.

## Experiment migration

The completed B2-S neighborhood experiment is rerun from the frozen B6-M full-period prediction bundle. Its immutable grid remains:

- `topk`: 26, 28, 30, 32, 34
- `n_drop`: 1, 2, 3, 4
- `hold_thresh`: 12, 14, 16, 18, 20, 22, 24, 26, 28
- `risk_degree`: 0.90, 0.95, 1.00

All 540 candidates and B2-S are evaluated as continuous portfolios on CSI1000 from 2020-01-13 through 2026-07-31. Selection uses the same deterministic axial-neighborhood robustness rule: the 25th percentile of after-cost excess IR across the candidate and its available one-step axial neighbors, followed by own IR, annualized excess return, maximum drawdown, turnover, and candidate ID tie-breaks.

There is no second test-opening phase. The selected candidate is an in-sample full-history research winner and is not automatically promoted.

## Reporting architecture

`build_strategy_stability_report.py` becomes the only Phase S HTML renderer. It reads all `phase: S` registry rows and produces:

1. Current B2-S baseline summary first.
2. Existing B6-M continuous full-period stability results.
3. The rerun 540-point B2-S neighborhood experiment, including the frozen full-period winner and robust ranking.
4. Other Phase S audit/diagnostic registrations, with links to tracked JSON summaries when their metric schema differs.

The report displays after-cost excess IR, after-cost excess annualized return, after-cost excess maximum drawdown, Sharpe ratio, Calmar ratio, annualized volatility, turnover, and relevant stability summaries when available. It does not imply holdout performance.

`strategy_neighborhood_report.html` stops being an active report target. Its information is incorporated into the unified report; historical registry rows remain auditable.

## Registry and protocol

New or migrated Phase S rows record:

- `evaluation_mode: full_history_in_sample`
- `selection_segment: [2020-01-13, 2026-07-31]`
- `selection_pool: csi1000`
- frozen model, prediction, protocol, base-config, and effective-config SHA-256 identities
- `cleanup_retention_eligible: false` unless explicitly promoted later
- a note that the result is not an out-of-sample estimate

Completed experiment records remain immutable. The rerun uses a new experiment ID/version rather than overwriting the previous valid/test audit row.

## Safety and resumability

- The full prediction artifact must cover the exact period and match its manifest hash before any candidate is reused or run.
- Checkpoints bind protocol, prediction, and base-config hashes and validate each candidate's effective config hash.
- Successful candidates resume safely; stale or mismatched checkpoints fail closed.
- Finalization independently verifies the exact 540-candidate set and recomputes the winner.
- Generated backtest sessions and MLflow artifacts are cleaned after tracked summaries and the unified report are finalized.

## Verification

- TDD coverage for the new Phase S time contract, no-holdout runner, checkpoint identity, full-period winner recomputation, unified report ordering, and registry immutability.
- Focused Phase S tests plus the complete `tests/backtest` suite.
- Registry-to-artifact SHA checks, no temporary absolute paths, `git diff --check`, and an independent code review before merge.

