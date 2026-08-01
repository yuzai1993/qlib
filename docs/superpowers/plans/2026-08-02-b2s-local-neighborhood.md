# B2-S Local Neighborhood Strategy Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement and execute a restartable 540-candidate valid-only B2-S neighborhood sweep, freeze one robust winner, test it once on three pools, and publish canonical results.

**Architecture:** A focused protocol module owns the immutable grid and robust selection rule. A runner reuses the existing frozen-prediction backtest primitives, checkpoints after every candidate, and opens test only for the frozen winner. A report/registry finalizer records the baseline-first comparison without promoting a new baseline.

**Tech Stack:** Python 3, Qlib pred-only backtests, pandas/numpy, YAML, JSONL, pytest, static HTML.

## Global Constraints

- No model retraining; use only the B6-M baseline manifest artifact.
- Select only on CSI1000 valid 2020-01-13 through 2021-07-15.
- Test only the frozen winner on CSI1000/CSI300/CSI500 through 2026-07-31.
- Use CNY 500,000, current Phase S fees, and B2-S as the first report table.
- Reuse registered B2-S test metrics rather than rerunning the baseline.
- Preserve checkpoints and finish within five hours.

---

### Task 1: Freeze grid and robust selection contract

**Files:**
- Create: `backtest/scripts/strategy_neighborhood_protocol.py`
- Create: `tests/backtest/test_strategy_neighborhood_protocol.py`

- [ ] Write failing tests for the exact 540 unique candidates, baseline inclusion, axial-neighbor membership, incomplete-neighbor rejection, and preregistered ranking order.
- [ ] Run the focused tests and confirm RED.
- [ ] Implement candidate generation, neighbor lookup, finite-metric validation, P25 scoring, and deterministic winner selection.
- [ ] Run the focused tests and confirm GREEN.

### Task 2: Build restartable valid/test runner

**Files:**
- Modify: `backtest/scripts/run_strategy_sweep.py`
- Create: `backtest/scripts/run_strategy_neighborhood.py`
- Create: `tests/backtest/test_run_strategy_neighborhood.py`

- [ ] Write failing tests for per-candidate risk propagation, checkpoint resume, valid completeness gating, and winner-only test planning.
- [ ] Run the focused tests and confirm RED.
- [ ] Add candidate risk support without changing legacy defaults, then implement atomic checkpoints and phase gating.
- [ ] Run the focused tests and confirm GREEN.

### Task 3: Publish baseline-first report and registry row

**Files:**
- Create: `backtest/scripts/finalize_strategy_neighborhood.py`
- Create: `tests/backtest/test_finalize_strategy_neighborhood.py`
- Create during execution: `backtest/experiments/strategy_neighborhood_report.html`
- Modify during execution: `backtest/experiments/registry.jsonl`
- Modify during execution: `backtest/experiments/report.html`

- [ ] Write failing tests for the baseline-first HTML, B2-S baseline reference, frozen-winner identity, and immutable JSONL upsert.
- [ ] Run the focused tests and confirm RED.
- [ ] Implement finalization and report rendering.
- [ ] Run the focused tests and confirm GREEN.

### Task 4: Execute and verify the experiment

**Files:**
- Create during execution: `backtest/configs/strategy-neighborhood/b2-s-local/*.yaml`
- Create during execution: `backtest/experiments/strategy-neighborhood/20260802_b2s_local/*`

- [ ] Generate the six frozen prediction artifacts and verify their hashes.
- [ ] Write the preregistered protocol before the first valid backtest.
- [ ] Run/resume all 540 CSI1000 valid candidates and freeze the robust winner.
- [ ] Run the frozen winner once on each of the three test pools.
- [ ] Finalize registry and both HTML reports.
- [ ] Run focused tests, `tests/backtest`, JSON/hash checks, and `git diff --check`.
- [ ] Run standard cleanup in dry-run and apply modes, then verify retained summaries.
- [ ] Commit the experiment implementation and results.
