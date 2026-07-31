# B5 Rolling Retrain and Vol-Scaled Label Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run two fully audited B5 Phase-M experiments: annual expanding walk-forward retraining and an H40 return label scaled by ex-ante 20-day volatility.

**Architecture:** Keep the ordinary vol-scaled variant on the existing five-config `train_only` path. Add a rolling orchestrator that produces one parent result session per seed and multiple train-only fold recorders inside it, then extend the canonical multi-pool evaluator to concatenate non-overlapping fold predictions before calling the existing `daily_ic` implementation. Extend cleanup validation narrowly so a retained rolling parent session may reference one successful MLflow experiment per declared fold.

**Tech Stack:** Python 3.12, pandas, Qlib DatasetH/PurgedHorizonDataset, DoubleEnsemble, MLflow filesystem backend, pytest, YAML/JSONL.

## Global Constraints

- Baseline is `B5 v1.0`; do not promote a candidate or modify live B1 artifacts.
- Phase M only: no strategy or portfolio backtest.
- Seeds are exactly `[42, 1000, 2000, 3000, 4000]`.
- Train pool is CSI1000; evaluation pools are CSI1000, CSI300, and CSI500.
- Official test prediction dates remain `2021-07-16` through `2026-07-16`.
- Fixed evaluation label is `Ref($close, -2)/Ref($close, -1) - 1`.
- H40 train/valid boundaries use `PurgedHorizonDataset(label_horizon=40)`.
- Rolling cadence is fixed ex ante at 252 trading days, expanding train start `2016-01-02`, and shifted fixed-length valid window.
- Vol-scaled target is fixed ex ante to H40 cumulative return divided by past 20-day daily-return volatility floored at 0.5% per day, followed by `CSRankNorm`.
- New directions are `train-schedule` and `label-risk-adjustment` because existing `train-data` and `label-design` tables are anchored to historical baselines.
- Preserve all pre-existing untracked files.
- Use `/opt/anaconda3/envs/qlib/bin/python`; never invoke Qlib multiprocessing code through stdin or a heredoc.

---

### Task 1: Rolling fold generation and parent-session runner

**Files:**
- Create: `backtest/scripts/run_rolling_retrain.py`
- Create: `tests/backtest/test_run_rolling_retrain.py`

**Interfaces:**
- Consumes: validated B5 YAML from `config_loader.load_config`, `build_task`, and `run_backtest.run_train_only_once`.
- Produces: `build_expanding_folds(cfg, calendar, step) -> list[dict]`, `apply_fold(cfg, fold) -> dict`, and a result-session `meta.json` with `mode="rolling_train_only"` and `rolling_folds`.

- [ ] **Step 1: Write failing fold-generation tests**

```python
def test_build_expanding_folds_shifts_train_end_and_valid_window():
    calendar = pd.bdate_range("2020-01-01", periods=18)
    cfg = fixture_config(
        train=("2020-01-01", "2020-01-03"),
        valid=("2020-01-06", "2020-01-10"),
        test=("2020-01-13", "2020-01-24"),
    )
    folds = rolling.build_expanding_folds(cfg, calendar, step=4)
    assert folds[0]["segments"] == {
        "train": ["2020-01-01", "2020-01-03"],
        "valid": ["2020-01-06", "2020-01-10"],
        "test": ["2020-01-13", "2020-01-16"],
    }
    assert folds[1]["segments"]["train"][1] == "2020-01-09"
    assert folds[1]["segments"]["valid"] == ["2020-01-10", "2020-01-16"]
```

