# Main Sell Verification and prType=49 Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an audited one-lot SELL verification for the active close-auction strategy, a durable PAUSED state, and an isolated `prType=49` one-lot BUY-to-next-day-SELL probe with evidence-complete QMT logging.

**Architecture:** Keep one SQLite account ledger because both strategy IDs trade account `8890116049`, but scope batches and reconciliation by strategy ID. Keep QMT file state isolated under `D:\qmt_bridge` and `D:\qmt_bridge\pr49_probe`; one fail-closed bridge source supports two explicitly configured execution profiles so recovery and logging cannot diverge. Operator batches are immutable recorded plans produced by a CLI, never raw JSONL edits.

**Tech Stack:** Python 3.10 (`/opt/anaconda3/envs/qlib/bin/python`), QMT built-in Python 3.6-compatible source, SQLite, YAML, JSONL/SMB protocol, Bash, pytest.

## Global Constraints

- The active account is REAL account `8890116049`; the ID remains outside Git and resolves from `QMT_REAL_ACCOUNT_ID`.
- Main remains `csi1000_b6m_b2s_postclose_real`, `CLOSE_AUCTION_LIMIT`, `prType=11`, submit time `14:57:05`.
- Probe ID is exactly `csi1000_pr49_one_lot_probe`, session `AFTER_HOURS_FIXED_PRICE`, signal type `AFTER_HOURS_CLOSE`, QMT `prType=49`.
- Both profiles retain `MAX_ORDER_QUANTITY=100`; no task removes or raises it.
- Probe roots are `D:\qmt_bridge\pr49_probe` and `/Volumes/qmt_bridge/pr49_probe`.
- Main authorization is `LIVE_OK_YYYY-MM-DD`; probe authorization is `PR49_LIVE_OK_YYYY-MM-DD`. Neither is created automatically or for a future date.
- If both authorization files exist for one date, both profiles fail closed before `passorder`.
- Main SELL verification must pass before the fixed-price probe is armed.
- After SELL verification, main stays PAUSED until a new explicit user instruction; data, prediction, report, and monitoring continue.
- A normal `passorder` return without a query-observed QMT order never becomes ACCEPTED and finalizes as ERROR containing `QMT order not observed after passorder`.
- Preserve unrelated Tushare working-tree changes; stage only files named by each task.
- Start implementation with `superpowers:using-git-worktrees` in a clean isolated worktree. The primary checkout currently has pre-existing Tushare edits in files that later tasks also touch; do not overwrite, stage, stash, or commit those primary-checkout edits.
- Do not create REAL batches, authorization markers, or actual QMT orders during implementation/tests.

---

### Task 1: Strategy-scoped account-ledger queries

**Files:**
- Modify: `live_trading/modules/fill_importer.py`
- Modify: `live_trading/scripts/run_publish_signals.py`
- Modify: `live_trading/scripts/run_monitor.py`
- Test: `tests/live_trading/test_fill_importer.py`
- Test: `tests/live_trading/test_signal_publisher.py`
- Test: `tests/live_trading/test_next_trade_date.py`

**Interfaces:**
- Produces: `get_batches_by_date(trade_date: str, strategy_id: str | None = None) -> list[dict]`.
- Produces: `get_active_batches_by_date(trade_date: str, strategy_id: str | None = None) -> list[dict]`.
- Produces: `get_unreconciled_active_live_batches_before(trade_date: str, strategy_id: str | None = None) -> list[dict]`.
- Consumes: existing `batches.strategy_id` and supersession semantics.

- [ ] **Step 1: Write failing shared-ledger tests**

Record main and probe batches for one date, then assert unfiltered queries return both and filtered queries return one:

~~~python
assert {b["strategy_id"] for b in recorder.get_active_batches_by_date(day)} == {
    "csi1000_b6m_b2s_postclose_real",
    "csi1000_pr49_one_lot_probe",
}
assert [b["batch_id"] for b in recorder.get_active_batches_by_date(
    day, strategy_id="csi1000_pr49_one_lot_probe",
)] == [probe_batch_id]
~~~

- [ ] **Step 2: Run tests and verify red state**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_fill_importer.py \
  tests/live_trading/test_signal_publisher.py \
  tests/live_trading/test_next_trade_date.py -q
~~~

Expected: FAIL from an unexpected `strategy_id` argument or cross-strategy result.

- [ ] **Step 3: Add parameterized SQL filtering**

Use bound parameters, never string interpolation:

~~~python
clauses = ["trade_date=?"]
params = [trade_date]
if strategy_id is not None:
    clauses.append("strategy_id=?")
    params.append(strategy_id)
