# Strategy Stability Baseline Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put a one-row B2-S baseline summary table before every experiment table in the standalone strategy stability report.

**Architecture:** `build_strategy_stability_report.build_html` will resolve the unique `baseline/b2-s-on-b6-m` registry row and join it to the B6-M diagnostic candidate identified by `strategy.candidate_id`. A focused renderer will combine baseline identity/protocol fields with that candidate's `full_period` metrics, then prepend the resulting section to the existing report sections.

**Tech Stack:** Python 3, JSONL registry, BeautifulSoup-based pytest assertions, static HTML/CSS.

## Global Constraints

- The current strategy baseline is `B2-S v1.0` on frozen model `B6 v1.0`.
- The report remains CSI1000-only and covers 2020-01-13 through 2026-07-31.
- The baseline table is the first `<table>` in the document.
- Existing B6-M full-period, yearly, and Top30-neighborhood sections remain unchanged.
- Missing or duplicate baseline registry rows are errors; missing individual metrics render as `—`.

---

### Task 1: Resolve and render the current strategy baseline

**Files:**
- Modify: `tests/backtest/test_build_strategy_stability_report.py`
- Modify: `backtest/scripts/build_strategy_stability_report.py`
- Modify: `backtest/experiments/strategy_stability_report.html`

**Interfaces:**
- Consumes: registry rows passed to `build_html(rows: Sequence[dict]) -> str`.
- Produces: `_baseline_section(baseline_row: dict, candidates: Sequence[dict]) -> str`, rendered before the existing stability sections.

- [x] **Step 1: Write the failing rendering and validation tests**

Add a `_baseline_row()` fixture containing `baseline_ref`, `frozen_model_ref`, `selection_pool`, `selection_segment`, `test_segment`, and the `topk-t30-d2-h20` strategy. Pass it to all successful `build_html` calls. Assert the first table is `table.baseline`, contains `B2-S v1.0`, `B6 v1.0`, `CSI1000`, `2020-01-13 至 2026-07-31`, `Top30 / d2 / h20`, and the six formatted metrics from the matching diagnostic candidate. Add missing- and duplicate-baseline cases that expect `ValueError` containing `exactly one B2-S`.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_build_strategy_stability_report.py -q
```

Expected: failures because the baseline row is not resolved and no baseline table exists.

- [x] **Step 3: Implement unique baseline resolution and rendering**

In `build_html`, select rows whose `exp_id` equals `baseline/b2-s-on-b6-m`; require exactly one. Resolve its `strategy.candidate_id` against the validated B6-M diagnostic candidates; require exactly one match. Render a `<section id="current-baseline">` containing a one-row `table.baseline` with identity, pool, combined full-period dates, parameters, and the six `METRICS` values. Insert this section before the existing B6-M section.

- [x] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_build_strategy_stability_report.py -q
```

Expected: all tests pass.

- [x] **Step 5: Regenerate the standalone report**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/build_strategy_stability_report.py
```

Confirm `backtest/experiments/strategy_stability_report.html` has `#current-baseline` and its first table has class `baseline`.

- [x] **Step 6: Run full verification**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest -q
git diff --check
```

Expected: the full backtest suite passes and the diff has no whitespace errors.

- [x] **Step 7: Commit the implementation**

```bash
git add backtest/scripts/build_strategy_stability_report.py \
  tests/backtest/test_build_strategy_stability_report.py \
  backtest/experiments/strategy_stability_report.html \
  docs/superpowers/plans/2026-08-02-strategy-stability-baseline-table.md
git commit -m "feat(backtest): add strategy baseline summary table"
```