- [ ] **Step 2: Run the new test and confirm it fails because the module/API is absent**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_run_rolling_retrain.py -q`

- [ ] **Step 3: Implement fold generation and fold application**

Implement exact calendar-index shifts from the original train/valid/test segments. Test blocks are contiguous and truncate only the final block at the official test end. `apply_fold` deep-copies the config, updates all three segments, sets handler `fit_end_time` to the fold train end and handler `end_time` to the fold test end, and leaves model/features/label untouched.

- [ ] **Step 4: Add failing tests for parent metadata and failed-fold exit status**

Use real temporary `meta.json` writes and monkeypatch only the expensive Qlib training boundary. Assert one successful run record per fold, `expected_fold_count`, seed, step, and exact prediction windows.

- [ ] **Step 5: Implement the CLI runner**

The runner initializes Qlib, creates one result session, invokes `run_train_only_once` once per fold, updates `meta.json` after every fold, writes the standard session summary, and exits nonzero unless all folds succeed.

- [ ] **Step 6: Run focused tests**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_run_rolling_retrain.py tests/backtest/test_run_train_only.py -q`

### Task 2: Canonical rolling multi-pool IC evaluation

**Files:**
- Modify: `backtest/scripts/eval_ic_multi_pool.py`
- Modify: `tests/backtest/test_eval_ic_multi_pool.py`

**Interfaces:**
- Consumes: five rolling parent sessions, their per-fold `trained_model` artifacts, and fold segments from `meta.json`.
- Produces: the same `pools -> seeds/seed_mean` schema as static evaluation plus `rolling`, `folds`, and per-seed yearly/fold summaries.

- [ ] **Step 1: Write failing manifest and concatenation tests**

```python
def test_validate_rolling_sessions_requires_identical_contiguous_folds(tmp_path):
    sessions = write_rolling_manifests(tmp_path, five_seeds=True)
    folds = evaluator._load_rolling_folds(sessions)
    assert [f["test"] for f in folds] == [
        ["2021-07-16", "2022-07-20"],
        ["2022-07-21", "2023-07-25"],
    ]
```

Add separate tests that overlapping windows, gaps, mismatched fold counts, or incomplete runs raise `ValueError`.

- [ ] **Step 2: Run the evaluator tests and confirm expected failures**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_eval_ic_multi_pool.py -q`

- [ ] **Step 3: Implement rolling evaluation**

Add `--rolling` to the existing CLI. For each pool and fold, build one inference dataset from the fold config, load the corresponding run model for each seed, normalize prediction index order, concatenate folds, and call the existing `daily_ic`. Reject duplicate `(datetime, instrument)` predictions and prediction-date gaps. Add fold and calendar-year summaries without changing the primary full-test summary.

- [ ] **Step 4: Run focused evaluator tests**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_eval_ic_multi_pool.py -q`

### Task 3: Rolling-aware cleanup retention

**Files:**
- Modify: `backtest/scripts/cleanup_experiment_artifacts.py`
- Modify: `tests/backtest/test_cleanup_experiment_artifacts.py`

**Interfaces:**
- Consumes: regular one-experiment sessions or rolling sessions with `expected_fold_count` successful fold experiment IDs.
- Produces: the existing cleanup plan, retaining every MLflow experiment referenced by an eligible rolling parent session.

- [ ] **Step 1: Write a failing rolling-session retention test**

Create five parent sessions, each with two successful fold experiment IDs and `mode="rolling_train_only"`. Assert the cleanup plan retains ten candidate MLflow experiment directories and still requires exactly five parent result sessions.

- [ ] **Step 2: Run the cleanup test and verify the current one-experiment assertion fails**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_cleanup_experiment_artifacts.py -q`

- [ ] **Step 3: Implement minimal rolling validation**

Keep the existing exactly-one rule for ordinary sessions. For rolling sessions, require `expected_fold_count > 0`, exactly that many successful run entries, unique numeric experiment IDs, and matching successful `rolling_folds`; collect all referenced experiment IDs.

- [ ] **Step 4: Run cleanup tests**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_cleanup_experiment_artifacts.py -q`

### Task 4: Pre-register and run the vol20-scaled experiment

**Files:**
- Create: `backtest/configs/label-risk-adjustment/vol20-scaled/lra_vol20_scaled_s{seed}.yaml` for all five seeds
- Modify: `backtest/experiments/registry.jsonl`
- Create: `backtest/experiments/ic/lra_vol20_scaled_test_1d.json`