~~~

Apply it to all three interfaces.

- [ ] **Step 4: Pass strategy ID from callers**

Pass `config["live"]["strategy_id"]` from prior-batch gates and `run_evening`/`run_postmarket`. Update fake recorders to accept and assert the argument.

- [ ] **Step 5: Verify and commit**

Run Step 2 again, then:

~~~bash
git diff --check -- live_trading/modules/fill_importer.py \
  live_trading/scripts/run_publish_signals.py live_trading/scripts/run_monitor.py \
  tests/live_trading/test_fill_importer.py tests/live_trading/test_signal_publisher.py \
  tests/live_trading/test_next_trade_date.py
git add live_trading/modules/fill_importer.py live_trading/scripts/run_publish_signals.py \
  live_trading/scripts/run_monitor.py tests/live_trading/test_fill_importer.py \
  tests/live_trading/test_signal_publisher.py tests/live_trading/test_next_trade_date.py
git commit -m "fix(live): scope batches by strategy id"
~~~

Expected: tests PASS and diff check exits 0.

---

### Task 2: Explicit execution profiles and probe config

**Files:**
- Create: `live_trading/modules/execution_profile.py`
- Create: `live_trading/configs/csi1000_pr49_one_lot_probe.yaml`
- Modify: `live_trading/modules/live_config.py`
- Modify: `live_trading/modules/signal_schema.py`
- Modify: `live_trading/modules/order_planner.py`
- Test: `tests/live_trading/test_live_config.py`
- Test: `tests/live_trading/test_signal_schema.py`
- Test: `tests/live_trading/test_order_planner.py`

**Interfaces:**
- Produces frozen `ExecutionProfile(name, signal_price_type, qmt_price_type, submit_after, cancel_at, finalize_at, snapshot_after, authorization_prefix)`.
- Produces `get_execution_profile(name: str) -> ExecutionProfile` for `CLOSE_AUCTION` and `AFTER_HOURS_FIXED_PRICE`.
- Produces probe config with `live.kind: OPERATOR_PROBE`, shared database, and isolated bridge root.
- Consumes `SignalOrder.price_type`; schema permits both types, while config/publisher prevents cross-combinations.

- [ ] **Step 1: Write failing profile/config tests**

~~~python
assert get_execution_profile("CLOSE_AUCTION").qmt_price_type == 11
assert get_execution_profile("CLOSE_AUCTION").signal_price_type == "CLOSE_AUCTION_LIMIT"
assert get_execution_profile("AFTER_HOURS_FIXED_PRICE").qmt_price_type == 49
assert get_execution_profile("AFTER_HOURS_FIXED_PRICE").signal_price_type == "AFTER_HOURS_CLOSE"
~~~

Also assert main rejects prType 49 and probe rejects prType 11, the main bridge root, or a strategy ID other than `csi1000_pr49_one_lot_probe`.

- [ ] **Step 2: Run focused tests and verify red state**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_live_config.py tests/live_trading/test_signal_schema.py \
  tests/live_trading/test_order_planner.py -q
~~~

Expected: FAIL because the profile/config is absent and schema rejects `AFTER_HOURS_CLOSE`.

- [ ] **Step 3: Implement the frozen profile table**

Use a frozen dataclass. Close auction uses `14:57:05/15:00:05/15:00:30/15:01:00/LIVE_OK_`; fixed price uses `15:05:00/15:28:00/15:30:00/15:31:00/PR49_LIVE_OK_`. Unknown names raise `ValueError`.

- [ ] **Step 4: Validate kind/profile pairs and parameterize planner**

`STRATEGY` accepts only close auction. `OPERATOR_PROBE` accepts only fixed price, REAL/LIVE, max 100, exact strategy ID, and a bridge root ending `/pr49_probe`. `OrderPlanner` accepts `signal_price_type`, defaulting to `CLOSE_AUCTION_LIMIT` for compatibility.

- [ ] **Step 5: Add minimal standalone probe YAML**

Include only account/live/storage/fees/monitor sections. Set storage to `live_trading/data/csi1000_b6m_b2s_postclose_real.db`; exclude model/parity because the generic predictor must not run this config.

- [ ] **Step 6: Verify and commit**

Run Step 2 and `git diff --check` for the listed files, then:

~~~bash
git add live_trading/modules/execution_profile.py \
  live_trading/configs/csi1000_pr49_one_lot_probe.yaml \
  live_trading/modules/live_config.py live_trading/modules/signal_schema.py \
  live_trading/modules/order_planner.py tests/live_trading/test_live_config.py \
  tests/live_trading/test_signal_schema.py tests/live_trading/test_order_planner.py
