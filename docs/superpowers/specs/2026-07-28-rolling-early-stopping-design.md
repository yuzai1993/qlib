# Annual Rolling Retrain Early-Stopping Experiment Design

## Objective

Determine whether `early_stopping_rounds` improves the existing B5-M annual
expanding rolling-retrain experiment, and distinguish an ineffective
early-stopping treatment from one that simply never triggers.

## Frozen Comparison

- Research baseline: B5-M (`B5 v1.0`).
- Direct control: `train-schedule/expanding-annual`.
- Treatment: `train-schedule/expanding-annual-es5`.
- Phase: M only; no strategy or portfolio changes.
- Seeds: `[42, 1000, 2000, 3000, 4000]`.
- Training pool: CSI1000.
- Evaluation pools: CSI1000, CSI300, CSI500.
- Test window: `2021-07-16` through `2026-07-16`.
- Rolling design: expanding train window, 252-trading-day step, shifted
  fixed-length valid window, and the existing 41-trading-day H40 purge.
- Model, Alpha158+range features, H40+CSRankNorm label, and all other
  hyperparameters remain identical to the no-early-stopping control.

The only treatment is:

```yaml
model:
  kwargs:
    early_stopping_rounds: 5
```

The maximum boosting rounds remain `epochs: 28`. The value is locked before
test evaluation and will not be selected from test results.

## Measurements

The canonical fixed one-day evaluation label remains:

```text
Ref($close, -2)/Ref($close, -1) - 1
```

Primary treatment comparison:

- CSI1000 five-seed mean RankIC versus the no-early-stopping annual rolling
  control.
- CSI1000 seed-paired RankIC wins versus that control.

Secondary checks:

- RankICIR and all-three-pool RankIC versus the rolling control.
- RankIC and RankICIR versus B5-M, which remains the formal registry baseline.
- Per-fold and per-calendar-year RankIC deltas.
- Each fold model's three DoubleEnsemble booster `best_iteration` values.
- Early-stopping trigger rate, where a booster triggers when
  `best_iteration < 28`.

## Decision Rule

- If ES5 improves mean RankIC and is directionally consistent across paired
  seeds, report that early stopping helps the rolling treatment, even if the
  resulting candidate still fails to beat B5-M.
- If ES5 does not improve the rolling control and early stopping triggered,
  conclude that early stopping is not beneficial for this design.
- If ES5 does not improve the rolling control and no booster triggered,
  conclude more narrowly that early stopping is redundant under the current
  28-round cap.
- No outcome promotes the candidate or changes live B1 automatically.

## Artifact and Cleanup Policy

Pre-register the experiment before training. Store the canonical evaluation
JSON, trigger diagnostics, registry row, configs, and generated report.
Because this is a diagnostic follow-up and a candidate is retained only if it
qualifies under the experiment standard, remove its heavy `mlruns/` and
`backtest/result/` artifacts after finalization when it does not qualify.
