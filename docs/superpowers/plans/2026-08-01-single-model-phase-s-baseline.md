# Single-Model Phase S Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the tracked baseline directory the canonical single-model B6-M entry point for Phase S and retire the duplicate experiment freeze manifest.

**Architecture:** Preserve five-seed Phase M evaluation evidence in the registry, but expose one deployment artifact through `backtest/models/baselines/b6-m/manifest.json`. Bind seed 4000 to its valid RankIC selection evidence, config hash, and retained model hash.

**Tech Stack:** JSON, Markdown, Python/pytest, Git.

## Global Constraints

- Phase M historical metrics, configs, and conclusions must not change.
- Phase S uses one frozen B6-M model: seed 4000.
- Test metrics must not be used to justify the retained seed.
- `backtest/models/baselines/` becomes the canonical model-artifact location.

---

### Task 1: Specify and test the canonical baseline manifest

**Files:**
- Create: `tests/backtest/test_baseline_model_manifests.py`
- Modify: `backtest/models/baselines/b6-m/manifest.json`
- Track: `backtest/models/baselines/b6-m/seed4000/trained_model`
- Track: `backtest/models/baselines/b1-m/manifest.json`
- Track: `backtest/models/baselines/b1-m/seed2000/trained_model`

**Interfaces:**
- Consumes: retained model/config files and the B6 valid IC artifact.
- Produces: a hash-verifiable single-model Phase S manifest.

- [ ] Write a failing test asserting seed 4000, valid-only selection, model/config hashes, and the absence of a five-seed runtime contract.
- [ ] Run the focused test and confirm failure because the current manifest selects by formal test metrics and lacks a config hash.
- [ ] Update the B6 manifest to satisfy the contract without changing the model binary.
- [ ] Run the focused test and confirm it passes.

### Task 2: Retire the duplicate freeze manifest

**Files:**
- Delete: `backtest/experiments/b6_model_freeze.json`
- Modify: `backtest/experiments/registry.jsonl`
- Modify: `backtest/EXPERIMENT_STANDARD.md`
- Delete: `backtest/scripts/promote_b6_baseline.py`
- Delete: `tests/backtest/test_promote_b6_baseline.py`

**Interfaces:**
- Consumes: canonical baseline manifest path.
- Produces: registry, standards, and promotion defaults that no longer point into `experiments/` for runtime model artifacts.

- [ ] Extend the failing contract test to reject retained references to `b6_model_freeze.json` and assert the single-model Phase S wording.
- [ ] Update registry and standard references while preserving Phase M metrics.
- [ ] Remove the completed B6-only promotion utility and its obsolete five-seed freeze tests.
- [ ] Delete the duplicate freeze file and run focused tests.

### Task 3: Verify and integrate

**Files:**
- Regenerate: `backtest/experiments/report.html` only if registry rendering changes.

**Interfaces:**
- Consumes: completed baseline artifact migration.
- Produces: verified commits on `exp/workspace` and `main`.

- [ ] Verify model/config hashes, JSON validity, registry uniqueness, and absence of stale freeze references.
- [ ] Run all `tests/backtest` tests and `git diff --check`.
- [ ] Commit and push `exp/workspace`.
- [ ] Fast-forward `main`, rerun `tests/backtest`, and push `main`.
