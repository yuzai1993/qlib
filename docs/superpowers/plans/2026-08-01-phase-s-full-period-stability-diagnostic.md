# Phase S Full-Period Strategy Stability Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run all frozen B1-M/B6-M Phase S strategies continuously on CSI1000 from 2020-01-13 through 2026-07-31 and publish a separate descriptive stability report using after-cost absolute-return risk metrics without changing the selected strategy or live configuration.

**Architecture:** Compose the already-frozen valid/test predictions into one hash-verified full-period bundle, run the existing pred-only backtest path with a diagnostic-specific runner, and summarize each continuous report into full-period and calendar-year metrics. Store two non-selecting diagnostic rows in the canonical registry, generate a separate HTML from those rows, then delete all diagnostic Qlib/MLflow run directories while retaining JSON, manifests and HTML.

**Tech Stack:** Python 3.12, pandas, Qlib pred-only backtest, JSON/JSONL, pytest, static HTML.

## Global Constraints

- Read `backtest/EXPERIMENT_STANDARD.md` as the experiment source of truth.
- Do not retrain or reinfer B1-M/B6-M; models remain under `backtest/models/baselines/<model-ref>/`.
- Use only CSI1000 with benchmark `SH000852`.
- Full period is exactly `2020-01-13` through `2026-07-31`.
- Account is exactly 500,000 yuan, risk degree 0.95, and current B1-S live costs remain unchanged.
- Run the existing 18 B1-M and 22 B6-M candidates; B1-S `topk-t10-d2-h1` is first.
- Do not compute or display IR; use after-cost absolute portfolio returns.
- Do not create `selected_candidate_id`, alter the existing Phase S winners, or modify live trading configuration.
- Write a new `backtest/experiments/strategy_stability_report.html`; do not overwrite `strategy_report.html`.
- Registry conclusion is `diagnostic_no_selection` and cleanup retention eligibility is false.

---

### Task 1: Pure Stability Metric Contract

**Files:**
- Create: `backtest/scripts/strategy_stability_metrics.py`
- Create: `tests/backtest/test_strategy_stability_metrics.py`

**Interfaces:**
- Consumes: a Qlib `report_normal.csv` DataFrame with `return`, `cost`, `bench`, `turnover` and a datetime index.
- Produces: `summarize_period(report: pd.DataFrame) -> dict[str, float | int | None]`, `summarize_years(report: pd.DataFrame) -> dict[str, dict]`, and `summarize_stability(report: pd.DataFrame) -> dict`.

- [ ] **Step 1: Write failing tests for metric formulas**

```python
def test_after_cost_absolute_metrics_use_return_minus_cost():
    report = frame(return_=[0.02, -0.01, 0.01], cost=[0.001] * 3)
    metrics = summarize_period(report)
    net = pd.Series([0.019, -0.011, 0.009])
    assert metrics["annualized_return"] == pytest.approx(net.mean() * 250)
    assert metrics["annualized_volatility"] == pytest.approx(net.std(ddof=1) * 250**0.5)
    assert metrics["sharpe_ratio"] == pytest.approx(net.mean() / net.std(ddof=1) * 250**0.5)
    assert metrics["calmar_ratio"] == pytest.approx(metrics["annualized_return"] / abs(metrics["max_drawdown"]))
```

- [ ] **Step 2: Write failing tests for continuous calendar-year splitting**

```python
def test_year_summary_marks_partial_years_and_counts_only_2021_to_2025():
    summary = summarize_years(report_spanning_2020_to_2026())
    assert summary["2020"]["partial_year"] is True
    assert summary["2021"]["partial_year"] is False
    assert summary["2026"]["partial_year"] is True
```

- [ ] **Step 3: Verify tests fail**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_strategy_stability_metrics.py -q`

Expected: import failure because `strategy_stability_metrics.py` does not exist.

- [ ] **Step 4: Implement finite, after-cost metric functions**

Use `net_return = report["return"] - report["cost"]`; arithmetic annualization is `mean × 250`; annualized volatility uses sample standard deviation; Sharpe assumes zero risk-free rate; max drawdown uses the compounded net-return curve; Calmar is empty when drawdown is zero; one-way turnover is `mean(turnover) × 250 / 2`. Include benchmark cumulative return only as context.

- [ ] **Step 5: Run tests and commit**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_strategy_stability_metrics.py -q`

Expected: all tests pass.

Commit: `feat(backtest): add full-period stability metrics`

---

### Task 2: Hash-Verified Full Prediction Bundle

**Files:**
- Create: `backtest/scripts/prepare_strategy_stability_predictions.py`
- Create: `tests/backtest/test_prepare_strategy_stability_predictions.py`

**Interfaces:**
- Consumes: `prediction_manifest.json`, `<model-ref>/csi1000_valid.pkl`, and `<model-ref>/csi1000_test.pkl` from the completed Phase S experiment.
- Produces: `compose_prediction(valid_path, test_path, valid_entry, test_entry) -> tuple[pd.DataFrame, dict]` and a manifest containing source SHA/index SHA plus composed SHA/index SHA.

