# Label Horizon Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure the valid-period holding distribution of the proposed Top30/Drop1 selector, train cumulative- and survival-horizon label variants, and report every model against both the fixed one-day label and its own label without running portfolio backtests.

**Architecture:** Add a pure holding-state diagnostic and label-design manifest generator, a `DatasetH` subclass that purges horizon-crossing train/valid samples, and a diagnostic evaluation-label override that leaves the official one-day evaluator default unchanged. Generate label-specific train-only YAML files from the frozen manifest, then extend registry rendering to show formal and self-label metric rows separately.

**Tech Stack:** Python 3.10, pandas, NumPy, Qlib `DatasetH`, LightGBM through Qlib, pytest, YAML, JSON/JSONL.

## Global Constraints

- Baseline is `B1 v1.0`; do not modify or promote it.
- Phase M only: no portfolio backtests or portfolio-return metrics.
- Train CSI1000; evaluate CSI300, CSI500, and CSI1000.
- Seeds are exactly `[42, 1000, 2000, 3000, 4000]`.
- Official segments remain train 2016-01-02–2020-01-10, valid 2020-01-13–2021-07-15, test 2021-07-16–2026-07-16.
- Formal comparison always uses `Ref($close, -2)/Ref($close, -1)-1`.
- Self-label metrics are diagnostic and cannot be used for baseline promotion.
- Do not inspect Stage 1 test metrics until Stage 0 outputs, Stage 1 horizons, Stage 2 weights, all hypotheses, and the common self-label evaluation window are frozen in the tracked manifest.
- Use `/opt/anaconda3/envs/qlib/bin/python`; never run Qlib multiprocessing code through stdin or a heredoc on macOS.

---

### Task 1: Pure Holding-Duration Diagnostic

**Files:**
- Create: `backtest/scripts/analyze_holding_duration.py`
- Test: `tests/backtest/test_analyze_holding_duration.py`

**Interfaces:**
- Consumes: B1 model sessions, `config_loader`, `select_topk_dropout`.
- Produces: `replay_holding_spells(scores: pd.Series, topk: int, n_drop: int) -> tuple[list[HoldingSpell], list[HoldingSpell]]`, `kaplan_meier_survival(completed: Sequence[int], censored: Sequence[int]) -> dict[int, float]`, and a JSON diagnostic.

- [ ] **Step 1: Write failing unit tests**

Test deterministic entry/exit durations, right-censoring at the final date,
Kaplan-Meier risk-set survival, five-seed aggregation, and nearest-anchor
mapping with shorter-horizon tie breaking.

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_analyze_holding_duration.py -q
```

Expected: import failure because `analyze_holding_duration.py` does not exist.

- [ ] **Step 3: Implement the pure replay and JSON CLI**

The CLI loads each B1 model, predicts CSI300 valid scores, replays
`topk=30,n_drop=1`, and writes per-seed and pooled duration statistics plus the
survival curve. It must never instantiate a portfolio executor or
`PortAnaRecord`.

- [ ] **Step 4: Run the focused test and verify pass**

Run the same pytest command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backtest/scripts/analyze_holding_duration.py tests/backtest/test_analyze_holding_duration.py
git commit -m "feat(backtest): analyze topk holding durations"
```

### Task 2: Horizon Labels and Frozen Manifest

**Files:**
- Create: `backtest/label_design/__init__.py`
- Create: `backtest/label_design/horizons.py`
- Create: `backtest/scripts/freeze_label_horizon_manifest.py`
- Test: `tests/backtest/test_label_horizons.py`
- Create at runtime: `backtest/experiments/label_horizon_manifest.json`

**Interfaces:**
- Consumes: Stage 0 JSON.
- Produces: `cumulative_label(horizon: int) -> str`,
  `survival_weighted_label(survival: Mapping[int, float], max_horizon: int) -> str`,
  `select_horizon_anchors(quantiles: Mapping[str, float], anchors: Sequence[int]) -> list[int]`,
  and an immutable manifest containing expressions, hypotheses, weights, and dates.

- [ ] **Step 1: Write failing tests**

Assert exact expressions for H=1 and H=20, normalized survival weights, stable
expression ordering, deterministic P50/P75/P90 anchor selection, and common
self-label end-date selection from a supplied trading calendar.

