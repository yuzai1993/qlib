# Phase M Train-Only and Artifact Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Phase M model workflows from running strategy backtests and enforce baseline-plus-best-candidate retention for MLflow and result sessions.

**Architecture:** Add a first-class `train_only` branch to the existing runner while preserving explicit combined and backtest-only modes. Add a registry-driven cleanup command whose pure selection and path-planning functions are unit tested, with destructive execution gated behind `--apply`.

**Tech Stack:** Python, Qlib workflow/MLflow, YAML, JSONL, pathlib, pytest.

## Global Constraints

- Phase M uses fixed five seeds and unified three-pool IC/RankIC evaluation.
- `train_only` must never construct or generate PortAnaRecord.
- Cleanup defaults to dry-run and deletes only validated direct children of the two artifact roots.
- Current baseline and the best qualifying five-seed candidate are selected as complete experiment groups.
- Historical registry, IC JSON, configs, and HTML are never deleted.
- Qlib commands use `/opt/anaconda3/envs/qlib/bin/python` and never heredoc/stdin on macOS.

---

### Task 1: Train-only configuration contract

**Files:**
- Modify: `backtest/scripts/config_loader.py`
- Modify: `tests/misc/test_backtest_config_loader.py`

**Interfaces:**
- Produces: `VALID_MODES` containing `train_only`, `train_backtest`, `backtest_only`
- Produces: `validate_run_section` that allows train-only configs without strategy/backtest

- [ ] Add tests proving `train_only` is accepted without strategy/backtest and invalid modes still fail.
- [ ] Run the tests and confirm failure because `train_only` is unsupported.
- [ ] Add the mode and conditional strategy/backtest validation.
- [ ] Re-run the focused tests to green.

### Task 2: Train-only execution

**Files:**
- Modify: `backtest/scripts/run_backtest.py`
- Create: `tests/backtest/test_run_train_only.py`

**Interfaces:**
- Produces: `run_train_only_once(run_idx, n_runs, session_dir, session_name, note, task) -> dict`

- [ ] Add a test with fake model, dataset, recorder, and report writers proving one fit/save occurs and no SignalRecord or PortAnaRecord is constructed.
- [ ] Run it and confirm failure because the function does not exist.
- [ ] Implement training, train-only metadata, lightweight report files, and main-mode dispatch.
- [ ] Re-run the focused runner and config tests.

### Task 3: Phase M config migration

**Files:**
- Modify: all tracked Phase M YAML under `backtest/configs/`
- Modify: `backtest/scripts/generate_liquidity_configs.py`
- Modify: `backtest/scripts/generate_liquidity_trim_configs.py`
- Modify: structural config tests under `tests/backtest/`

**Interfaces:**
- Produces: every tracked `train_backtest` model config explicitly using `train_only`

- [ ] Add a repository structural test rejecting `train_backtest` in Phase M YAML.
- [ ] Run it and confirm the existing configs fail.
- [ ] Mechanically migrate the YAML and force both generators to emit `train_only`.
- [ ] Re-run config and generator tests.

### Task 4: Registry-driven cleanup

**Files:**
- Create: `backtest/scripts/cleanup_experiment_artifacts.py`
- Create: `tests/backtest/test_cleanup_experiment_artifacts.py`

**Interfaces:**
- Produces: `select_retained_rows(rows) -> list[dict]`
- Produces: `build_cleanup_plan(repo_root, rows) -> dict`
- Produces CLI: `cleanup_experiment_artifacts.py [--repo-root PATH] [--registry PATH] [--apply]`

- [ ] Add synthetic registry tests for baseline selection, all-pool strict improvement, mean RankIC ranking, RankICIR tie-break, and no-candidate behavior.
- [ ] Add temporary-directory tests proving paths outside direct artifact children are rejected and dry-run does not delete.
- [ ] Run tests and confirm failure because the script does not exist.
- [ ] Implement selection, exact-path planning, MLflow train-recorder discovery, dry-run output, and apply deletion.
- [ ] Re-run cleanup tests.

### Task 5: Standard and current cleanup

**Files:**
- Modify: `backtest/EXPERIMENT_STANDARD.md`

- [ ] Upgrade the standard to v1.3 and document train-only Phase M.
- [ ] Replace section 6.3 with unified MLflow/result retention rules and the cleanup command.
- [ ] Update the execution checklist and script appendix.
- [ ] Run the cleanup command in dry-run mode and inspect the baseline/candidate/delete sets.
- [ ] Run with `--apply`, then verify only B1 and `feature-technical/trend` result sessions remain; MLflow retains every still-available selected train recorder and contains no backtest or `.trash`.

### Task 6: Verification and integration

**Files:**
- Verify all modified source, YAML, tests, and standard files.

- [ ] Run focused config, runner, cleanup, and experiment structural tests.
- [ ] Run all `tests/backtest` tests except the documented pre-existing missing-config failures.
- [ ] Run `git diff --check`, inspect the final cleanup plan, and verify both artifact roots.
- [ ] Request independent code review and address all Critical/Important findings.
- [ ] Commit the implementation and documentation without modifying the current baseline metrics.