- [ ] **Step 1: Write failing composition tests**

```python
def test_compose_prediction_is_sorted_unique_and_covers_exact_full_period():
    full, audit = compose_prediction(valid, test, valid_entry, test_entry)
    assert full.index.is_monotonic_increasing
    assert not full.index.has_duplicates
    assert full.index.get_level_values("datetime").min() == pd.Timestamp("2020-01-13")
    assert full.index.get_level_values("datetime").max() == pd.Timestamp("2026-07-31")
    assert audit["sources"][0]["prediction_sha256"] == valid_entry["prediction_sha256"]
```

- [ ] **Step 2: Write failing tests for SHA mismatch, overlapping index, model/pool mismatch, and missing dates**

Each case must raise `ValueError` before writing a composed file.

- [ ] **Step 3: Verify tests fail**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_prepare_strategy_stability_predictions.py -q`

- [ ] **Step 4: Implement composition and CLI**

CLI output root is `backtest/experiments/strategy-stability/20260801_full_period`; write `predictions/<model-ref>/csi1000_full.pkl` and `prediction_manifest.json`. Reuse `sha256_file()` and `prediction_index_sha256()`.

- [ ] **Step 5: Run tests and commit**

Commit: `feat(backtest): compose frozen full-period predictions`

---

### Task 3: Diagnostic Runner and Config Generation

**Files:**
- Create: `backtest/scripts/run_strategy_stability.py`
- Create: `tests/backtest/test_run_strategy_stability.py`
- Modify: `backtest/scripts/run_strategy_sweep.py`
- Modify: `backtest/scripts/phase_s_protocol.py`

**Interfaces:**
- Consumes: composed prediction, composed manifest entry, frozen source config, `strategy_grid(model_ref)`, and Task 1 metric functions.
- Produces: configs in `backtest/configs/strategy-stability/<model-ref>/`, `full_results.json`, and comparison Markdown with no winner field.

- [ ] **Step 1: Write failing config tests**

Assert CSI1000, SH000852, 500,000 account, live costs, `segments.test == ["2020-01-13", "2026-07-31"]`, and unchanged candidate strategy kwargs.

- [ ] **Step 2: Write failing result-contract tests**

```python
def test_diagnostic_payload_has_all_candidates_baseline_first_and_no_winner():
    payload = build_payload("b6-m", rows)
    assert len(payload["all_rows"]) == 22
    assert payload["all_rows"][0]["candidate_id"] == "topk-t10-d2-h1"
    assert "winner" not in payload and "selected_candidate_id" not in payload
```

Also assert row metrics contain no IR keys and non-finite metrics become `invalid` with an error.

- [ ] **Step 3: Verify tests fail**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_run_strategy_stability.py -q`

- [ ] **Step 4: Implement runner**

For each candidate, write an exact full-period config, call `run_pred_backtest.py --skip-pred-copy`, verify the source prediction SHA from session metadata, read `report_normal.csv`, and attach Task 1 full/year metrics. Support `--resume-summary` so failed candidates can be rerun without discarding attempt history.

- [ ] **Step 5: Run tests and commit**

Commit: `feat(backtest): run continuous strategy stability diagnostics`

---

### Task 4: Diagnostic Registry Lifecycle and Separate HTML

**Files:**
- Create: `backtest/scripts/register_strategy_stability.py`
- Create: `backtest/scripts/build_strategy_stability_report.py`
- Create: `tests/backtest/test_register_strategy_stability.py`
- Create: `tests/backtest/test_build_strategy_stability_report.py`

**Interfaces:**
- Consumes: composed prediction manifest and two `full_results.json` files.
- Produces: registry rows `strategy-stability-full-period/b1-m` and `/b6-m`, plus `backtest/experiments/strategy_stability_report.html`.

- [ ] **Step 1: Write failing preregistration/finalization tests**

Assert the grid and metric names are frozen before execution, final rows contain all candidates and failures, conclusion is `diagnostic_no_selection`, `cleanup_retention_eligible` is false, and neither row has a selected candidate.

- [ ] **Step 2: Write failing HTML tests**

Assert two model sections, B1-S first, six requested metrics, calendar-year tables, partial-year labels, and the six-row B6 `d2/d3 × h5/h10/h20` neighborhood. Assert no table header equals `IR` and the existing report path is untouched.

