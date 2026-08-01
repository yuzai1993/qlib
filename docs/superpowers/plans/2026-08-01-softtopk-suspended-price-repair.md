# SoftTopk Suspended-Price Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent non-finite prices on suspended holdings from poisoning SoftTopk portfolios and replace the ten invalid full-period CSI1000 diagnostics with finite repaired runs.

**Architecture:** Fix the source valuation helper, add explicit repair-only resume and registry transitions, and reuse the frozen prediction artifacts to rerun only invalid rows. Preserve all prior failure evidence while regenerating the registry-backed HTML.

**Tech Stack:** Python, pandas/numpy, Qlib backtest, pytest, JSONL registry, generated HTML.

## Global Constraints

- Phase S uses only frozen models under `backtest/models/baselines/`; no retraining.
- Pool is CSI1000; period is 2020-01-13 through 2026-07-31; account is 500,000.
- Fees and strategy parameters remain frozen by the existing protocol.
- Metrics are annualized return, Sharpe, Calmar, annualized volatility, maximum drawdown, and annualized one-way turnover; no IR metric.
- This is a non-selecting diagnostic and cannot change a baseline or live configuration.
- Use `/opt/anaconda3/envs/qlib/bin/python`; never invoke Qlib multiprocessing through stdin/heredoc.

---

### Task 1: Repair non-finite holding valuation

**Files:**
- Modify: `tests/backtest/test_order_generator_missing_price.py`
- Modify: `qlib/contrib/strategy/order_generator.py:16-34`

**Interfaces:**
- Consumes: `Exchange.get_deal_price(...) -> float | None` and `Position.get_stock_price(stock_id) -> float`.
- Produces: `_calculate_current_stock_value(...) -> float` with finite-price fallback.

- [ ] Add a test whose exchange returns `math.nan` for a suspended holding and whose hand-derived expected portfolio value is `160.0`.
- [ ] Run `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_order_generator_missing_price.py -q` and confirm the new test fails because the value is NaN.
- [ ] Change the fallback condition to treat `None` or any non-finite numeric price as unavailable.
- [ ] Add an infinity case and run the same test file to green.

### Task 2: Add explicit repair resume and registry audit

**Files:**
- Modify: `tests/backtest/test_run_strategy_stability.py`
- Modify: `backtest/scripts/run_strategy_stability.py`
- Modify: `tests/backtest/test_register_strategy_stability.py`
- Modify: `backtest/scripts/register_strategy_stability.py`

**Interfaces:**
- Produces: `select_resume_candidates(..., retry_invalid: bool = False)` and CLI `--retry-invalid`.
- Produces: finalizer CLI `--repair-reason TEXT`, allowing only an audited `complete -> complete` replacement.

- [ ] Change the resume test to assert default selection retries only `failed`, while `retry_invalid=True` retries both `failed` and `invalid`.
- [ ] Run the runner test and confirm it fails on the missing keyword behavior.
- [ ] Implement the keyword and CLI flag, passing it into selection.
- [ ] Add a registry test starting from a complete row, binding a repaired result with a literal reason, and asserting the previous path/SHA are appended to `repair_history`.
- [ ] Run the registry test and confirm it fails before implementation.
- [ ] Implement the explicit repair transition while retaining normal overwrite rejection.
- [ ] Run both focused test files to green.

### Task 3: Rerun the ten invalid SoftTopk diagnostics

**Files:**
- Modify: `backtest/experiments/strategy-stability/20260801_full_period/b1-m/full_results.json`
- Modify: `backtest/experiments/strategy-stability/20260801_full_period/b6-m/full_results.json`
- Regenerate configs in: `backtest/configs/strategy-stability/{b1-m,b6-m}/`

**Interfaces:**
- Consumes the two frozen `csi1000_full.pkl` files and `prediction_manifest.json`.
- Produces merged result payloads with repaired rows and `previous_attempts`.

- [ ] Run `run_strategy_stability.py` for B1-M with its frozen prediction, base config, existing summary, and `--retry-invalid`.
- [ ] Run it for B6-M with the corresponding frozen inputs and `--retry-invalid`.
- [ ] Verify exactly four B1-M and six B6-M rows were replaced and all requested metrics are finite over 1,587 dates.

### Task 4: Finalize registry, report, cleanup, and verification

**Files:**
- Modify: `backtest/experiments/registry.jsonl`
- Modify: `backtest/experiments/strategy_stability_report.html`

**Interfaces:**
- Consumes repaired full result JSON.
- Produces audited registry entries and the standalone report.

- [ ] Finalize both rows with `--repair-reason "SoftTopk suspended-price NaN fallback repair"`.
- [ ] Regenerate `strategy_stability_report.html` from the registry.
- [ ] Run focused backtest tests, then the repository non-slow test suite from `tests/`.
- [ ] Run cleanup dry-run, inspect zero errors and exact targets, then run cleanup with `--apply`.
- [ ] Verify 40/40 diagnostic rows are successful, all required metrics are finite, prediction SHAs are unchanged, the report contains no invalid status, and `git status` contains only intended tracked changes.