git commit -m "feat(live): define isolated execution profiles"
~~~

Expected: all focused tests PASS.

---

### Task 3: Immutable operator test-batch publisher

**Files:**
- Create: `live_trading/modules/operator_probe.py`
- Create: `live_trading/scripts/run_operator_probe.py`
- Modify: `live_trading/modules/fill_importer.py`
- Test: `tests/live_trading/test_operator_probe.py`

**Interfaces:**
- Produces frozen `OperatorProbeRequest(config_id, trade_date, stock_code, side, quantity, reason)`.
- Produces `build_operator_order(request, config, recorder, broker_trade_date) -> SignalOrder`.
- Produces `publish_operator_probe(request, config, recorder, publisher, account_id) -> pathlib.Path`.
- CLI: `--config --trade-date --stock-code --side BUY|SELL --quantity 100 --reason --publish`.
- Consumes `record_publish_plan`, `SignalPublisher`, Task 2 profile, ledger positions, and latest broker snapshot.

- [ ] **Step 1: Write failing validation and immutability tests**

Reject quantity 0/200/non-lot, unknown stock, SELL missing from ledger, SELL unavailable in broker snapshot, BUY already held, stale/missing broker snapshot, duplicate conflicting batch, and missing explicit `--publish`.

~~~python
order = build_operator_order(request, config, recorder, "2026-08-10")
assert order.side == "SELL"
assert order.quantity == 100
assert order.reason == "operator_sell_probe"
assert order.price_type == "CLOSE_AUCTION_LIMIT"
~~~

- [ ] **Step 2: Run new tests and verify import failure**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_operator_probe.py -q
~~~

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Add broker availability accessor**

Implement `get_broker_position_details(trade_date: str) -> dict[str, dict]` returning shares, `can_use_volume`, avg cost, and market value from the latest snapshot. Missing snapshots are an error, not zero positions.

- [ ] **Step 4: Implement deterministic immutable publication**

Use batch ID `<YYYYMMDD>_<strategy_id>_900` and `make_client_order_id(trade_date, 900, 1, side)`. Call `record_publish_plan` before `SignalPublisher.publish`. Identical retry is idempotent; conflicting content raises `SchemaError`.

- [ ] **Step 5: Add dry-run audit and explicit REAL gate**

Without `--publish`, print header/order/checksum JSON and perform no DB/SMB writes. With it, require `LIVE_TRADING_CONFIRM=YES`, resolved REAL account, writable root, and no conflict.

- [ ] **Step 6: Verify and commit**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_operator_probe.py tests/live_trading/test_fill_importer.py \
  tests/live_trading/test_signal_publisher.py -q
git diff --check -- live_trading/modules/operator_probe.py \
  live_trading/scripts/run_operator_probe.py live_trading/modules/fill_importer.py \
  tests/live_trading/test_operator_probe.py
git add live_trading/modules/operator_probe.py live_trading/scripts/run_operator_probe.py \
  live_trading/modules/fill_importer.py tests/live_trading/test_operator_probe.py
git commit -m "feat(live): add audited operator probe batches"
~~~

Expected: tests PASS and only named files are staged.

---

### Task 4: Audited main-plan preview and durable PAUSED state

**Files:**
- Create: `live_trading/modules/execution_state.py`
- Create: `live_trading/scripts/set_execution_state.py`
- Modify: `live_trading/modules/fill_importer.py`
- Modify: `live_trading/scripts/run_publish_signals.py`
- Modify: `live_trading/run_publish_cron.sh`
- Modify: `live_trading/modules/pipeline_monitor.py`
- Modify: `live_trading/scripts/run_monitor.py`
- Test: `tests/live_trading/test_execution_state.py`
- Test: `tests/live_trading/test_run_publish_signals.py`
- Test: `tests/live_trading/test_operational_wrappers.py`
- Test: `tests/live_trading/test_pipeline_monitor.py`

**Interfaces:**
- Produces SQLite table `execution_state(strategy_id PRIMARY KEY, state, reason, changed_at)`.
- Produces `get_execution_state(strategy_id) -> dict`, default `ACTIVE` without inserting.
- Produces `set_execution_state(strategy_id, state, reason, changed_at)`, accepting only ACTIVE/PAUSED.
- Produces `run_publish_signals.py --audit-preview PATH`, an atomic preview with no batch/SMB write.
- Consumes main publish wrapper and evening monitor.

- [ ] **Step 1: Write failing pause-path tests**