- [ ] **Step 3: Verify tests fail**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_register_strategy_stability.py tests/backtest/test_build_strategy_stability_report.py -q`

- [ ] **Step 4: Implement atomic registry state transitions and HTML**

Preregister each row, bind only identity-matched full-period summaries, preserve original strategy-sweep rows byte-for-byte except for JSONL serialization, and build HTML solely from the two diagnostic registry rows.

- [ ] **Step 5: Run tests and commit**

Commit: `feat(backtest): register and report strategy stability`

---

### Task 5: Diagnostic-Aware Cleanup

**Files:**
- Modify: `backtest/scripts/cleanup_experiment_artifacts.py`
- Modify: `tests/backtest/test_cleanup_experiment_artifacts.py`

**Interfaces:**
- Consumes: registry Phase S rows with `conclusion == "diagnostic_no_selection"`.
- Produces: a cleanup plan that skips winner validation for diagnostics and retains no diagnostic result/MLflow directories.

- [ ] **Step 1: Write failing cleanup tests**

Create a complete formal Phase S row, a diagnostic row with no winner, and diagnostic run directories. Assert the plan retains the existing 17 formal directories, deletes diagnostic directories, and has no errors.

- [ ] **Step 2: Verify the test fails**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_cleanup_experiment_artifacts.py -q`

- [ ] **Step 3: Implement explicit diagnostic skip**

Skip winner-matrix validation only when phase is S, conclusion is `diagnostic_no_selection`, and cleanup retention eligibility is false. Any partially matching row remains an error.

- [ ] **Step 4: Run tests and commit**

Commit: `fix(backtest): clean non-selecting strategy diagnostics safely`

---

### Task 6: Freeze Protocol and Generate Full Predictions

**Files:**
- Create: `backtest/experiments/strategy-stability/20260801_full_period/protocol.json`
- Create: `backtest/experiments/strategy-stability/20260801_full_period/prediction_manifest.json`
- Modify: `backtest/experiments/registry.jsonl`

**Interfaces:**
- Consumes: Tasks 2 and 4 CLIs.
- Produces: immutable protocol/prediction hashes and two preregistered diagnostic rows before any backtest starts.

- [ ] **Step 1: Generate and audit composed predictions**

Run the preparation CLI with both model refs. Verify two files, exact 2020-01-13/2026-07-31 coverage, no duplicate indices and matching source hashes.

- [ ] **Step 2: Preregister both diagnostic grids**

Run the registration CLI in preregister mode and confirm registry states are `preregistered` with 18/22 candidates.

- [ ] **Step 3: Commit protocol and preregistration**

Commit: `exp(backtest): preregister full-period stability diagnostic`

---

### Task 7: Execute 40 Full-Period CSI1000 Backtests

**Files:**
- Create: `backtest/configs/strategy-stability/b1-m/*.yaml`
- Create: `backtest/configs/strategy-stability/b6-m/*.yaml`
- Create: `backtest/experiments/strategy-stability/20260801_full_period/b1-m/full_results.json`
- Create: `backtest/experiments/strategy-stability/20260801_full_period/b6-m/full_results.json`

**Interfaces:**
- Consumes: Task 3 runner and Task 6 prediction bundle.
- Produces: all 40 candidate outcomes and yearly metrics.

- [ ] **Step 1: Run B1-M and B6-M diagnostics in parallel**

Use `/opt/anaconda3/envs/qlib/bin/python`, one process per model, with the composed manifest explicitly supplied. Do not run more than the two model-level processes concurrently.

- [ ] **Step 2: Audit completeness and metric finiteness**

Confirm 18/22 unique candidates, B1-S first, every result has full-period and 2020～2026 metrics or an explicit failed/invalid status, and no metric key/header contains IR.

- [ ] **Step 3: Resume only failures if needed**

Use the same frozen bundle and `--resume-summary`; preserve all prior attempts.

---

### Task 8: Finalize Registry, Report, Cleanup and Verification

**Files:**
- Modify: `backtest/experiments/registry.jsonl`
- Create: `backtest/experiments/strategy_stability_report.html`
- Modify: `backtest/experiments/report.html`

**Interfaces:**
- Consumes: Tasks 4, 5 and 7 outputs.
- Produces: final diagnostic HTML and a clean retained artifact set.

- [ ] **Step 1: Finalize both diagnostic registry rows**

Bind the two summaries and assert `diagnostic_no_selection`, no selected candidate, exact account/date/prediction hashes, and original formal Phase S winners unchanged.

- [ ] **Step 2: Generate and structurally inspect both HTML reports**

Use BeautifulSoup to assert baseline-first order, metric/table presence, B6 neighborhood rows, partial-year labels and absence of IR headers.

- [ ] **Step 3: Run cleanup dry-run and apply**

Dry-run must have zero errors and select all diagnostic result/MLflow sessions for deletion while preserving the existing 17 formal result and 17 MLflow directories. Apply only after those assertions pass.

- [ ] **Step 4: Run focused regression verification**

Run all new tests plus the existing Phase S protocol, prediction, sweep, registry, report and cleanup suites. Run `git diff --check` and assert a clean registry parse with `allow_nan=False` semantics.

- [ ] **Step 5: Request independent code review**

Review the implementation against the approved spec. Fix all Critical/Important findings and rerun verification.

- [ ] **Step 6: Commit final experiment artifacts**

Commit: `exp(backtest): complete full-period strategy stability diagnostic`