- [ ] **Step 2: Run the focused test and verify failure**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_label_horizons.py -q
```

- [ ] **Step 3: Implement expression and manifest generation**

The manifest records `baseline_ref`, data version, Stage 0 input hash, selected
anchors, maximum horizon, common self-label end date, cumulative expressions,
survival weights and expression, fixed seeds, fixed pools, and pre-registered
hypotheses.

- [ ] **Step 4: Run tests and verify pass**

- [ ] **Step 5: Commit**

```bash
git add backtest/label_design backtest/scripts/freeze_label_horizon_manifest.py tests/backtest/test_label_horizons.py
git commit -m "feat(backtest): freeze label horizon designs"
```

### Task 3: Purged Dataset for Multi-Day Labels

**Files:**
- Create: `backtest/label_design/dataset.py`
- Test: `tests/backtest/test_label_horizon_dataset.py`

**Interfaces:**
- Consumes: ordinary Qlib handler and official segment map.
- Produces: `PurgedHorizonDataset(DatasetH)` with kwargs
  `label_horizon: int` and `purge_segments: tuple[str, ...] = ("train", "valid")`.

- [ ] **Step 1: Write failing tests**

Use a fake handler/calendar to prove that train and valid end dates move back
by exactly `label_horizon` trading dates during `prepare`, test remains
unchanged, direct slice preparation remains unchanged, and the stored official
segment boundaries are not mutated.

- [ ] **Step 2: Run the focused test and verify failure**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_label_horizon_dataset.py -q
```

- [ ] **Step 3: Implement the minimal subclass**

Override named-segment preparation only. Obtain the calendar from Qlib, clone
the requested slice, and replace its end with the horizon-purged date.

- [ ] **Step 4: Run tests and verify pass**

- [ ] **Step 5: Commit**

```bash
git add backtest/label_design/dataset.py tests/backtest/test_label_horizon_dataset.py
git commit -m "feat(backtest): purge horizon labels at split boundaries"
```

### Task 4: Label-Design Config Generator

**Files:**
- Create: `backtest/scripts/generate_label_horizon_configs.py`
- Test: `tests/backtest/test_label_horizon_configs.py`
- Create at runtime: `backtest/configs/label-design/<variant>/*.yaml`

**Interfaces:**
- Consumes: `backtest/experiments/label_horizon_manifest.json` and the B1 config.
- Produces: one `train_only` config per candidate and seed using
  `PurgedHorizonDataset` and an explicit Alpha158 label.

- [ ] **Step 1: Write failing tests**

Assert that only label, dataset class/kwargs, note, seed, and identifying
comments differ from B1; all five seeds exist; strategy/backtest blocks are
irrelevant to `train_only`; dates, features, model hyperparameters, train pool,
and processors remain fixed.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_label_horizon_configs.py -q
```

- [ ] **Step 3: Implement the generator**

Generate directories and filenames from manifest variant IDs. Refuse to
overwrite configs whose parsed content differs unless `--force` is explicitly
provided.

- [ ] **Step 4: Run tests and verify pass**

- [ ] **Step 5: Commit**

```bash
git add backtest/scripts/generate_label_horizon_configs.py tests/backtest/test_label_horizon_configs.py
git commit -m "feat(backtest): generate label horizon configs"
```

### Task 5: Dual-Label IC Evaluation

**Files:**
- Modify: `backtest/scripts/eval_ic_multi_pool.py`
- Test: `tests/backtest/test_eval_ic_multi_pool.py`

**Interfaces:**
- Consumes: candidate config, five trained sessions, pools, segment, and an
  optional diagnostic evaluation expression/end date.
- Produces: the existing JSON schema plus `eval_label_role`, `eval_label`,
  `effective_eval_segment`, and per-seed `n_days`.

- [ ] **Step 1: Add failing tests**

Prove default CLI behavior remains the fixed one-day label, a diagnostic
`--eval-label` requires `--eval-label-role self`, a self-label end override is
accepted, and the fetched label plus dataset use the same effective end.

- [ ] **Step 2: Run tests and verify failure**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_eval_ic_multi_pool.py -q
```

- [ ] **Step 3: Implement the diagnostic override**

Keep `EVAL_LABEL_EXPR` as the default and reject custom expressions unless
explicitly marked `self`. Do not alter official one-day outputs.

- [ ] **Step 4: Run tests and verify pass**

- [ ] **Step 5: Commit**

```bash
git add backtest/scripts/eval_ic_multi_pool.py tests/backtest/test_eval_ic_multi_pool.py
git commit -m "feat(backtest): evaluate candidate self labels"
```

### Task 6: Two-Row Registry Report

**Files:**
- Modify: `backtest/scripts/build_experiment_report.py`
- Test: `tests/backtest/test_build_experiment_report.py`

**Interfaces:**
- Consumes: registry entries containing
  `metrics_by_eval_label: {"eval_1d": ..., "eval_self": ...}`.
- Produces: two HTML rows per label-design experiment, with formal best-value
  highlighting isolated from diagnostic self-label rows.

- [ ] **Step 1: Write failing rendering tests**