Assert PAUSED requires a reason, direct LIVE publication raises `ExecutionPausedError`, audit preview contains proposed BUY and zero SELL orders, and cron pause mode creates neither inbox files nor batch rows.

~~~python
state = recorder.get_execution_state(MAIN_ID)
assert state["state"] == "PAUSED"
assert preview["trade_date"] == "2026-08-11"
assert preview["sell_count"] == 0
~~~

- [ ] **Step 2: Run tests and verify red state**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_execution_state.py tests/live_trading/test_run_publish_signals.py \
  tests/live_trading/test_operational_wrappers.py tests/live_trading/test_pipeline_monitor.py -q
~~~

Expected: FAIL for missing state API and pause behavior.

- [ ] **Step 3: Add table/API and atomic preview**

Write previews to `live_trading/logs/<strategy_id>/previews/signal_<trade_date>.json` using a sibling temporary file and `os.replace`. Include strategy ID, signal/trade dates, generated timestamp, current positions, order count, BUY/SELL counts, and complete proposed orders.

- [ ] **Step 4: Branch cron explicitly on PAUSED**

Add `set_execution_state.py --get --config`. When PAUSED, `run_publish_cron.sh` invokes predictor with `--dry-run --audit-preview`, does not require `LIVE_TRADING_CONFIRM`, logs `publish paused preview-only`, and exits zero. Direct publish remains fail closed.

- [ ] **Step 5: Make evening monitoring recognize an audited pause**

Return OK only if state is PAUSED and preview date equals the expected next open date. Missing/stale preview remains WARN. PAUSED never suppresses postmarket fill/account findings.

- [ ] **Step 6: Verify and commit**

Run Step 2 plus `git diff --check`, then:

~~~bash
git add live_trading/modules/execution_state.py live_trading/scripts/set_execution_state.py \
  live_trading/modules/fill_importer.py live_trading/scripts/run_publish_signals.py \
  live_trading/run_publish_cron.sh live_trading/modules/pipeline_monitor.py \
  live_trading/scripts/run_monitor.py tests/live_trading/test_execution_state.py \
  tests/live_trading/test_run_publish_signals.py tests/live_trading/test_operational_wrappers.py \
  tests/live_trading/test_pipeline_monitor.py
git commit -m "feat(live): add audited paused operating state"
~~~

Expected: focused tests PASS.

---

### Task 5: Dual-profile QMT state machine and exclusive authorization

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`
- Test: `tests/live_trading/test_qmt_bridge_logic.py`

**Interfaces:**
- Produces QMT settings `EXECUTION_PROFILE`, `BRIDGE_ROOT`, `OTHER_BRIDGE_ROOT`, `ACCOUNT_ID`, `ACCOUNT_ENVIRONMENT`, `ALLOW_REAL_MONEY`.
- Produces `_profile_settings()`, `_authorization_path(trade_date)`, `_other_profile_authorized(trade_date)`, `_expected_signal_price_type()`.
- Consumes existing batch recovery, order polling, callbacks, and one-lot guard.

- [ ] **Step 1: Write failing profile/timing tests**

Assert close auction retains current times and prType 11. Under fixed price assert `15:05:00`, `15:28:00`, `15:30:00`, `15:31:00`, prType 49, `AFTER_HOURS_CLOSE`, and prefix `PR49_LIVE_OK_`. Assert either profile refuses submission when both marker paths exist.

- [ ] **Step 2: Run QMT tests and verify red state**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py -q
~~~

Expected: FAIL because the bridge is hard-coded to close auction.

- [ ] **Step 3: Add a Python-3.6-compatible profile dictionary**

Do not import dataclasses or macOS modules into QMT. Default to `EXECUTION_PROFILE="CLOSE_AUCTION"`; invalid profiles disable trading during `init` and emit a structured error.

- [ ] **Step 4: Parameterize validation, timing, API type, price, and marker**

Close auction continues to pass BUY/SELL daily limits. Fixed price passes price `0` with prType 49, logs the official close reference separately, and requires a finite positive post-close reference before submission. Preserve state-before-API idempotency and `MAX_ORDER_QUANTITY=100`.

- [ ] **Step 5: Block dual authorization before API attempts**

Emit `DUAL_AUTHORIZATION_BLOCKED` with both marker paths and finalize planned orders without `passorder`. Freeze the decision at first eligible execution wakeup.

- [ ] **Step 6: Verify and commit**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py -q
git diff --check -- live_trading/qmt_strategy/qmt_signal_bridge.py \
  tests/live_trading/test_qmt_bridge_logic.py
git add live_trading/qmt_strategy/qmt_signal_bridge.py \
  tests/live_trading/test_qmt_bridge_logic.py
git commit -m "feat(live): add isolated qmt execution profiles"
~~~

Expected: QMT tests PASS.

---

### Task 6: Evidence-complete prType=49 logging

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`
- Test: `tests/live_trading/test_qmt_bridge_logic.py`

