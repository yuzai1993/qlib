# Live Account Cash Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the live funds account to `8881352838`, replace the already-published 2026-07-16 batch, and make unavailable QMT account data and all-skipped execution fail visibly.

**Architecture:** Preserve the signal header as the account source of truth. Make the QMT account boundary return `None` for unavailable data while retaining `0.0` as a valid broker balance, then retry until close. Add a business-level monitor rule for a complete but entirely skipped LIVE batch and allow postmarket reconciliation to run before the daily qlib update.

**Tech Stack:** Python 3.6-compatible QMT strategy code, Python 3.11 monitor code, pytest, SQLite, JSONL shared bridge.

## Global Constraints

- The exact funds account is `8881352838` and account type remains `STOCK`.
- Do not create `LIVE_OK_2026-07-16`.
- Do not modify the archived 2026-07-15 fills or reconstruct trades.
- Do not back up the formal database; the user explicitly requested a clean pre-live state without backups.
- Publish the corrected 2026-07-16 plan before archiving or superseding its existing `001` plan.
- Keep QMT source ASCII-only and Python 3.6 compatible.
- The Windows QMT editor copy must be compiled and restarted before creating the next LIVE_OK.

---

### Task 1: Fail closed when QMT account cash is unavailable

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py:22-32,423-432,619-646,658-683`
- Test: `tests/live_trading/test_qmt_bridge_logic.py`

**Interfaces:**
- Consumes: `get_trade_detail_data(account_id, ACCOUNT_TYPE, "ACCOUNT")`.
- Produces: `_get_available_cash(account_id) -> float | None`, where non-negative floats are valid broker cash and `None` means unavailable account data.

- [ ] **Step 1: Write failing cash-boundary tests**

Add tests that install fake QMT account results:

```python
def test_available_cash_distinguishes_empty_query_from_real_zero(
    bridge, monkeypatch,
):
    monkeypatch.setattr(
        bridge, "get_trade_detail_data", lambda *args: [], raising=False,
    )
    assert bridge._get_available_cash("8881352838") is None

    class Account:
        m_strAccountID = "8881352838"
        m_dAvailable = 0.0

    monkeypatch.setattr(
        bridge, "get_trade_detail_data", lambda *args: [Account()],
        raising=False,
    )
    assert bridge._get_available_cash("8881352838") == 0.0
```

Add a buy-phase test with `_get_available_cash` returning `None`; assert no order is added to `batch.submitted` and no fill is written during the trading window. Add a close-finalization test asserting an unsubmitted BUY becomes `ERROR` with message `account cash unavailable at close`.

- [ ] **Step 2: Verify RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q tests/live_trading/test_qmt_bridge_logic.py -k 'available_cash or cash_unavailable'
```

Expected: the empty query test fails because the current function returns `0.0`; the close test fails because current finalization returns `EXPIRED`.

- [ ] **Step 3: Write the minimal implementation**

Import `math`. Change `_get_available_cash` to return `None` for exceptions, empty results, missing/non-finite/negative `m_dAvailable`, or a non-empty `m_strAccountID` different from the requested id. Log the reason with the requested account id.

In the BUY phase use:

```python
cash = _get_available_cash(account_id)
if cash is None:
    return
batch.remaining_cash = cash
_save_active_state(batch)
```

In `_force_finalize_if_near_close`, write `ERROR` / `account cash unavailable at close` for an unsubmitted BUY when `batch.phase == "BUY"` and `batch.remaining_cash is None`; retain existing EXPIRED behavior for other unfinished orders.

- [ ] **Step 4: Verify GREEN and commit**

Run the full QMT bridge test file, then commit:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q tests/live_trading/test_qmt_bridge_logic.py
git add live_trading/qmt_strategy/qmt_signal_bridge.py tests/live_trading/test_qmt_bridge_logic.py
git commit -m "fix(live_trading): fail closed on unavailable QMT cash"
```

---

### Task 2: Alert on all-skipped LIVE execution and run postmarket on stale calendar

**Files:**
- Modify: `live_trading/modules/pipeline_monitor.py:10-88`
- Modify: `live_trading/scripts/run_monitor.py:330-365`
- Test: `tests/live_trading/test_pipeline_monitor.py`
- Test: `tests/live_trading/test_next_trade_date.py`

**Interfaces:**
- Consumes: active batch rows and fill rows containing `batch_id`, `mode`, `status`, `filled_qty`, and `message`.
- Produces: `Finding("ALL_ORDERS_SKIPPED", "CRIT", message)`.
- Produces: `_may_run_with_stale_calendar(stage, active_batches) -> bool`.

- [ ] **Step 1: Write failing monitor tests**

Update the `_fill` helper to include `batch_id=BATCH["batch_id"]`. Add:

```python
def test_postmarket_all_live_orders_skipped_is_critical():
    fills = [
        _fill(status="SKIPPED", qty=0),
        _fill(status="SKIPPED", code="000001.SZ", qty=0),
    ]
    findings = check_postmarket(
        "2026-07-15", [{**BATCH, "mode": "LIVE", "planned_orders": 2}],
        {BATCH["batch_id"]: {"planned": 2, "terminal": 2, "missing": 0}},
        fills, prev_positions={},
    )
    finding = next(f for f in findings if f.rule == "ALL_ORDERS_SKIPPED")
    assert finding.level == "CRIT"
