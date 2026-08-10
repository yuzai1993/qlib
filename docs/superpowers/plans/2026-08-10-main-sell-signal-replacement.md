# Main SELL Signal Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Atomically replace the unclaimed 2026-08-11 main BUY batch with one 100-share `CLOSE_AUCTION_LIMIT` SELL for `601326.SH`.

**Architecture:** Extend the existing `override_main_signal.py` operator tool with one narrowly scoped replacement path. It validates ledger holdings and bridge state, records the replacement, removes the original pair from the consumable inbox into a recoverable archive, publishes the SELL pair, and records SQLite supersession.

**Tech Stack:** Python 3.12, pathlib, Qlib live-trading signal schema/publisher/recorder, pytest.

## Global Constraints

- Do not add marker, confirmation-token, account-environment routing, or PR49 behavior.
- The original batch must be wholly unclaimed in `inbox`; any `processing` artifact fails closed.
- Sell exactly 100 shares of `601326.SH` on 2026-08-11 using `CLOSE_AUCTION_LIMIT`.
- Preserve the original files in a recoverable superseded archive.

---

### Task 1: Add the minimal replacement operation

**Files:**
- Modify: `live_trading/scripts/override_main_signal.py`
- Modify: `tests/live_trading/test_run_publish_signals.py`

**Interfaces:**
- Consumes: `LiveRecorder.get_positions()`, `record_publish_plan()`, `supersede_batch()` and `SignalPublisher.publish()`.
- Produces: `replace_unclaimed_batch(root, db_path, source_batch, stock_code, quantity, reason, operator, seq) -> pathlib.Path`.

- [ ] **Step 1: Write failing tests**

Add tests proving that replacement accepts a held stock absent from the current BUY batch, emits one SELL with `instrument_qlib="SH601326"`, archives the source pair, publishes only the replacement pair, and records `superseded_by`. Add failure cases for insufficient holdings and any matching processing artifact.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_run_publish_signals.py -q
```

Expected: new replacement tests fail before implementation.

- [ ] **Step 3: Implement the replacement**

Add a stock-code-to-Qlib conversion for `.SH`/`.SZ`, bridge preflight, recoverable archive moves, durable publish, and supersession. Expose it through `--replace-source`; retain the existing non-replacing behavior.

- [ ] **Step 4: Run focused and live-trading tests**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_run_publish_signals.py -q
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading -q
```

Expected: zero failures.

- [ ] **Step 5: Execute and verify the real bridge replacement**

Run the tool with source batch `20260811_csi1000_b6m_b2s_postclose_real_001`, stock `601326.SH`, quantity `100`, sequence `900`, the production DB, and `--replace-source`. Then decode the resulting JSONL, query both SQLite rows, list `inbox`, `processing`, and the superseded archive, and rerun the evening monitor.

- [ ] **Step 6: Commit the implementation**

Stage only the replacement implementation, tests, and this plan; preserve unrelated working-tree changes. Commit with `feat: safely replace unclaimed main signal batch`.