**Interfaces:**
- Produces stable events: `RUNTIME_CONFIG`, `TIMER_REGISTERED`, `BATCH_VALIDATED`, `SECURITY_DETAIL`, `PREORDER_SNAPSHOT`, `PASSORDER_ATTEMPT`, `PASSORDER_RETURNED`, `SUBMITTED_UNCONFIRMED`, `ORDER_QUERY`, `ORDER_NOT_OBSERVED`, `ORDER_OBSERVED`, `ORDER_STATUS_CHANGED`, `ORDER_CALLBACK`, `DEAL_CALLBACK`, `ORDER_ERROR_CALLBACK`, `ORDER_FINALIZED`, `ACCOUNT_SNAPSHOT`, `LOG_WRITE_RECOVERED`.
- Produces `_safe_detail(obj, field_names) -> dict` and persisted per-order query counters.
- Consumes existing text/JSONL append helpers.

- [ ] **Step 1: Write failing successful and missing-order event tests**

~~~python
assert subsequence(events, [
    "PREORDER_SNAPSHOT", "PASSORDER_ATTEMPT", "PASSORDER_RETURNED",
    "SUBMITTED_UNCONFIRMED", "ORDER_QUERY", "ORDER_OBSERVED",
    "ORDER_STATUS_CHANGED", "DEAL_CALLBACK", "ORDER_FINALIZED",
])
~~~

Make `passorder` return normally while ORDER queries omit the remark; assert repeated `ORDER_NOT_OBSERVED`, terminal ERROR containing `QMT order not observed after passorder`, and no ACCEPTED.

- [ ] **Step 2: Run log-focused tests and verify red state**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_qmt_bridge_logic.py \
  -k 'log or observed or passorder or callback' -q
~~~

Expected: FAIL on missing events/fields.

- [ ] **Step 3: Persist runtime, batch, security, and pre-order evidence**

Log source version/file hash when obtainable, QMT version when exposed, masked account, profile, roots, all timing, cap, timer path, checksum, relevant raw security fields, official close source/time, cash/shares/frozen amounts, and sanitized `passorder` arguments.

- [ ] **Step 4: Persist API return and every relevant ORDER query**

Flush `PASSORDER_ATTEMPT` before the API. Capture return `repr`, type, and elapsed milliseconds. Every poll records result count, match count, query number, cumulative wait, and full business fields for exact/suspected remark matches. Never log credentials/tokens.

- [ ] **Step 5: Persist callbacks, transitions, final evidence, and log recovery**

Callbacks include IDs, quantities, prices, statuses, and errors. `ORDER_FINALIZED` summarizes API return, observation, callbacks, fill, and reason. On JSONL failure retain one bounded marker, continue FormulaOutput, and emit `LOG_WRITE_RECOVERED` after recovery.

- [ ] **Step 6: Verify and commit**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py -q
git diff --check -- live_trading/qmt_strategy/qmt_signal_bridge.py \
  tests/live_trading/test_qmt_bridge_logic.py
git add live_trading/qmt_strategy/qmt_signal_bridge.py \
  tests/live_trading/test_qmt_bridge_logic.py
git commit -m "feat(live): persist complete qmt order evidence"
~~~

Expected: complete QMT test file PASS.

---

### Task 7: prType=49 two-day lifecycle and shared-ledger import

**Files:**
- Modify: `live_trading/modules/operator_probe.py`
- Modify: `live_trading/scripts/run_operator_probe.py`
- Create: `live_trading/run_probe_import.sh`
- Modify: `live_trading/scripts/run_import_fills.py`
- Test: `tests/live_trading/test_operator_probe.py`
- Test: `tests/live_trading/test_operational_wrappers.py`
- Test: `tests/live_trading/test_fill_importer.py`

**Interfaces:**
- Produces `validate_probe_transition(request, recorder) -> None`.
- Produces lifecycle states `BUY_PLANNED`, `BUY_FILLED`, `SELL_PLANNED`, `CLOSED`, `FAILED`.
- Produces probe import wrapper reading isolated outbound and writing the shared database.
- Consumes Task 3 publisher and Task 2 probe config.

- [ ] **Step 1: Write failing transition tests**

BUY rejects a held symbol or missing `--eligibility-confirmed`. SELL rejects missing/unavailable actual probe holdings, same-day shares, another symbol, plan-only quantity, or unresolved prior probe batch.

