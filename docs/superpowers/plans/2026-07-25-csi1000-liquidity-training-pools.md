# CSI1000 Liquidity Training Pools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a controlled five-arm CSI1000 liquidity-sample experiment, promote the same-version full group to B1-M, and deploy the best valid-period full model from a Git-tracked artifact directory.

**Architecture:** A custom `DatasetH` subclass filters only the train frame using a causal 20-day lagged turnover proxy; standard Qlib handlers continue to supply unchanged valid/test frames. Existing training and multi-pool evaluation entry points are reused, with the evaluator generalized to the valid segment for deployment seed selection.

**Tech Stack:** Python, pandas, Qlib DatasetH/Data API, LightGBM, MLflow file store, pytest, YAML/JSON.

## Global Constraints

- Follow `backtest/EXPERIMENT_STANDARD.md`; Phase M changes training samples only.
- Fixed seeds: `[42, 1000, 2000, 3000, 4000]`.
- Train: `2016-01-02~2020-01-10`; valid: `2020-01-13~2021-07-15`; test: `2021-07-16~2026-07-16`.
- Default test pools: `csi300`, `csi500`, `csi1000`.
- Never use test metrics to select the live recorder.
- Run Qlib workflows as script files, never heredoc/stdin, with `/opt/anaconda3/envs/qlib/bin/python`.
- User explicitly authorized clearing the contents of `backtest/result/` and `mlruns/`.

---

### Task 1: Clean experiment artifacts

**Files:**
- Delete contents only: `backtest/result/`
- Delete contents only: `mlruns/`

- [ ] Verify both exact directories and current sizes.
- [ ] Remove only their children, preserving the directory roots.
- [ ] Verify both directories contain zero children.

### Task 2: Train-only liquidity filtering

**Files:**
- Create: `backtest/datasets/liquidity_segment.py`
- Modify: `backtest/scripts/config_loader.py`
- Test: `tests/backtest/test_sample_dataset.py`
- Test: `tests/misc/test_backtest_config_loader.py`

- [ ] Write failing tests proving causal expression construction, mutually exclusive daily thirds, deterministic random sampling, train-only filtering, and custom dataset config propagation.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement `select_daily_bucket`, `load_liquidity_scores`, and `LiquiditySegmentDatasetH`.
- [ ] Extend `build_task` to merge an optional top-level `dataset` class/module/kwargs with the standard handler and segments.
- [ ] Run the focused and existing config-loader tests to green.
- [ ] Commit the tested filtering support.

### Task 3: Segment-aware IC evaluation and valid selection

**Files:**
- Modify: `backtest/scripts/eval_ic_multi_pool.py`
- Create: `backtest/scripts/select_valid_model.py`
- Test: `tests/backtest/test_eval_ic_multi_pool.py`
- Test: `tests/backtest/test_select_valid_model.py`

- [ ] Write failing tests for `--segment valid`, segment-specific dates/dataset construction, and RankIC/RankICIR selection without test fields.
- [ ] Run tests and confirm failures.
- [ ] Generalize evaluator helpers and output metadata to the requested segment, defaulting to test.
- [ ] Implement a selector that chooses maximum valid RankIC, then RankICIR, and writes an auditable JSON.
- [ ] Run focused tests to green and commit.

### Task 4: Experiment configurations

**Files:**
- Create: `backtest/configs/train-data/csi1000-full-v2/*.yaml`
- Create: `backtest/configs/train-data/csi1000-random-third/*.yaml`
- Create: `backtest/configs/train-data/csi1000-liquidity-high/*.yaml`
- Create: `backtest/configs/train-data/csi1000-liquidity-mid/*.yaml`
- Create: `backtest/configs/train-data/csi1000-liquidity-low/*.yaml`
- Test: `tests/backtest/test_liquidity_experiment_configs.py`

- [ ] Write a failing structural test requiring exactly five fixed seeds per arm and allowing only dataset sampling fields to differ from full.
- [ ] Generate the 25 explicit YAML configs.
- [ ] Run structural tests and config-loader tests to green.
- [ ] Commit configs.

### Task 5: Execute and evaluate

**Files:**
- Create: `backtest/experiments/ic/<arm>_<pool>.json`
- Modify: `backtest/experiments/registry.jsonl`
- Regenerate: `backtest/experiments/report.html`

- [ ] Run all 25 configs strictly serially and verify every summary has one success and zero failures.
- [ ] Evaluate every arm on the three default pools with the unified evaluator.
- [ ] Evaluate `csi1000-full-v2` on valid/csi1000 and select the deployment recorder.
- [ ] Save pairwise CSI300 results, append all five experiment rows, and rebuild HTML.
- [ ] Commit result summaries and report.

### Task 6: Promote B1-M and update live

**Files:**
- Modify: `backtest/EXPERIMENT_STANDARD.md`
- Modify: `backtest/experiments/registry.jsonl`
- Modify: `live_trading/configs/csi300_topk10_live.yaml`
- Modify: `backtest/configs/csi300_live_parity.yaml`
- Modify: `live_trading/modules/signal_generator.py`
- Modify: `live_trading/modules/backtest_parity.py`
- Create: `live_trading/models/b1_m/<model-id>/trained_model`
- Create: `live_trading/models/b1_m/<model-id>/metadata.json`
- Create: `backtest/experiments/selection/b1_m_live_model.json`
- Test: `tests/live_trading/test_signal_generator.py`
- Test: `tests/live_trading/test_backtest_parity.py`

- [ ] Update the standard to B1-M only after all five full runs and metrics are present.
- [ ] Write failing tests requiring Git-tracked `model_path` loading, missing/hash-mismatched artifact failure, and live/backtest path parity.
- [ ] Copy the selected model into `live_trading/models/b1_m/<model-id>/`, record source/valid metrics/size/SHA-256, and add it to Git.
- [ ] Update live/parity model path, identifiers, and handler fit start while leaving the live CSI300 universe and B0-S strategy unchanged.
- [ ] Run live/backtest parity, model loading, signal-generation smoke, registry/report, and targeted tests.
- [ ] Commit experiment/baseline documentation separately from the live configuration commit.

### Task 7: Retention cleanup and main integration

**Files:**
- Retain in experiment `mlruns/`: five B1-M full training recorders.
- Retain in Git: selected live model and metadata under `live_trading/models/b1_m/`.

- [ ] Delete all non-retained train/backtest MLflow experiments and `.trash`.
- [ ] Verify every retained model path loads, its SHA-256 matches metadata, and all deleted recorders are absent.
- [ ] Verify both worktrees are clean before integration.
- [ ] Cherry-pick only the live-related commit onto main.
- [ ] Re-run parity/model-loading tests from main and verify clean git status.
