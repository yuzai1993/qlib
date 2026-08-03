# Serial Live Pipeline and Stock Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start the CSI1000 workflow once at 20:00, run all three operational stages consecutively, and populate the name cache used by prediction monitoring.

**Architecture:** The existing Python scheduler remains the single owner of the cross-stage lock and atomic daily receipts, but loses all per-stage clock gates. The existing postclose wrapper gains one Tushare name-cache sub-stage; Web requests continue using the local SQLite join and never call Tushare.

**Tech Stack:** Python 3.12, Bash, SQLite, Tushare, FastAPI, pytest, macOS cron.

## Global Constraints

- Cron has exactly one weekday entry at `0 20 * * 1-5`.
- Stage order is postclose, publish, evening with no sleeps or intermediate schedules.
- A failed stage is recorded and contributes to the final non-zero exit, but later stages still run.
- Atomic daily receipts and the scheduler directory lock remain in place.
- Stock-name refresh failure must not suppress report, publish, or evening checks.
- Account, model, strategy, bridge protocol, Shadow gates, and loopback Web binding remain unchanged.

---

### Task 1: Immediate serial scheduler and one daily cron trigger

**Files:**
- Modify: `tests/live_trading/test_operational_wrappers.py`
- Modify: `tests/live_trading/test_live_config.py`
- Modify: `live_trading/scripts/run_scheduler.py`
- Modify: `live_trading/crontab.csi1000_postclose.example`
- Modify: `live_trading/configs/csi1000_b6m_b2s_postclose.yaml`
- Modify: `live_trading/README.md`

**Interfaces:**
- Produces: `run_pipeline(config_id: str, project_root: Path, now: datetime) -> int`.
- Produces: daily receipts under `live_trading/.scheduler/<config>/<date>/`.
- Invokes exact wrapper order: postclose, publish, evening.

- [ ] **Step 1: Replace the scheduler behavior tests** with literal trace assertions that one invocation at 20:00 produces `postclose`, `publish`, `evening`; postclose exit 2 still produces all three trace rows and overall status 1; a second invocation produces no extra trace; a pre-existing postclose receipt skips only that stage.
- [ ] **Step 2: Change the cron contract test** to require the literal command `0 20 * * 1-5 .../run_scheduler_cron.sh csi1000_b6m_b2s_postclose`, and change the active-config test to assert the obsolete multi-time `schedule` mapping is absent.
- [ ] **Step 3: Run the focused tests and verify RED** because the current scheduler gates stages by 20:00, 21:30, and 22:30 and the current cron runs every minute.
- [ ] **Step 4: Implement `run_pipeline`** by removing schedule parsing and time comparisons, preserving the directory lock, sequential `subprocess.run`, atomic receipt writes, failure accumulation, and receipt-based skipping.
- [ ] **Step 5: Update cron, active YAML, and README** to make 20:00 the only trigger and describe immediate serial execution and manual recovery.
- [ ] **Step 6: Run focused scheduler/config tests, Python compilation, Bash syntax, and `git diff --check`; verify GREEN.**
- [ ] **Step 7: Commit** with `feat(live): run postclose workflow as one serial job`.

### Task 2: Populate the stock-name cache in postclose

**Files:**
- Modify: `tests/live_trading/test_operational_wrappers.py`
- Modify: `live_trading/run_postclose_cron.sh`
- Modify: `live_trading/README.md`

**Interfaces:**
- Consumes: `live_trading/scripts/refresh_stock_names.py --config <id>`.
- Produces: refreshed `stock_names(stock_code, instrument, name)` rows before report and publish.

- [ ] **Step 1: Extend the real wrapper fixture** with a fake Python executable that records `stock_names`, and add assertions for `import`, `postmarket`, `update`, `stock_names`, `report` ordering.
- [ ] **Step 2: Add a failure case** where name refresh exits 3 and assert report still runs, the wrapper exits non-zero, and the log summary contains `stock_names=3`.
- [ ] **Step 3: Run the focused wrapper tests and verify RED** because the current postclose wrapper never calls the refresh command.
- [ ] **Step 4: Source `~/.qlib_live_env`, resolve `QLIB_LIVE_PYTHON`, and add the `stock_names` sub-stage** after data update and before the conditional report; include its status in the summary without changing report gating.
- [ ] **Step 5: Update README** to document local cache refresh and non-blocking downstream behavior.
- [ ] **Step 6: Run focused wrapper and stock-name tests plus Bash syntax and verify GREEN.**
- [ ] **Step 7: Commit** with `fix(live): refresh prediction stock names after close`.

### Task 3: Full verification and production deployment

**Files:**
- Runtime update: user crontab.
- Runtime update: `live_trading/data/csi1000_b6m_b2s_postclose.db` via the existing refresh command.

**Interfaces:**
- Consumes: verified 20:00 cron template and `refresh_stock_names.py`.
- Produces: a single installed cron entry and named prediction API responses.

- [ ] **Step 1: Run `pytest tests/live_trading -q`, parity validation, Python compilation, Bash syntax checks, plist lint, and `git diff --check`.**
- [ ] **Step 2: Install and read back the updated crontab; assert exactly one scheduler command at 20:00 and no 21:30, 22:30, or every-minute entries.**
- [ ] **Step 3: Run the stock-name refresh once with the production config and verify the SQLite cache is non-empty and the latest predictions join to non-empty names.**
- [ ] **Step 4: Verify `/api/predictions?limit=5` contains names, `/api/overview` returns HTTP 200, LaunchAgent is running, and the listener remains `127.0.0.1:8081`.**
- [ ] **Step 5: Recheck `LIVE_RUN_MODE=SIMULATE`, confirmation unset, no `LIVE_OK`, Git status, and recent commits.**