~~~python
validate_probe_transition(sell_request, recorder)
assert lifecycle["state"] == "BUY_FILLED"
assert broker_positions[stock]["can_use_volume"] >= 100
~~~

- [ ] **Step 2: Run tests and verify red state**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_operator_probe.py tests/live_trading/test_fill_importer.py \
  tests/live_trading/test_operational_wrappers.py -q
~~~

Expected: FAIL for missing lifecycle/import wrapper.

- [ ] **Step 3: Add idempotent lifecycle persistence**

Create `operator_probe_lifecycle(strategy_id, stock_code, buy_batch_id, buy_trade_date, sell_batch_id, sell_trade_date, state, updated_at)`. Update lifecycle from imported terminal fills, never from plan quantities.

- [ ] **Step 4: Require next-day actual availability for SELL**

Use Qlib trade calendar to require `sell_trade_date > buy_trade_date`; require applied BUY quantity exactly 100 and latest broker `can_use_volume >= 100`; bind SELL to the lifecycle symbol.

- [ ] **Step 5: Add isolated import wrapper**

`run_probe_import.sh` loads probe config, imports `/Volumes/qmt_bridge/pr49_probe/outbound` into the shared DB, archives only under probe root, prints lifecycle state, and reconciles only the probe strategy.

- [ ] **Step 6: Verify and commit**

Run Step 2 plus diff check, then:

~~~bash
git add live_trading/modules/operator_probe.py live_trading/scripts/run_operator_probe.py \
  live_trading/run_probe_import.sh live_trading/scripts/run_import_fills.py \
  tests/live_trading/test_operator_probe.py tests/live_trading/test_operational_wrappers.py \
  tests/live_trading/test_fill_importer.py
git commit -m "feat(live): guard pr49 two-day probe lifecycle"
~~~

Expected: focused tests PASS.

---

### Task 8: Monitoring, Web visibility, and stop conditions

**Files:**
- Modify: `live_trading/modules/pipeline_monitor.py`
- Modify: `live_trading/scripts/run_monitor.py`
- Modify: `live_trading/web/api.py`
- Modify: `live_trading/web/static/index.html`
- Test: `tests/live_trading/test_pipeline_monitor.py`
- Test: `tests/live_trading/test_monitor_web_api.py`

**Interfaces:**
- Produces findings `DUAL_AUTHORIZATION`, `PROBE_ORDER_NOT_OBSERVED`, `PROBE_SNAPSHOT_MISSING`, `PROBE_POSITION_DRIFT`, `PROBE_LIFECYCLE_INVALID`.
- Adds Web fields `execution_state`, `execution_profile`, `probe_lifecycle`.
- Consumes strategy-scoped batches/fills and account-wide broker snapshot.

- [ ] **Step 1: Write failing finding/API tests**

Cover both markers present, `PASSORDER_ATTEMPT` without observed order/final ERROR, missing snapshot, BUY fill without broker holding, SELL fill with nonzero probe position, and PAUSED-main/RUNNING-probe display.

- [ ] **Step 2: Run tests and verify red state**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py tests/live_trading/test_monitor_web_api.py -q
~~~

Expected: FAIL for missing findings/fields.

- [ ] **Step 3: Implement fail-closed findings**

Every CRIT includes date, strategy ID, batch ID, stock, expected/observed state, and exact log path. PAUSED never suppresses account drift. Send the same evidence through ServerChan.

- [ ] **Step 4: Add read-only Web status**

Show strategy ID, execution profile, ACTIVE/PAUSED, probe lifecycle, and stock name/code. Add no control endpoint or button capable of publishing or creating markers.

- [ ] **Step 5: Verify and commit**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py tests/live_trading/test_monitor_web_api.py -q
git diff --check -- live_trading/modules/pipeline_monitor.py \
  live_trading/scripts/run_monitor.py live_trading/web/api.py \
  live_trading/web/static/index.html tests/live_trading/test_pipeline_monitor.py \
  tests/live_trading/test_monitor_web_api.py
git add live_trading/modules/pipeline_monitor.py live_trading/scripts/run_monitor.py \
  live_trading/web/api.py live_trading/web/static/index.html \
  tests/live_trading/test_pipeline_monitor.py tests/live_trading/test_monitor_web_api.py
git commit -m "feat(live): monitor isolated execution probes"
~~~

Expected: tests PASS.

---

### Task 9: Deployment and operator runbooks

**Files:**
- Modify: `live_trading/README.md`
- Modify: `live_trading/qmt_strategy/README_QMT.md`
- Create: `live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md`
- Modify: `tests/live_trading/test_repository_boundaries.py`
- Modify: `tests/live_trading/test_operational_wrappers.py`

