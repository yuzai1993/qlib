# CSI1000 Low-Liquidity Trim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether removing the lowest-liquidity 10%, 20%, or one-third of CSI1000 train samples improves B1-M out-of-sample ranking metrics.

**Architecture:** Extend the existing train-only liquidity dataset with a cumulative lower-tail cutoff while preserving its historical bucket API. Generate 15 explicit configurations from B1, train serially, evaluate each five-seed group through the standard three-pool evaluator, register summaries, rebuild the report, and remove non-baseline MLflow artifacts.

**Tech Stack:** Python, pandas, Qlib DatasetH/Data API, LightGBM, YAML, JSON, pytest.

## Global Constraints

- Follow `backtest/EXPERIMENT_STANDARD.md`; this is Phase M and only train samples change.
- Baseline reference is `B1 v1.0`.
- Fixed seeds are `[42, 1000, 2000, 3000, 4000]`.
- Train is `2016-01-02~2020-01-10`; valid is `2020-01-13~2021-07-15`; test is `2021-07-16~2026-07-16`.
- Evaluate all three default pools: `csi300`, `csi500`, `csi1000`.
- Run Qlib with `/opt/anaconda3/envs/qlib/bin/python` from script files, never heredoc/stdin.
- Do not promote a baseline or choose a live seed from test metrics.

---

### Task 1: Lower-tail liquidity selector

**Files:**
- Modify: `backtest/datasets/liquidity_segment.py`
- Test: `tests/backtest/test_sample_dataset.py`

**Interfaces:**
- Produces: `select_above_daily_quantile(scores: pd.Series, min_pct: float) -> pd.Series`
- Produces: `LiquiditySegmentDatasetH(..., min_liquidity_pct: float | None = None)`

- [ ] **Step 1: Write failing selector tests**

Add tests with two dates of deterministic scores. Assert `min_pct=0.1`, `0.2`, and `1/3`
retain only ranks strictly above each cutoff; assert values outside `(0, 1)` raise
`ValueError`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:
`/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_sample_dataset.py -q`

Expected: failure because `select_above_daily_quantile` and `min_liquidity_pct` do not exist.

- [ ] **Step 3: Implement the cumulative selector**

Normalize the index, rank non-null scores per date with `method="first", pct=True`, return a
full-index boolean mask for `rank > min_pct`, validate the cutoff, and make the dataset choose
this path when `min_liquidity_pct` is set. Reject simultaneous bucket and cutoff modes.

- [ ] **Step 4: Re-run focused tests**

Run:
`/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_sample_dataset.py -q`

Expected: all tests pass.

### Task 2: Explicit experiment configs

**Files:**
- Create: `backtest/scripts/generate_liquidity_trim_configs.py`
- Create: `backtest/configs/liquidity-trim/drop-low-10pct/*.yaml`
- Create: `backtest/configs/liquidity-trim/drop-low-20pct/*.yaml`
- Create: `backtest/configs/liquidity-trim/drop-low-third/*.yaml`
- Create: `tests/backtest/test_liquidity_trim_experiment_configs.py`

**Interfaces:**
- Consumes: B1 configs under `backtest/configs/train-data/csi1000-full-v2/`
- Produces: 15 configs using `min_liquidity_pct` values `0.1`, `0.2`, and `1/3`

- [ ] **Step 1: Write a failing structural test**

Require exactly five fixed seeds per arm; compare each generated config to its B1 source and
allow differences only in `run.note` and top-level `dataset`.

- [ ] **Step 2: Run the structural test and verify failure**

Run:
`/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_liquidity_trim_experiment_configs.py -q`

Expected: failure because the generator/configs do not exist.

- [ ] **Step 3: Implement generator and generate configs**

Copy each B1 YAML, set the note, and add
`LiquiditySegmentDatasetH` with `min_liquidity_pct`, `lookback=20`, and `lag=1`.

- [ ] **Step 4: Run configuration tests**

Run:
`/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_liquidity_trim_experiment_configs.py tests/misc/test_backtest_config_loader.py -q`

Expected: all tests pass.

### Task 3: Train and evaluate three arms

**Files:**
- Create during execution: `backtest/result/<timestamp>_<note>/`
- Create: `backtest/experiments/ic/lt_drop_low_<cutoff>_lgbm_<pool>.json`

**Interfaces:**
- Consumes: 15 explicit YAML configs
- Produces: five successful sessions and three pool summaries per arm

- [ ] **Step 1: Run all 15 trainings serially**

For each explicit config run:
`MLFLOW_ALLOW_FILE_STORE=true /opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py --config <relative-config>`

Expected: each summary reports one successful run and zero failures.

- [ ] **Step 2: Evaluate each arm on three pools**

Run `backtest/scripts/eval_ic_multi_pool.py` with the arm's seed-42 config, all five
`SESSION:SEED` arguments, pools `csi300 csi500 csi1000`, and the arm-specific IC JSON output.

- [ ] **Step 3: Validate result completeness**

Assert every JSON contains five seeds and a `seed_mean` for all three pools, with data version
and fixed test segment present.

### Task 4: Registry, report, and pairwise comparison

**Files:**
- Modify: `backtest/experiments/registry.jsonl`
- Regenerate: `backtest/experiments/report.html`

**Interfaces:**
- Consumes: three evaluator JSONs and B1 IC JSON
- Produces: three `direction="liquidity-trim"` registry rows

- [ ] **Step 1: Compute CSI300 paired comparisons**

Use `backtest/scripts/eval_protocol.py:pairwise_win_count` on matching seed RankIC means for
each arm versus `td_csi1000_full_v2_lgbm_csi300.json`.

- [ ] **Step 2: Append pre-registered rows**

Record `baseline_ref="B1 v1.0"`, five seeds, CSI1000 train pool, the cutoff expression, all
config/result paths, metrics, pairwise results, and a conclusion based on three-pool RankIC.

- [ ] **Step 3: Rebuild and inspect HTML**

Run:
`/opt/anaconda3/envs/qlib/bin/python backtest/scripts/build_experiment_report.py`

Confirm the `liquidity-trim` table starts with the B1 row and then contains all three arms.

### Task 5: Cleanup and verification

**Files:**
- Delete: this experiment's non-baseline directories under `mlruns/`
- Delete: `mlruns/.trash/` contents
- Retain: current B1 five-seed recorders and Git-tracked live model

- [ ] **Step 1: Resolve exact MLflow IDs**

Read B1 session `run_meta.json` files and live metadata to build an explicit recorder-ID
whitelist. Resolve every experiment directory before deletion.

- [ ] **Step 2: Remove only non-whitelisted experiment artifacts**

Delete the 15 new train/backtest recorder directories and `.trash`, preserving B1 and live
artifacts required by the standard.

- [ ] **Step 3: Run final verification**

Run focused dataset/config tests, parse all new JSON and registry lines, rebuild the report,
verify B1 paths still load, and inspect `git diff --check` plus `git status`.

- [ ] **Step 4: Commit experiment changes**

Commit source/tests/configs first and result summaries/report second, without changing the B1
definition or live configuration.