**Interfaces:**
- Consumes: B5 configs and ordinary train-only/evaluator scripts.
- Produces: five standard result sessions and one canonical multi-pool evaluation JSON.

- [ ] **Step 1: Create five configs and pending registry row before training**

Use this fixed label in every config:

```text
(Ref($close,-41)/Ref($close,-1)-1)/If(Gt(Std($close/Ref($close,1)-1,20),0.005),Std($close/Ref($close,1)-1,20),0.005)
```

Keep `DropnaLabel`, `CSRankNorm`, H40 purge, model, dates, and all non-seed settings identical to B5.

- [ ] **Step 2: Validate configuration invariants**

Run the phase-M config tests and a focused Qlib expression load over a short historical range.

- [ ] **Step 3: Train all five seeds**

Run each YAML through `backtest/scripts/run_backtest.py`; require exit code zero and capture all five session names.

- [ ] **Step 4: Evaluate all three pools**

Run `eval_ic_multi_pool.py` with the five sessions and the fixed 1-day evaluation label.

- [ ] **Step 5: Finalize the registry row**

Add five-seed mean/std metrics, CSI1000 seed-paired comparison versus B5, data version, conclusion, configs, and result paths.

### Task 5: Pre-register and run annual expanding rolling retraining

**Files:**
- Create: `backtest/configs/train-schedule/expanding-annual/ts_expanding_annual_s{seed}.yaml` for all five seeds
- Modify: `backtest/experiments/registry.jsonl`
- Create: `backtest/experiments/ic/ts_expanding_annual_test_1d.json`

**Interfaces:**
- Consumes: Task 1 runner and Task 2 evaluator.
- Produces: five rolling parent sessions and one canonical stitched-OOS evaluation JSON.

- [ ] **Step 1: Create five B5-identical seed configs with rolling metadata and a pending registry row**

The only treatment is `rolling.step=252`, `rolling.type=expanding`; fold dates are generated from the official Qlib trading calendar.

- [ ] **Step 2: Train the five parent sessions**

Run each config through `run_rolling_retrain.py`. Each parent must contain the same complete fold manifest and all successful fold models.

- [ ] **Step 3: Evaluate stitched predictions on all three pools**

Run `eval_ic_multi_pool.py --rolling`, require full official test coverage, and store full-test, fold, and yearly metrics.

- [ ] **Step 4: Finalize the registry row**

Record full metrics, CSI1000 seed-paired comparison, fold/year diagnostics, result paths, and an evidence-based conclusion without changing B5.

### Task 6: Report, cleanup, and final verification

**Files:**
- Modify: `backtest/experiments/report.html` through its generator only

**Interfaces:**
- Consumes: finalized registry.
- Produces: report tables with B5 first, plus a safe cleanup result.

- [ ] **Step 1: Generate and inspect the HTML report**

Run `build_experiment_report.py`. Verify `train-schedule` and `label-risk-adjustment` each show B5 as the first row.

- [ ] **Step 2: Run the full relevant test suite**

Run:

```text
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_run_rolling_retrain.py \
  tests/backtest/test_run_train_only.py \
  tests/backtest/test_eval_ic_multi_pool.py \
  tests/backtest/test_cleanup_experiment_artifacts.py \
  tests/backtest/test_build_experiment_report.py \
  tests/backtest/test_phase_m_train_only_configs.py -q
```

- [ ] **Step 3: Dry-run cleanup and inspect the complete plan**

Run `cleanup_experiment_artifacts.py` without `--apply`; stop if it reports any error or would not retain exactly B5 plus the best all-three-pool candidate.

- [ ] **Step 4: Apply cleanup**

Run the same command with `--apply`, then dry-run again to verify no unintended result or MLflow directories remain.

- [ ] **Step 5: Audit repository state and report results**

Verify registry JSONL parses, report regenerates deterministically apart from timestamp, all five seeds are present, all official test dates are covered, and no live-trading config/model was changed.
