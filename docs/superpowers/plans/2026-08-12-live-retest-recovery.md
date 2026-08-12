# Live Retest Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the two one-lot validation failures and stage replacements without exposing the normal BUY batch.

**Architecture:** Keep tracked QMT sources fail-closed and generate runtime-bound deployment copies atomically. Add an explicit QMT timer to the standalone PR49 strategy while reusing its existing dated single-consumption handler.

**Tech Stack:** Python, pytest, QMT `schedule_run`/`run_time`, SMB atomic files, SQLite ledger.

## Global Constraints

- Main retest is SELL `601326.SH`, exactly 100 shares, prType 11.
- PR49 retest is BUY `688223.SH`, exactly 100 shares, prType 49.
- Repository sources contain no real account number.
- No normal BUY batch may remain active before both validations finish.
- Preserve failed and superseded artifacts as audit evidence.

---

### Task 1: Runtime-bound QMT deployment renderer

**Files:**
- Create: `live_trading/scripts/render_qmt_runtime.py`
- Create: `tests/live_trading/test_render_qmt_runtime.py`

**Interfaces:**
- Consumes: fail-closed source path, output path, account ID, expected cash.
- Produces: `render_main_source(...)` and `render_pr49_source(...)` deployment text with exact single-setting replacements.

- [ ] Write tests proving the main output selects REAL execution, enables real money, disables the empty-position bootstrap gate, injects expected cash and account locally, while the input template remains unchanged.
- [ ] Write a test proving the PR49 output injects the account without changing its timer/window settings.
- [ ] Run the focused test and require the expected missing-module/function failure.
- [ ] Implement exact-one replacement validation, atomic output, and a CLI that reads `QMT_REAL_ACCOUNT_ID` rather than accepting a tracked secret.
- [ ] Run the focused tests and require zero failures.

### Task 2: PR49 timer-driven execution

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_pr49_debug.py`
- Modify: `tests/live_trading/test_qmt_pr49_debug.py`

**Interfaces:**
- Produces: `timer_callback(ContextInfo)` and `_register_timer(ContextInfo)`.
- QMT schedule: first wakeup `YYYYMMDD150500`, interval three seconds, timer name `qlib_pr49_poll`.

- [ ] Write tests proving `init` registers the timer and the registered callback submits an in-window request exactly once.
- [ ] Run the focused tests and require failure because no timer is registered.
- [ ] Implement `schedule_run` with the main bridge's `run_time` fallback and persistent `TIMER_REGISTERED` evidence.
- [ ] Run both PR49 focused tests and require zero failures.

### Task 3: Verify, deploy, and stage isolated retests

**Files:**
- Render: `/Volumes/qmt_bridge/strategy/qmt_signal_bridge.py`
- Render: `/Volumes/qmt_bridge/strategy/qmt_pr49_debug.py`
- Archive: active normal 2026-08-13 BUY pair through the existing replacement tool.
- Create: 2026-08-13 main SELL override and PR49 pending request.

- [ ] Run all `tests/live_trading` tests, compile QMT sources, and run `git diff --check`.
- [ ] Render both shared deployment sources using the locally stored account and current ledger cash; verify runtime settings without printing the account.
- [ ] Replace the 2026-08-13 normal batch with SELL `601326.SH` quantity 100, sequence 902.
- [ ] Exclusively stage PR49 request `PR49B20260813688223` for BUY `688223.SH` quantity 100.
- [ ] Confirm no active normal BUY batch remains and both failed 2026-08-12 artifacts remain archived.
- [ ] Commit only renderer, PR49 timer, tests, spec, and this plan; preserve unrelated worktree changes.
