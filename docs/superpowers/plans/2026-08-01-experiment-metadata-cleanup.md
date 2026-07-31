# Experiment Metadata Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the experiment report chronological with B6-M last, and reduce root experiment metadata to the registry, report, and a lean B6-M freeze record while preserving metrics, configs, and model artifacts.

**Architecture:** Treat `registry.jsonl` and `experiments/ic/` as the experiment evidence index, and `b6_model_freeze.json` as the sole extra production-model contract. Remove historical selection/protocol/diagnostic manifests and their references; regenerate the HTML from the cleaned registry.

**Tech Stack:** Python 3, JSON/JSONL, pytest, static HTML report generator.

## Global Constraints

- Preserve all experiment metric JSON files under `backtest/experiments/ic/`.
- Preserve all training configs and B6-M model paths/hashes.
- Preserve historical experiment rows and metrics, including post-2020 experiments, but remove obsolete protocol/selection artifact references.
- Do not change `backtest/EXPERIMENT_STANDARD.md` evaluation rules or time splits.

---

### Task 1: Make the baseline report chronological

**Files:**
- Modify: `tests/backtest/test_build_experiment_report.py`
- Modify: `backtest/scripts/build_experiment_report.py`
- Regenerate: `backtest/experiments/report.html`

**Interfaces:**
- Consumes: registry rows sorted by date and registry position.
- Produces: baseline table rows ordered B0 through B6, with B6-M as the final row.

- [ ] Change the existing report-order test to assert B0 precedes B6 and B6 is last.
- [ ] Run the focused test and confirm it fails because B6 is currently forced to the first row.
- [ ] Remove the baseline-direction newest-first special case while preserving historical `baseline_ref` injection for other direction tables.
- [ ] Run the focused report tests and regenerate `report.html`.

### Task 2: Remove low-value experiment metadata

**Files:**
- Modify: `backtest/experiments/registry.jsonl`
- Modify: `backtest/experiments/b6_model_freeze.json`
- Modify: `backtest/experiments/ic/mh_valid_rankic_selected_test_1d.json`
- Modify: `backtest/experiments/ic/tr_rankic_winner_post2020_forward_comparison.json`
- Delete: `backtest/experiments/SAMPLE_EXPERIMENT_SUMMARY.md`
- Delete: `backtest/experiments/b5_post2020_forward_protocol.json`
- Delete: `backtest/experiments/b5_rankic_hyperparam_selection.json`
- Delete: `backtest/experiments/h40_survival_weight_manifest.json`
- Delete: `backtest/experiments/h40_survival_weight_manifest.sha256`
- Delete: `backtest/experiments/holding_duration_top30_drop1_valid.json`
- Delete: `backtest/experiments/label_horizon_manifest.json`
- Delete if present: `backtest/experiments/.DS_Store`

**Interfaces:**
- Consumes: existing metrics, configs, sessions, models, hashes, and training contract.
- Produces: a lean B6-M freeze record and registry rows with no references to deleted metadata.

- [ ] Add a repository check that searches retained experiment metadata for references to the files being deleted; confirm it currently finds references.
- [ ] Remove selection/protocol/diagnostic fields without altering metrics, configs, session/model locations, or experiment conclusions.
- [ ] Recompute hashes for retained JSON files whose content changed and update their registry references.
- [ ] Delete the confirmed low-value files and verify no retained runtime metadata references them.

### Task 3: Verify and publish

**Files:**
- Verify: all changed files.

**Interfaces:**
- Consumes: cleaned metadata and regenerated report.
- Produces: tested commits on `exp/workspace` and fast-forwarded `main`, both pushed.

- [ ] Parse every retained JSON/JSONL file and verify unique registry `exp_id` values.
- [ ] Run focused backtest tests, then the full relevant test suite.
- [ ] Run `git diff --check` and inspect the final diff/stat.
- [ ] Commit and push `exp/workspace`.
- [ ] Fast-forward `main` to the same commit and push `main`.
