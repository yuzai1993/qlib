# CSI1000 Real Account One-Lot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely publish and execute a one-lot REAL batch for account 8890116049 on 2026-08-06.

**Architecture:** Keep the strategy/model unchanged while introducing an isolated REAL config and ledger. Extend each protocol boundary to recognize REAL only when its own explicit safety switch is present, and add a broker-side first-trade preflight before passorder.

**Tech Stack:** Python 3.12, QMT Python 3.6, YAML, SQLite, pytest, bash/cron/launchd.

## Global Constraints

- Account 8890116049 starts empty with available cash and total assets of 1,000,000 yuan.
- Every order remains capped at 100 shares.
- REAL requires process confirmation, QMT real-money opt-in, exact account match, and a date-scoped LIVE_OK marker.
- Simulation history must not seed or share the REAL ledger.

---

### Task 1: REAL protocol and ledger support

**Files:** `live_trading/modules/live_config.py`, `live_trading/modules/signal_schema.py`, `live_trading/modules/fill_importer.py`, `live_trading/scripts/run_publish_signals.py`, matching `tests/live_trading/` files.

- [ ] Add failing tests for valid REAL configuration, environment-specific account lookup, REAL batch durability, and fail-closed mismatches.
- [ ] Run focused tests and confirm expected failures.
- [ ] Implement the minimum paired REAL gates and run the focused tests green.

### Task 2: Isolated REAL runtime configuration

**Files:** create `live_trading/configs/csi1000_b6m_b2s_postclose_real.yaml`; update config and operational tests.

- [ ] Assert the REAL strategy ID, 1,000,000 opening cash, zero adjustment, LIVE mode, REAL environment, and unique database path.
- [ ] Add the standalone config and initialize its empty ledger.

### Task 3: QMT broker-side REAL preflight

**Files:** `live_trading/qmt_strategy/qmt_signal_bridge.py`, `tests/live_trading/test_qmt_bridge_logic.py`, `live_trading/qmt_strategy/README_QMT.md`.

- [ ] Add failing tests for real-money opt-in, exact account, empty-position and cash-tolerance checks.
- [ ] Implement preflight rejection before any passorder while retaining `MAX_ORDER_QUANTITY=100`.
- [ ] Run bridge tests and copy the verified script to `/Volumes/qmt_bridge/strategy/`.

### Task 4: Runtime cutover and verification

**Files:** cron template, LaunchAgent template, README, private `~/.qlib_live_env`, SMB state/inbox.

- [ ] Point cron and monitoring at the REAL config and install both runtime definitions.
- [ ] Ensure no 2026-08-06 simulation batch or authorization can execute.
- [ ] Dry-run the REAL 2026-08-06 plan, then publish only after all tests pass.
- [ ] Verify config, ledger, bridge payload, cron, monitor health, one-lot cap and date-scoped authorization state.