**Interfaces:**
- Produces exact two-instance Windows settings and day-of-trade BUY/SELL gates.
- Consumes all commands/config IDs created above.

- [ ] **Step 1: Write failing repository-boundary tests**

Assert controlled files mention exact probe config/CLI/import wrapper, shared DB, isolated root, ID, prefix, prType 49, and 100-share cap. Assert Git contains no authorization markers, account secrets, fills, or account snapshots.

- [ ] **Step 2: Run tests and verify red state**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_repository_boundaries.py \
  tests/live_trading/test_operational_wrappers.py -q
~~~

Expected: FAIL for missing checklist/documented commands.

- [ ] **Step 3: Document main preview, SELL, and PAUSED workflow**

Give exact preview, reviewed operator publish, import, postmarket verification, and pause commands. State that preview is evidence only and raw JSONL editing is prohibited.

- [ ] **Step 4: Document two QMT instances and log inspection**

List every probe-local setting, compilation/start steps, expected `RUNTIME_CONFIG`/`TIMER_REGISTERED`, SMB paths, UI account check, and text/JSONL inspection commands. State that API return alone is not acceptance.

- [ ] **Step 5: Document BUY-day and SELL-day gates**

Stop before marker creation and require fresh user confirmation for stock/date. Rollback removes only an unused same-day marker, stops probe strategy, and preserves processing/outbound/log evidence.

- [ ] **Step 6: Verify and commit**

Run Step 2 plus diff check, then:

~~~bash
git add live_trading/README.md live_trading/qmt_strategy/README_QMT.md \
  live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md \
  tests/live_trading/test_repository_boundaries.py \
  tests/live_trading/test_operational_wrappers.py
git commit -m "docs(live): add sell and pr49 probe runbooks"
~~~

Expected: boundary/wrapper tests PASS.

---

### Task 9A: Audited same-day snapshot bootstrap

