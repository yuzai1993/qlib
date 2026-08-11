# Main SELL and PR49 Retest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare auditable 2026-08-12 retests for one main-strategy prType=11 SELL and one standalone prType=49 BUY.

**Architecture:** Keep the paths independent. The existing main replacement tool supersedes the unclaimed normal BUY batch, while the standalone PR49 script gains a small date/time state gate that permits a staged request but invokes `passorder` only inside its configured window.

**Tech Stack:** QMT Python strategy runtime, Python 3.12 tests, pytest, SMB atomic files, SQLite live ledger.

## Global Constraints

- Main SELL is exactly `601326.SH`, 100 shares, `CLOSE_AUCTION_LIMIT`/QMT prType=11.
- PR49 BUY is exactly `688223.SH`, 100 shares, QMT prType=49, trade date 2026-08-12.
- No legacy marker files and no automatic retry after `PASSORDER_ATTEMPT`.
- Preserve superseded main artifacts and the rejected PR49 request as evidence.
- Do not stage unrelated dirty-worktree files.

---

### Task 1: Add PR49 request date/time gating

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_pr49_debug.py`
- Create: `tests/live_trading/test_qmt_pr49_debug.py`

**Interfaces:**
- Consumes: request fields `request_id`, `trade_date`, `side`, `stock_code`, `quantity`.
- Produces: `request_action(request, current_date, current_time) -> str`, returning `WAIT_DATE`, `WAIT_WINDOW`, `SUBMIT`, or `EXPIRE`.

- [ ] Write tests for future-date waiting, before-window waiting, in-window submission, after-window expiry, past-date expiry, and exactly one passorder call.
- [ ] Run `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_pr49_debug.py -q` and verify the tests fail before implementation.
- [ ] Implement the pure decision function, bounded wait logging, exact account binding, sanitized attempt arguments, and terminal processing behavior.
- [ ] Run the focused test and verify zero failures.

### Task 2: Verify and deploy both QMT strategies

**Files:**
- Copy: `live_trading/qmt_strategy/qmt_signal_bridge.py` to `/Volumes/qmt_bridge/strategy/qmt_signal_bridge.py`
- Copy: `live_trading/qmt_strategy/qmt_pr49_debug.py` to `/Volumes/qmt_bridge/strategy/qmt_pr49_debug.py`

**Interfaces:**
- Consumes: tested repository sources.
- Produces: byte-identical shared deployment sources verified by SHA-256.

- [ ] Run `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading -q` and require zero failures.
- [ ] Copy each source through a temporary file and atomic rename on SMB.
- [ ] Compare repository and shared SHA-256 values and require exact matches.

### Task 3: Publish the two 2026-08-12 test inputs

**Files:**
- Move/archive: existing `/Volumes/qmt_bridge/inbox/signal_20260812_csi1000_b6m_b2s_postclose_real_001.*`
- Create: replacement main SELL signal pair through `override_main_signal.py --replace-source`
- Create: `/Volumes/qmt_bridge/pr49_debug/request.pending.json` with exclusive creation

**Interfaces:**
- Consumes: live holdings from `csi1000_b6m_b2s_postclose_real.db` and the two tested QMT scripts.
- Produces: one active main SELL batch and one staged PR49 BUY request.

- [ ] Confirm the main source batch remains wholly in inbox, no matching processing artifacts exist, and the ledger has at least 100 shares of `601326.SH`.
- [ ] Replace sequence 001 with sequence 901 using stock `601326.SH`, quantity 100, and reason `prtype11_sell_retest`.
- [ ] Confirm the old PR49 request remains processed, then exclusively stage pending request ID `PR49B20260812688223` for trade date 2026-08-12; only the new QMT source activates it.
- [ ] Decode and validate both artifacts, confirm no duplicate request ID in the event log, and run the evening monitor.

### Task 4: Commit and hand off Windows deployment

**Files:**
- Commit only the PR49 source, PR49 tests, corrected design, and this plan.

**Interfaces:**
- Produces: reviewed commit plus exact Windows copy/compile/restart instructions.

- [ ] Re-run the focused PR49 tests and all `tests/live_trading` tests.
- [ ] Commit with `feat: gate pr49 debug orders by trade window`.
- [ ] Report exact shared source paths, hashes, active batch/request IDs, and the requirement to copy, compile, verify account binding, and restart both QMT strategies before 2026-08-12.