Assert B1 remains the first row, candidate `eval_1d` precedes `eval_self`,
self rows have a diagnostic CSS class and label, rowspans cover experiment
name/hypothesis, and self values cannot win formal best highlighting.

- [ ] **Step 2: Run the focused test and verify failure**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_build_experiment_report.py -q
```

- [ ] **Step 3: Implement two-row expansion**

Apply expansion only when `metrics_by_eval_label` exists; preserve all existing
registry/report behavior for prior directions.

- [ ] **Step 4: Run tests and verify pass**

- [ ] **Step 5: Commit**

```bash
git add backtest/scripts/build_experiment_report.py tests/backtest/test_build_experiment_report.py
git commit -m "feat(backtest): render dual label metrics"
```

### Task 7: Freeze Design and Run Stage 0

**Files:**
- Create: `backtest/experiments/holding_duration_top30_drop1_valid.json`
- Create: `backtest/experiments/label_horizon_manifest.json`
- Create: `backtest/configs/label-design/<variant>/*.yaml`

- [ ] **Step 1: Run the Stage 0 diagnostic with the five B1 sessions**

Run the script as a file with the five registry session/seed pairs and CSI300
valid segment.

- [ ] **Step 2: Inspect completeness and censoring**

Confirm five seeds, nonzero completed spells, explicit censored spells, monotone
survival, and all requested quantiles.

- [ ] **Step 3: Freeze the manifest before test evaluation**

Generate the manifest, inspect the chosen anchors and survival expression, and
record its SHA-256 hash.

- [ ] **Step 4: Generate and validate configs**

Run the generator and the focused configuration tests.

- [ ] **Step 5: Commit frozen design artifacts**

```bash
git add backtest/experiments/holding_duration_top30_drop1_valid.json backtest/experiments/label_horizon_manifest.json backtest/configs/label-design
git commit -m "exp(backtest): freeze label horizon matrix"
```

### Task 8: Train All Candidates and Evaluate Valid

**Files:**
- Create: `backtest/result/<session>/...` for each run.
- Create: `backtest/experiments/ic/<variant>_valid_{1d,self}.json`.

- [ ] **Step 1: Run all Stage 1 five-seed train-only configs**

Run sequentially to control memory and record every successful or failed
session.

- [ ] **Step 2: Run the survival-weighted five-seed configs**

- [ ] **Step 3: Evaluate valid against fixed and self labels**

Use all three pools and the common self-label valid end.

- [ ] **Step 4: Verify the frozen design has not changed**

Recompute the manifest hash and compare with Task 7.

- [ ] **Step 5: Record operational failures before proceeding**

Do not change labels or hypotheses. Repair only implementation/runtime defects.

### Task 9: Final Test Evaluation, Registry, Report, and Cleanup

**Files:**
- Create: `backtest/experiments/ic/<variant>_test_{1d,self}.json`
- Modify: `backtest/experiments/registry.jsonl`
- Regenerate: `backtest/experiments/report.html`

- [ ] **Step 1: Evaluate every candidate on fixed one-day test labels**

Use the full official test period and all three pools.

- [ ] **Step 2: Evaluate every candidate on its self label**

Use the frozen common self-label end date and all three pools.

- [ ] **Step 3: Build paired CSI300 comparisons**

Compare only `eval_1d` per-seed RankIC with B1.

- [ ] **Step 4: Append one registry entry per candidate**

Include hypothesis, baseline, five seeds, train/test pools, data version,
configs, sessions, `metrics_by_eval_label`, pairwise result, conclusion, and
notes. A candidate is formally improved only if all three fixed-label RankIC
means exceed B1; self-label results do not affect this conclusion.

- [ ] **Step 5: Regenerate and inspect the HTML report**

Confirm the label-design table begins with B1 and every experiment has formal
then diagnostic rows.

- [ ] **Step 6: Run the complete relevant test suite**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_analyze_holding_duration.py tests/backtest/test_label_horizons.py tests/backtest/test_label_horizon_dataset.py tests/backtest/test_label_horizon_configs.py tests/backtest/test_eval_ic_multi_pool.py tests/backtest/test_build_experiment_report.py tests/backtest/test_phase_m_train_only_configs.py tests/backtest/test_run_train_only.py -q
```

- [ ] **Step 7: Run artifact cleanup dry-run, inspect, then apply**

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/cleanup_experiment_artifacts.py
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/cleanup_experiment_artifacts.py --apply
```

- [ ] **Step 8: Commit tracked results**

```bash
git add backtest/experiments/registry.jsonl backtest/experiments/report.html backtest/experiments/ic
git commit -m "exp(backtest): evaluate label horizons"
```

