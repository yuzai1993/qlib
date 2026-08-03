# Account Value Adjustment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve QMT available cash and economic total assets simultaneously when the broker reports a negative aggregate market value without ordinary positions.

**Architecture:** Seed a finite `value_adjustment` beside cash in `account_state`, include it in every economic-value calculation, and leave spendable-cash behavior unchanged. Extend broker reconciliation to compare QMT's aggregate-minus-position residual with the stored adjustment, then deploy an exact account bootstrap under Shadow safeguards.

**Tech Stack:** Python 3.12, SQLite, PyYAML, pytest, Bash cron wrappers.

## Global Constraints

- Use exact opening cash `9,949,714.06`, adjustment `-681,126.98`, and economic value `9,268,587.08`.
- Keep `live.broker_environment=SIMULATION`, `live.allow_real_money=false`, and `live.default_mode=SIMULATE`.
- Do not create `LIVE_OK` and do not set `LIVE_TRADING_CONFIRM`.
- Seed the adjustment only on a fresh unused ledger; never rewrite existing history from config.
- Preserve backward compatibility for configs and databases without an adjustment by treating it as zero.

---

### Task 1: Ledger bootstrap and configuration contract

**Files:**
- Modify: `tests/live_trading/test_fill_importer.py`
- Modify: `tests/live_trading/test_live_config.py`
- Modify: `live_trading/modules/fill_importer.py`
- Modify: `live_trading/modules/live_config.py`
- Modify: `live_trading/scripts/run_import_fills.py`
- Modify: `live_trading/scripts/run_publish_signals.py`

**Interfaces:**
- Produces: `LiveRecorder(..., opening_value_adjustment: float | None = None)`
- Produces: `LiveRecorder.get_value_adjustment() -> float`
- Consumes: `account.opening_value_adjustment` from a standalone live YAML.

- [ ] **Step 1: Write failing tests** for exact finite adjustment seeding, zero fallback, immutable reopening, used-ledger rejection, and positive economic opening value validation.
- [ ] **Step 2: Run focused tests and verify RED** with missing constructor/config behavior.
- [ ] **Step 3: Implement minimal account-state seeding and validation**, then pass the new argument from import and publish entry points.
- [ ] **Step 4: Run focused tests and verify GREEN.**
- [ ] **Step 5: Commit the ledger/config contract.**

### Task 2: Economic value consumers and snapshot persistence

**Files:**
- Modify: `tests/live_trading/test_run_publish_signals.py`
- Modify: `tests/live_trading/test_snapshot.py`
- Modify: `tests/live_trading/test_monitor_store.py`
- Modify: `tests/live_trading/test_monitor_web_api.py`
- Modify: `live_trading/scripts/run_publish_signals.py`
- Modify: `live_trading/modules/snapshot.py`
- Modify: `live_trading/modules/monitor_store.py`
- Modify: `live_trading/scripts/run_monitor.py`
- Modify: `live_trading/web/api.py`

**Interfaces:**
- Produces: `account_value(..., value_adjustment=0.0) -> float` for publish sizing.
- Produces: `build_snapshot(..., account_value_adjustment=0.0)`.
- Persists: `daily_snapshot.account_value_adjustment REAL NOT NULL DEFAULT 0`.

- [ ] **Step 1: Write failing tests** proving a negative adjustment reduces order-sizing NAV without changing cash, appears in snapshots/reports/API, and migrates old databases.
- [ ] **Step 2: Run focused tests and verify RED.**
- [ ] **Step 3: Implement the minimal value calculation, snapshot column migration, reporting, and API exposure.**
- [ ] **Step 4: Run focused tests and verify GREEN.**
- [ ] **Step 5: Commit the economic-value consumers.**

### Task 3: Broker residual reconciliation

**Files:**
- Modify: `tests/live_trading/test_fill_importer.py`
- Modify: `tests/live_trading/test_pipeline_monitor.py`
- Modify: `live_trading/modules/fill_importer.py`
- Modify: `live_trading/modules/pipeline_monitor.py`
- Modify: `live_trading/scripts/run_monitor.py`

**Interfaces:**
- Produces: `LiveRecorder.get_broker_position_market_values(trade_date) -> dict[str, float | None]`.
- Extends: `check_broker_reconcile(..., ledger_value_adjustment=0.0, broker_position_market_values=None, value_tolerance=None)`.

- [ ] **Step 1: Write failing tests** for matching negative residual, material residual drift, and incomplete broker value fields.
- [ ] **Step 2: Run focused tests and verify RED.**
- [ ] **Step 3: Implement snapshot value access and fail-closed residual comparison when all required values exist.**
- [ ] **Step 4: Run focused tests and verify GREEN.**
- [ ] **Step 5: Commit reconciliation support.**

### Task 4: Account-specific parity and deployment configuration

**Files:**
- Modify: `tests/live_trading/test_backtest_parity.py`
- Modify: `tests/live_trading/test_live_config.py`
- Modify: `live_trading/modules/backtest_parity.py`
- Modify: `live_trading/configs/csi1000_b6m_b2s_postclose.yaml`
- Modify: `backtest/configs/csi1000_b6m_b2s_postclose_parity.yaml`
- Modify: `live_trading/README.md`

**Interfaces:**
- Changes parity account comparison to `opening_cash + opening_value_adjustment`.

- [ ] **Step 1: Write a failing parity test** showing that the economic opening value, not spendable cash alone, must match `backtest.account`.
- [ ] **Step 2: Run focused tests and verify RED.**
- [ ] **Step 3: Implement parity derivation and set the exact account values in both configs; document account switching.**
- [ ] **Step 4: Run focused tests and verify GREEN.**
- [ ] **Step 5: Commit configuration and documentation.**

### Task 5: Verification, merge, and Shadow deployment

**Files:**
- Create at runtime: `live_trading/data/csi1000_b6m_b2s_postclose.db`
- Update outside Git: user crontab.

**Interfaces:**
- Consumes the completed account-value implementation and existing cron wrappers.
- Produces a seeded local ledger and installed weekday cron entries.

- [ ] **Step 1: Run the full live-trading suite, parity gate, shell syntax checks, and `git diff --check`.**
- [ ] **Step 2: Merge the verified branch into `main` and repeat the focused verification from `main`.**
- [ ] **Step 3: Initialize the absent database through the production import entry point and inspect `account_state`, positions, batches, and snapshots.**
- [ ] **Step 4: Install the 20:00 postclose, 21:30 publish, and 22:30 evening cron entries while removing the superseded 16:30 data-update entry; read back the crontab.**
- [ ] **Step 5: Run Shadow preflight and confirm no `LIVE_OK`, no LIVE confirmation, no queued orders, exact ledger values, bridge health, data freshness, and actionable logs.**