**Files:**
- Modify: `live_trading/modules/fill_importer.py`
- Modify: `live_trading/modules/operator_probe.py`
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`
- Create: `live_trading/scripts/request_account_snapshot.py`
- Modify: `live_trading/run_probe_import.sh`
- Modify: `live_trading/README.md`
- Modify: `live_trading/qmt_strategy/README_QMT.md`
- Modify: `live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md`
- Test: relevant `tests/live_trading/` importer, operator, QMT, wrapper, and boundary suites

**Interfaces:**
- Produces an audited snapshot-only request and response with no orders and no authorization marker.
- Uses a separate durable request record rather than fabricating a LIVE batch.
- Lets the same-day matched REAL ACCOUNT snapshot satisfy operator pre-publication evidence.

- [ ] **Step 1: Write failing request/import/QMT tests**

Cover durable request creation, exact replay, malformed/tampered requests, profile/root/account binding, QMT restart idempotency, and proof that no `passorder` path is reachable.

- [ ] **Step 2: Implement snapshot-only request lifecycle**

Use isolated request/processing/archive/response locations and an explicit schema/checksum. QMT must log request receipt, bound runtime configuration, account query, response persistence, and terminal status. It must never create or consume `LIVE_OK`/`PR49_LIVE_OK` and must never call `passorder` for this artifact type.

- [ ] **Step 3: Import trusted same-day ACCOUNT evidence**

Validate durable request ID, trade date, execution profile, bridge root, and masked/full REAL account identity. Positions-only or mismatched responses remain diagnostic and cannot authorize operator publication. Exact replay is a no-op; changed terminal evidence fails closed.

- [ ] **Step 4: Wire the operator preflight and runbooks**

Provide exact Mac request/import commands and Windows/QMT evidence checks. The snapshot request is read-only broker observation, not a trading authorization. Stop before any operator batch publication or marker creation.

- [ ] **Step 5: Verify and commit**

Run focused tests, the complete `tests/live_trading` suite, syntax/shell/diff checks, and an independent Critical/Important review. Do not create a REAL runtime request during development.

---

### Task 10: Full verification and deployment handoff

**Files:**
- Verify only; modify earlier files solely to fix failures attributable to this plan.

**Interfaces:**
- Produces a tested commit series and Windows handoff.
- Produces no LIVE batch or authorization marker.

- [ ] **Step 1: Run the complete live-trading suite**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading -q
~~~

Expected: zero failures.

- [ ] **Step 2: Run parity/config checks**

~~~bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/check_backtest_parity.py \
  --config csi1000_b6m_b2s_postclose_real
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_live_config.py tests/live_trading/test_backtest_parity.py -q
~~~

Expected: main parity passes; probe is excluded because it cannot generate model orders.

- [ ] **Step 3: Run syntax, shell, and diff checks**

~~~bash
/opt/anaconda3/envs/qlib/bin/python -m py_compile \
  live_trading/modules/execution_profile.py live_trading/modules/execution_state.py \
  live_trading/modules/fill_importer.py live_trading/modules/live_config.py \
  live_trading/modules/operator_probe.py live_trading/modules/order_planner.py \
  live_trading/modules/pipeline_monitor.py live_trading/modules/signal_publisher.py \
  live_trading/modules/signal_schema.py \
  live_trading/scripts/request_account_snapshot.py \
  live_trading/scripts/run_import_fills.py live_trading/scripts/run_monitor.py \
  live_trading/scripts/run_operator_probe.py \
  live_trading/scripts/run_publish_signals.py \
  live_trading/scripts/set_execution_state.py live_trading/web/api.py
/opt/anaconda3/envs/qlib/bin/python -c \
  "import ast,pathlib; p=pathlib.Path('live_trading/qmt_strategy/qmt_signal_bridge.py'); ast.parse(p.read_text(encoding='gbk'), filename=str(p), feature_version=(3,6))"
bash -n live_trading/run_publish_cron.sh live_trading/run_probe_import.sh
git diff --check 67373cd7..HEAD
~~~

Expected: every command exits 0, including the QMT source under Python 3.6 grammar.

- [ ] **Step 4: Audit safety invariants and working tree**

~~~bash
rg -n "MAX_ORDER_QUANTITY = 100|prType=49|PR49_LIVE_OK_|QMT order not observed after passorder|SNAPSHOT_ORDER_ADVANCE|SNAPSHOT_MAC_LIFECYCLE" \
  live_trading/qmt_strategy/qmt_signal_bridge.py \
  live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md
git ls-files | rg '(^|/)(LIVE_OK_|PR49_LIVE_OK_|signal_.*\.jsonl|fills_.*\.(jsonl|done)|account_.*\.(jsonl|done)|(?:request|response)_.*\.(json|done)|SNAPSHOT_(?:ORDER_ADVANCE|MAC_LIFECYCLE)\.lock|OPERATOR_AUTHORIZATION\.lock|.*\.db(?:-.*)?)$' || true
find . -path './.git' -prune -o -path './.superpowers' -prune -o -type f \
  \( -name 'LIVE_OK_*' -o -name 'PR49_LIVE_OK_*' -o -name 'signal_*.jsonl' \
     -o -name 'fills_*.jsonl' -o -name 'fills_*.done' \
     -o -name 'account_*.jsonl' -o -name 'account_*.done' \
     -o -name 'request_*.json' -o -name 'request_*.done' \
     -o -name 'response_*.json' -o -name 'response_*.done' \
     -o -name 'SNAPSHOT_ORDER_ADVANCE.lock' \
     -o -name 'SNAPSHOT_MAC_LIFECYCLE.lock' \
     -o -name 'OPERATOR_AUTHORIZATION.lock' -o -name '*.db' \
     -o -name '*.db-wal' -o -name '*.db-shm' \) -print
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_repository_boundaries.py -q
git status --short
~~~

Expected: invariants are present; tracked/runtime-artifact searches print nothing; the
boundary audit passes; and the isolated worktree is clean. Do not inspect or mutate the mounted
QMT share during repository verification.

- [ ] **Step 5: Review commits without runtime changes**

~~~bash
git log --oneline --reverse 67373cd7..HEAD
git diff --stat 67373cd7..HEAD
git diff --name-only 67373cd7..HEAD | rg -i 'tushare|data_collector' || true
git -C /Users/yuxianqi/Project/qlib status --short
~~~

Expected: the isolated branch contains focused live-trading commits and no Tushare/data-collector
files. The final read-only command may show the user's pre-existing Tushare and overlapping
primary-checkout edits; those files must remain unmodified, unstaged, and outside this branch.

- [ ] **Step 6: Hand off Windows deployment**

Report tests and commits. Instruct the user to complete the documented bidirectional Windows
QMT <-> macOS SMB `O_CREAT|O_EXCL` lock acceptance and PowerShell parser/runtime check first.
Then compile the updated bridge twice: keep main on close auction and configure the second
instance for fixed-price probe/root. Verify account binding, roots, timers, safety cap, startup
logs, and all snapshot-request directories. Stop before creating a REAL snapshot request,
selecting a stock/date, publishing a REAL order batch, or creating either authorization marker.
