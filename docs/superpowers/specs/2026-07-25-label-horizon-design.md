# Label Horizon Experiment Design

## Objective

Investigate whether the B1 model's one-day training label is mismatched with
the intended low-turnover `TopkDropout(topk=30, n_drop=1)` strategy, and test
whether labels spanning the strategy's observed holding horizon improve model
quality.

This is a Phase M experiment. It changes labels only. It does not run portfolio
backtests, compare returns, or tune strategy parameters.

## Fixed Experiment Protocol

- Baseline: `B1 v1.0`.
- Train pool: CSI1000.
- Test pools: CSI300, CSI500, CSI1000.
- Seeds: `[42, 1000, 2000, 3000, 4000]`.
- Train: 2016-01-02 through 2020-01-10.
- Valid: 2020-01-13 through 2021-07-15.
- Test: 2021-07-16 through 2026-07-16.
- Features: Alpha158.
- Model: the B1 LightGBM model and hyperparameters.
- Training mode: `train_only`.
- Formal model-selection metric: mean test RankIC against the fixed one-day
  evaluation label, followed by RankICIR, using the existing protocol.
- The B1 strategy and all portfolio-performance metrics are out of scope.

## Research Basis

The repository's current label predicts the close-to-close return from the
next tradable close to the following close:

```text
Ref($close, -2) / Ref($close, -1) - 1
```

The intended target strategy retains 29 of 30 names on a typical unconstrained
day. Its mechanical replacement rate is therefore about 1/30 per day, but the
actual holding distribution depends on score persistence and can have a long
right tail. A fixed one-day label may favor immediate payoff even when a
selected name remains in the portfolio for several weeks.

External work supports treating forecast horizon as an empirical design
choice rather than assuming that the shortest horizon is optimal:

- Nechvátalová (2024) reports a trade-off between short-horizon predictability,
  turnover, and after-cost performance, and studies forecasts at multiple
  horizons:
  https://doi.org/10.32065/CJEF.2024.02.01
- Ballarin, Capra, and Dellaportas (2025) explicitly estimate separate
  multi-horizon return forecasts and require a horizon-sized temporal buffer
  to avoid leakage:
  https://arxiv.org/abs/2504.19623
- van Binsbergen et al.'s alpha-decay study documents that forecast horizons
  and realized holding periods can differ because positions are accumulated
  and unwound gradually:
  https://business.columbia.edu/faculty/research/alpha-decay

These results motivate testing the hypothesis; they do not establish that a
longer label will improve this A-share model.

## Stage 0: Holding-Duration Diagnostic

Use each of the five existing B1 models to generate valid-segment scores for
CSI300. Replay only the deterministic instrument-selection state transition:

```text
TopkDropout(topk=30, n_drop=1)
```

The replay ignores capital, position weights, prices, fees, tradability, and
portfolio returns. It is a holding-state diagnostic, not a backtest.

For every seed, and for the pooled set of completed holding spells, report:

- count of completed and right-censored spells;
- mean, median, P75, P90, and maximum completed duration;
- fractions held at least 5, 10, 20, 30, 40, and 60 trading days;
- the empirical survival curve `S(k) = P(T >= k)`.

Right-censored spells at the valid-segment end must not be treated as completed.
Use a Kaplan-Meier-style risk-set estimate for the survival curve so censoring
does not bias long-duration survival downward.

## Stage 1: Cumulative-Horizon Scan

Map the pooled valid-segment P50, P75, and P90 holding durations to the nearest
distinct member of:

```text
{5, 10, 20, 30, 40, 60}
```

Ties are resolved toward the shorter horizon. If two quantiles map to the same
anchor, fill the missing candidate with the closest unused anchor to the
duplicated raw quantile. Record the raw quantiles and deterministic mapping in
the experiment manifest before training.

For each selected horizon `H`, train:

```text
Ref($close, -(H+1)) / Ref($close, -1) - 1
```

Each candidate uses all five fixed seeds. The hypothesis for every candidate
must be written to the registry staging manifest before its first training run.

## Stage 2: Survival-Weighted Label

Before inspecting any Stage 1 test metrics, freeze one additional label from
the Stage 0 valid survival curve:

```text
y_t = sum(k=1..Hmax, w_k * r_{t+k})
w_k = S(k) / sum(j=1..Hmax, S(j))
r_{t+k} = Ref($close, -(k+1)) / Ref($close, -k) - 1
```

`Hmax` is the largest Stage 1 anchor. The expression is expanded explicitly
because Qlib rolling operators do not support a forward window.

This label approximates expected contribution while a newly bought name
remains held: near-term returns receive high weight, while distant returns are
weighted by the probability that the position survives to that age.

Train this candidate with the same five seeds.

## Leakage and Boundary Control

An `H`-day future label makes the last `H` observations in train and valid
depend on prices in the following segment. The experiment must preserve the
official segment boundaries in configuration while excluding boundary samples
whose label end lies outside their segment.

- Training preparation excludes the last `H` trading dates of train.
- Early-stopping validation excludes the last `H` trading dates of valid.
- This masking is label-specific and must not alter feature availability.
- Add automated tests proving that no retained sample's label end exceeds the
  segment end.

For the one-day B1 comparator, retain the official published B1 metrics. Do not
replace or promote the baseline based on a purged re-estimate.

## Dual-Label Evaluation

Every candidate is evaluated twice on all three test pools:

1. `eval_1d`: the fixed protocol label
   `Ref($close, -2)/Ref($close, -1)-1`.
2. `eval_self`: the candidate's own training label.

Both rows report:

- RankIC;
- RankICIR;
- IC;
- ICIR;
- number of evaluated dates;
- five-seed mean and RankIC standard deviation.

Only `eval_1d` is comparable to B1 and eligible for the formal Phase M decision.
`eval_self` is diagnostic and must be visually marked as non-baseline-comparable.

The `eval_1d` row uses the complete test interval. All `eval_self` rows use one
common test end date, obtained by moving backward from 2026-07-16 by the
largest horizon in the batch. This ensures that every self-label row uses the
same dates and does not require post-test prices that are unavailable.

## Valid-First Execution Gate

The execution order prevents test-driven iteration:

1. Generate Stage 0 valid diagnostics.
2. Freeze Stage 1 anchors, Stage 2 weights, label expressions, hypotheses, and
   the common self-label evaluation window in a tracked manifest.
3. Train all Stage 1 and Stage 2 candidates.
4. Evaluate all candidates on valid and complete operational checks.
5. Only after the complete design is frozen, run the final test evaluations.
6. Do not create or alter candidates after viewing test results.

## Registry and HTML Report

Use direction `label-design` and `baseline_ref: "B1 v1.0"`.

Each candidate remains one registry experiment with a structured
`metrics_by_eval_label` object containing `eval_1d` and `eval_self`. The HTML
report renders two rows per experiment:

- fixed one-day evaluation first;
- self-label evaluation second.

The label-design table begins with the B1 baseline row. Best-value highlighting
for formal comparisons considers only baseline and `eval_1d` rows; self-label
rows use separate diagnostic styling.

Failed and partial runs are registered, as required by the experiment
standard.

## Completion and Interpretation

For each candidate:

- compare three-pool `eval_1d` RankIC and RankICIR with B1;
- report CSI300 per-seed paired RankIC differences against B1;
- use `eval_self` only to explain horizon learnability;
- do not use portfolio-return language;
- do not promote a baseline without explicit user confirmation.

After updating the registry and generated HTML report, run the standard
artifact cleanup first in dry-run mode and then with `--apply`.