```

Add a test proving a FILLED LIVE order does not trigger the rule. Add a pure gate test asserting `_may_run_with_stale_calendar("postmarket", [BATCH])` is true, while report and an empty batch list are false.

- [ ] **Step 2: Verify RED**

Run both monitor test files. Expected: failures for the missing finding and helper.

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q tests/live_trading/test_pipeline_monitor.py tests/live_trading/test_next_trade_date.py
```

- [ ] **Step 3: Implement all-skipped detection**

Select active LIVE batch ids, then related LIVE fills. If the number of relevant fills covers all planned orders and every row has `status == "SKIPPED"` and `filled_qty == 0`, append one CRIT finding. Build a sorted, deduplicated reason list from non-empty `message` values.

- [ ] **Step 4: Implement the calendar exception**

Add:

```python
def _may_run_with_stale_calendar(stage, active_batches):
    return stage == "postmarket" and bool(active_batches)
```

In `main`, load active batches once. When the date is absent from qlib calendar, continue only when this helper returns true; otherwise retain current holiday and DATA_STALE behavior.

- [ ] **Step 5: Verify GREEN and commit**

Run the command from Step 2, then commit the four files with:

```bash
git commit -m "fix(live_trading): alert when all live orders are skipped"
```

---

### Task 3: Correct the account and replace the 2026-07-16 plan

**Files and state:**
- Modify outside git: `~/.qlib_live_env`
- Modify runtime database: `live_trading/data/csi300_topk10_live.db`
- Modify shared state: `/Volumes/qmt_bridge/inbox`, `/Volumes/qmt_bridge/archive`

**Interfaces:**
- Consumes: `QMT_ACCOUNT_ID=8881352838`, `run_publish_signals.py --seq 2`, and `LiveRecorder.supersede_batch`.
- Produces: active batch `20260716_csi300_topk10_002` with account `8881352838`.

- [ ] **Step 1: Verify safety preconditions**

Assert `LIVE_OK_2026-07-16` does not exist, inbox contains the wrong-account `001` pair, and the formal database has only `001` for 2026-07-16.

- [ ] **Step 2: Correct the environment account**

Replace exactly `export QMT_ACCOUNT_ID="88813528"` with `export QMT_ACCOUNT_ID="8881352838"`, then read back only that line.

- [ ] **Step 3: Publish corrected plan before removing old plan**

Run:

```bash
QMT_ACCOUNT_ID=8881352838 LIVE_TRADING_CONFIRM=YES /opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_publish_signals.py --config csi300_topk10_live --trade-date 2026-07-16 --mode LIVE --seq 2
```

Verify the new header account, checksum, order count, durable plan, and inbox pair.

- [ ] **Step 4: Retire `001` only after `002` is durable**

Move the explicit `001` jsonl/done files from inbox to archive. Call:

```python
recorder.supersede_batch(
    "20260716_csi300_topk10_001",
    "20260716_csi300_topk10_002",
)
```

Verify only `002` is active and no 2026-07-16 LIVE_OK exists.

- [ ] **Step 5: Verify monitor projection**

Assert `/api/overview` returns account `8881352838` and active batch `002`; `/api/batches` returns `001` as SUPERSEDED and `002` as ACTIVE.

---

### Task 4: Full verification and Windows deployment handoff

**Files:**
- Verify: all files modified in Tasks 1-2
- Runtime handoff: `live_trading/qmt_strategy/qmt_signal_bridge.py`

**Interfaces:**
- Produces: tested Mac-side code and an exact Windows deployment requirement.

- [ ] **Step 1: Run complete regression**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q tests/live_trading tests/paper_trading/test_signal_generator.py
```

Expected: zero failures.

- [ ] **Step 2: Run syntax and repository checks**

```bash
/opt/anaconda3/envs/qlib/bin/python -m py_compile live_trading/modules/pipeline_monitor.py live_trading/scripts/run_monitor.py live_trading/qmt_strategy/qmt_signal_bridge.py
git diff --check
git status --short
```

- [ ] **Step 3: Verify formal and shared state**

Assert database integrity, one active corrected batch, wrong batch in archive, corrected batch in inbox, corrected account environment, and next-day LIVE_OK absent.

- [ ] **Step 4: Hand off required Windows action**

Tell the user to replace the QMT editor source with the committed `live_trading/qmt_strategy/qmt_signal_bridge.py`, compile, restart the strategy, and run an account query that shows `m_strAccountID=8881352838` and a non-empty `m_dAvailable` before creating `LIVE_OK_2026-07-16`.
