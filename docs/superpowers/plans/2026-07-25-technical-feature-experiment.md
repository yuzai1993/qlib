# B1-M Technical Feature Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four pre-registered technical-feature variants to B1-M and complete the fixed five-seed, three-pool Phase M evaluation.

**Architecture:** A focused `Alpha158Technical` subclass appends selected expression groups to the unchanged Alpha158 fields. Generated experiment YAMLs select one group or all groups while inheriting every other B1-M setting; the existing training, unified IC evaluation, registry, and report pipelines remain authoritative.

**Tech Stack:** Python, Qlib expressions/DataHandlerLP, PyYAML, pytest, LightGBM, JSONL.

## Global Constraints

- Baseline reference is exactly `B1 v1.0`.
- Phase is `M`; only model features may change.
- Train pool is CSI1000 and seeds are exactly `[42, 1000, 2000, 3000, 4000]`.
- Valid is `2020-01-13` through `2021-07-15`; test is `2021-07-16` through `2026-07-16`.
- Test pools are exactly `csi300`, `csi500`, and `csi1000`.
- Evaluation uses `backtest/scripts/eval_ic_multi_pool.py`.
- Test results must not change the pre-registered feature definitions.

---

### Task 1: Technical feature handler

**Files:**
- Create: `backtest/features/__init__.py`
- Create: `backtest/features/technical.py`
- Test: `tests/backtest/test_technical_feature_handler.py`

**Interfaces:**
- Consumes: `qlib.contrib.data.handler.Alpha158`
- Produces: `Alpha158Technical(feature_groups: Sequence[str], **kwargs)` and `technical_feature_config(groups: Sequence[str]) -> tuple[list[str], list[str]]`

- [ ] Write failing tests asserting group field counts, unique names, exact combined concatenation, rejection of empty/unknown/duplicate groups, and absence of negative `Ref` offsets.
- [ ] Run `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_technical_feature_handler.py -q` and confirm failure because the module is absent.
- [ ] Implement the minimal group registry, validation function, and Alpha158 subclass.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: Frozen experiment configurations

**Files:**
- Create: `backtest/configs/feature-technical/{bollinger,momentum,trend,combined}/*.yaml`
- Test: `tests/backtest/test_technical_feature_configs.py`

**Interfaces:**
- Consumes: `Alpha158Technical` and B1-M seed configs
- Produces: twenty standard `train_backtest` YAML files

- [ ] Write failing parametrized tests for the 20 expected configs and compare every frozen field against B1-M.
- [ ] Run the focused config test and confirm the missing-file failure.
- [ ] Generate the YAMLs by copying B1-M and changing only experiment comments, run note, handler class/module, `feature_groups`, and seed-specific note.
- [ ] Re-run handler/config tests and confirm they pass.

### Task 3: Train and evaluate all pre-registered variants

**Files:**
- Create: `backtest/result/<timestamp>_<variant-seed>/`
- Create: `backtest/experiments/ic/ft_<variant>_lgbm_{csi300,csi500,csi1000}.json`

**Interfaces:**
- Consumes: twenty YAML configs
- Produces: five trained sessions and one three-pool evaluation JSON per variant

- [ ] Run every YAML with `/opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py --config <path>`.
- [ ] Confirm each session `metrics.json` reports `status: success` and capture exact session names.
- [ ] Run `eval_ic_multi_pool.py` per variant with all five `session:seed` inputs and the three default pools.
- [ ] Validate each evaluation JSON contains five seeds for every pool and the fixed test segment.

### Task 4: Registry, report, cleanup, and verification

**Files:**
- Modify: `backtest/experiments/registry.jsonl`
- Regenerate: `backtest/experiments/report.html`

**Interfaces:**
- Consumes: evaluation JSONs and B1-M metrics
- Produces: four immutable experiment records and the standard HTML report

- [ ] Compute CSI300 paired RankIC differences against the B1 seed metrics.
- [ ] Append all four pre-registered records with configs, result dirs, summaries, pairwise results, and evidence-based conclusions.
- [ ] Run `build_experiment_report.py` and verify the `feature-technical` table begins with B1.
- [ ] Delete only the new experiment MLflow train/backtest directories using explicit IDs; preserve the five B1 experiment/recorder pairs and tracked live model.
- [ ] Run focused tests, registry/report consistency checks, and `git diff --check`; inspect `git status`.
