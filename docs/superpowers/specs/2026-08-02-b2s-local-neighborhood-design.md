# B2-S Local Neighborhood Strategy Experiment Design

## Objective

Search a tightly bounded TopkDropout hyperparameter neighborhood around the
current B2-S baseline without retraining B6-M and without using test data for
selection. The complete workflow must fit within five hours on the local
machine and remain restartable after interruption.

## Frozen protocol

- Model: B6-M seed4000 from `backtest/models/baselines/b6-m/manifest.json`.
- Baseline: B2-S v1.0, Top30 / d2 / h20 / risk 0.95.
- Account and costs: CNY 500,000 and the current Phase S exchange-cost contract.
- Selection data: CSI1000 valid, 2020-01-13 through 2021-07-15 only.
- Test data: CSI1000, CSI300, and CSI500, 2021-07-16 through 2026-07-31.
- Test remains closed until one valid winner is frozen. Existing registered
  B2-S test metrics are reused and are not rerun.

## Candidate grid and runtime

The preregistered grid contains 540 TopkDropout candidates:

- `topk`: 26, 28, 30, 32, 34;
- `n_drop`: 1, 2, 3, 4;
- `hold_thresh`: 12, 14, 16, 18, 20, 22, 24, 26, 28;
- `risk_degree`: 0.90, 0.95, 1.00.

Historical pred-only valid backtests take about 14 seconds each, giving an
estimated 2.1 hours for the grid. Prediction generation, three winner test
runs, verification, reporting, and cleanup keep the full workflow below the
four-to-five-hour ceiling.

## Robust valid selection

Raw best-of-540 IR would be too sensitive to a single grid spike. Each
candidate is therefore scored with the 25th percentile of after-cost excess IR
across itself and every available one-step axial neighbor in the four grid
dimensions. A candidate is eligible only when all of its expected axial
neighbors completed successfully with finite metrics.

Selection order is fixed before execution:

1. neighborhood IR 25th percentile, descending;
2. candidate's own after-cost excess IR, descending;
3. candidate's after-cost excess annualized return, descending;
4. candidate's after-cost maximum drawdown, descending;
5. annualized one-way turnover, ascending;
6. candidate ID, ascending.

## Execution and recovery

A dedicated runner writes the protocol before any backtest, verifies frozen
prediction hashes, writes each generated YAML, and checkpoints valid rows after
every candidate. Resume skips already successful candidate IDs. Non-finite
metrics are invalid outcomes. Once the grid is complete, the runner freezes the
winner and runs that one candidate once on each test pool.

## Artifacts and reporting

The experiment lives under
`backtest/experiments/strategy-neighborhood/20260802_b2s_local/`. It stores the
protocol, prediction manifest, full valid results, frozen winner test results,
and an HTML report whose first table is the B2-S baseline. The canonical
registry receives one `strategy-neighborhood-b2-s` row with
`baseline_ref=B2-S v1.0`; the canonical experiment HTML is regenerated.

After verification, the standard artifact cleanup is run in dry-run and apply
modes. The tracked summaries remain reproducible even if transient MLflow and
result directories are removed.
