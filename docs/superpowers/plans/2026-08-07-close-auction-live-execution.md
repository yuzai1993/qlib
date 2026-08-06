# Close Auction Live Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace unsupported QMT after-hours orders with guarded close-auction limit orders, reserve 7% cash, persist diagnostics to SMB, and only acknowledge broker-observed orders.

**Architecture:** Keep protocol planning on macOS and broker execution in the QMT Python 3.6 bridge. The protocol names the close-auction session while QMT resolves each order's explicit daily upper/lower limit immediately before submission. The bridge appends durable text/JSONL diagnostics, binds the account for callbacks, and separates API submission from broker acceptance.

**Tech Stack:** Python 3.6-compatible QMT strategy, Python 3.11 application/tests, pytest, YAML, JSON Lines, SMB bridge directory.

## Global Constraints

- QMT runtime compatibility is Python 3.6 and source encoding remains GBK-compatible ASCII.
- Live account remains `8890116049`, `ACCOUNT_ENVIRONMENT="REAL"`, and `ALLOW_REAL_MONEY=True` only in the deployed runtime copy.
- `MAX_ORDER_QUANTITY=100` remains a hard one-lot ceiling.
- Execution starts at `14:57:05`; `prType=11`; buys use `UpStopPrice`; sells use `DownStopPrice`.
- Maximum equity exposure is 93%; normal cash reserve is 7%; at most two initial buys are planned per day.
- No cancel request is issued during the 14:57-15:00 close auction.
- A `passorder` return is not acceptance; only an observed QMT order may produce `ACCEPTED`.
- Logs append to `D:\qmt_bridge\logs` and survive QMT restarts.
- Existing unrelated Tushare and operational-wrapper worktree changes must be preserved and excluded from focused commits.

---

### Task 1: Close-auction protocol and configuration

**Files:**
- Modify: `live_trading/modules/signal_schema.py`
- Modify: `live_trading/modules/order_planner.py`
- Modify: `live_trading/modules/live_config.py`
- Modify: `live_trading/configs/csi1000_b6m_b2s_postclose_real.yaml`
- Modify: `backtest/configs/csi1000_b6m_b2s_postclose_real_parity.yaml`
- Modify: `tests/live_trading/test_signal_schema.py`
- Modify: `tests/live_trading/test_order_planner.py`
- Modify: `tests/live_trading/test_live_config.py`
- Modify: `tests/live_trading/test_backtest_parity.py`
- Modify affected fixtures under `tests/live_trading/`

**Interfaces:**
- Produces: signal orders with `price_type="CLOSE_AUCTION_LIMIT"` and `limit_price=0.0` as a broker-resolved sentinel.
- Produces: validated live keys `execution_session="CLOSE_AUCTION"`, `close_auction_price_type=11`, `submit_after="14:57:05"`, `finalize_at="15:00:30"`, `snapshot_after="15:01:00"`.
- Produces: live and parity `risk_degree=0.93`.

- [ ] **Step 1: Write failing protocol and config tests**

```python
def test_close_auction_order_is_valid():
    validate_order(_order(price_type="CLOSE_AUCTION_LIMIT", limit_price=0.0))

def test_after_hours_order_is_rejected():
    with pytest.raises(SchemaError, match="CLOSE_AUCTION_LIMIT"):
        validate_order(_order(price_type="AFTER_HOURS_CLOSE"))

def test_real_config_uses_close_auction_and_cash_reserve():
    cfg = load_live_config(REAL_LIVE_PATH, REPO_ROOT)
    assert cfg["strategy"]["risk_degree"] == pytest.approx(0.93)
    assert cfg["live"]["execution_session"] == "CLOSE_AUCTION"
    assert cfg["live"]["close_auction_price_type"] == 11
    assert cfg["live"]["submit_after"] == "14:57:05"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_signal_schema.py tests/live_trading/test_order_planner.py tests/live_trading/test_live_config.py tests/live_trading/test_backtest_parity.py -q`

Expected: failures mention `AFTER_HOURS_CLOSE`, missing `close_auction_price_type`, and `risk_degree` still being 0.95.

- [ ] **Step 3: Implement the close-auction protocol and config validation**

Change schema/planner constants and validation messages to `CLOSE_AUCTION_LIMIT`; require a zero sentinel because QMT supplies the current daily limit at execution. Replace the real config's after-hours keys with the close-auction keys above. Update the designated real parity config's strategy risk degree to 0.93 without altering the frozen research baseline provenance.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit the protocol/config slice**

```bash
git add live_trading/modules/signal_schema.py live_trading/modules/order_planner.py live_trading/modules/live_config.py live_trading/configs/csi1000_b6m_b2s_postclose_real.yaml backtest/configs/csi1000_b6m_b2s_postclose_real_parity.yaml tests/live_trading/test_signal_schema.py tests/live_trading/test_order_planner.py tests/live_trading/test_live_config.py tests/live_trading/test_backtest_parity.py
git commit -m "feat(live): plan close auction orders"
```

---

### Task 2: QMT close-auction pricing and schedule

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`
- Modify: `tests/live_trading/test_qmt_bridge_logic.py`

**Interfaces:**
- Produces: `_instrument_limit_price(ContextInfo, stock_code, side) -> float` with new-API/legacy-API fallback.
- Produces: `LIMIT_PRICE_TYPE=11`, `TRADE_START="14:57:05"`, `FINALIZE_AT="15:00:30"`, `SNAPSHOT_AT="15:01:00"`.
- Consumes: `CLOSE_AUCTION_LIMIT` protocol from Task 1.

- [ ] **Step 1: Write failing price and timing tests**

```python
def test_buy_uses_explicit_upper_limit_and_sell_uses_lower_limit(monkeypatch):
    ctx = ContextWithInstrumentDetail({
        "UpStopPrice": 12.34,
        "DownStopPrice": 10.10,
    })
    assert bridge._instrument_limit_price(ctx, "600000.SH", "BUY") == 12.34
    assert bridge._instrument_limit_price(ctx, "600000.SH", "SELL") == 10.10

def test_invalid_limit_price_fails_closed():
    with pytest.raises(ValueError, match="limit price"):
        bridge._instrument_limit_price(ContextWithInstrumentDetail({"UpStopPrice": 0}), "600000.SH", "BUY")
```

Also assert the timer registers at `14:56:55`, submission is blocked before `14:57:05`, `passorder` receives `(prType, price) == (11, resolved_limit)`, and no cancel function is invoked during the close auction.

- [ ] **Step 2: Run QMT bridge tests and verify RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py -q`

Expected: missing `_instrument_limit_price`, old 15:05 constants, and `prType=49` assertions fail.

- [ ] **Step 3: Implement explicit daily-limit resolution and close-auction timing**

Use `ContextInfo.get_instrument_detail(stock_code)` when present, otherwise `ContextInfo.get_instrumentdetail(stock_code)`. Reject missing, boolean, non-finite, or non-positive prices before mutating submission state. Call `passorder` with price type 11 and the resolved float price. Remove the 15:28 cancel phase and finalize after the auction.

- [ ] **Step 4: Run QMT bridge tests and verify GREEN**

Run the command from Step 2. Expected: all QMT bridge tests pass.

- [ ] **Step 5: Commit the execution slice**

```bash
git add live_trading/qmt_strategy/qmt_signal_bridge.py tests/live_trading/test_qmt_bridge_logic.py
git commit -m "feat(live): execute in closing auction"
```

---

### Task 3: Durable logs, callbacks, and truthful order acceptance

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`
- Modify: `live_trading/modules/signal_schema.py`
- Modify: `live_trading/modules/pipeline_monitor.py`
- Modify: `tests/live_trading/test_qmt_bridge_logic.py`
- Modify: `tests/live_trading/test_signal_schema.py`
- Modify: `tests/live_trading/test_pipeline_monitor.py`

**Interfaces:**
- Produces: `_log_event(event_type, **fields)` appending daily text and JSONL files.
- Produces: `orderError_callback(ContextInfo, orderArgs, errMsg)` and account binding during `init`.
- Produces: internal `SUBMITTED_UNCONFIRMED` log event, not a fill protocol status.
- Produces: `ACCEPTED` only from QMT `ORDER` rows; missing order after submission becomes terminal `ERROR`.

- [ ] **Step 1: Write failing durable-log and acceptance tests**

```python
def test_log_event_appends_across_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "BRIDGE_ROOT", str(tmp_path))
    bridge._log_event("START", message="first")
    bridge._log_event("START", message="second")
    rows = (tmp_path / "logs" / f"qmt_events_{bridge._today()}.jsonl").read_text().splitlines()
    assert [json.loads(row)["message"] for row in rows] == ["first", "second"]

def test_passorder_return_does_not_write_accepted(monkeypatch):
    bridge._submit(ctx, batch, order, live=True, limit_price=12.34)
    assert not any(fill["status"] == "ACCEPTED" for fill in recorded_fills)

def test_order_query_is_required_for_accepted():
    summary = bridge._summarize_remark_orders([qmt_order], 100)
    assert summary["fill_status"] == "ACCEPTED"
    assert summary["qmt_order_id"]
```

Add tests that `init` calls `set_account`, binding failure disables live submission, `orderError_callback` persists the broker text and generates `REJECTED`, and finalization without any QMT order generates `ERROR` rather than `EXPIRED`.

- [ ] **Step 2: Run focused diagnostics tests and verify RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py tests/live_trading/test_signal_schema.py tests/live_trading/test_pipeline_monitor.py -q`

Expected: durable log/callback helpers are missing and the old immediate `ACCEPTED` assertion fails.

- [ ] **Step 3: Implement append-only logging and callback-safe state transitions**

Append one UTF-8 JSON object and one timestamped text line per event using open/write/flush/close. Catch filesystem errors and fall back to `print`. Bind `ACCOUNT_ID` through `ContextInfo.set_account` in `init`. Serialize only safe public `m_` order fields plus error text in `orderError_callback`; correlate by `userOrderId`/remark and persist `REJECTED`. Keep the pre-submit idempotency marker, log `SUBMITTED_UNCONFIRMED`, and wait for `ORDER` polling before writing `ACCEPTED`.

- [ ] **Step 4: Run focused diagnostics tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit the diagnostics slice**

```bash
git add live_trading/qmt_strategy/qmt_signal_bridge.py live_trading/modules/signal_schema.py live_trading/modules/pipeline_monitor.py tests/live_trading/test_qmt_bridge_logic.py tests/live_trading/test_signal_schema.py tests/live_trading/test_pipeline_monitor.py
git commit -m "fix(live): require broker order confirmation"
```

---

### Task 4: Cash-reserve and failed-sell rollout guards

**Files:**
- Modify: `live_trading/modules/order_manager.py`
- Modify: `live_trading/modules/fill_importer.py`
- Modify: `live_trading/scripts/run_publish_signals.py`
- Modify: `tests/live_trading/test_order_manager.py`
- Modify: `tests/live_trading/test_fill_importer.py`
- Modify: `tests/live_trading/test_signal_publisher.py`

**Interfaces:**
- Produces: no new BUY intents when broker positions exceed `topk`, a prior LIVE SELL has a terminal failure/non-fill state, or available cash cannot cover the configured two-slot amount.
- Consumes: imported terminal fill/account snapshots and `risk_degree=0.93`.

- [ ] **Step 1: Write failing rollout-guard tests**

```python
def test_prior_failed_sell_blocks_new_live_publish():
    recorder.record_fill(_fill(side="SELL", status="ERROR"))
    with pytest.raises(SystemExit, match="failed SELL"):
        ensure_no_failed_prior_sells(recorder, "2026-08-10")

def test_more_than_topk_positions_blocks_new_buys():
    intents = manager.generate_orders(scores, account_with_31_positions, cash, prices, nav)
    assert not [row for row in intents if row["direction"] == "BUY"]
```

Add a test that 7% cash funds two 3.1% target slots and a test that insufficient reserve permits required sells but suppresses buys with an auditable reason.

- [ ] **Step 2: Run order-generation/import tests and verify RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_order_manager.py tests/live_trading/test_fill_importer.py tests/live_trading/test_signal_publisher.py -q`

Expected: pending-sell/position-count reserve guards are absent.

- [ ] **Step 3: Implement fail-closed buy suppression**

Use existing imported account and fill state; do not create a second ledger. Add a recorder query for prior LIVE SELL orders whose latest terminal result is `REJECTED`, `ERROR`, `EXPIRED`, or zero-filled `PARTIAL`; fail closed before a new LIVE publish and include the batch/order ids in the error. In `OrderManager`, preserve SELL intents but suppress BUY intents whenever current position count exceeds `topk`. Never resubmit an existing client order id.

- [ ] **Step 4: Run order-generation/import tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit the rollout-guard slice**

```bash
git add live_trading/modules/order_manager.py live_trading/modules/fill_importer.py live_trading/scripts/run_publish_signals.py tests/live_trading/test_order_manager.py tests/live_trading/test_fill_importer.py tests/live_trading/test_signal_publisher.py
git commit -m "feat(live): reserve cash for close auction"
```

---

### Task 5: Deployment documentation and full verification

**Files:**
- Modify: `live_trading/README.md`
- Modify: `live_trading/qmt_strategy/README_QMT.md`
- Modify: `tests/live_trading/test_operational_wrappers.py` only where documentation/wrapper assertions require the new session names.
- Copy after verification: `/Volumes/qmt_bridge/strategy/qmt_signal_bridge.py`

**Interfaces:**
- Produces: operator procedure for copying/compiling, verifying account binding and persistent logs, and creating only the current day's `LIVE_OK`.

- [ ] **Step 1: Update operational tests and verify RED where applicable**

Assert docs/runtime mention `14:57:05`, `prType=11`, `CLOSE_AUCTION_LIMIT`, the 100-share ceiling, and `D:\qmt_bridge\logs`. Run `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_operational_wrappers.py -q` and confirm the old wording fails the new assertions.

- [ ] **Step 2: Update operator documentation**

Document: stop QMT strategy; copy source; compile; start without `LIVE_OK`; verify `START` and `ACCOUNT_BOUND` in both shared logs; verify account `8890116049`; create only today's `LIVE_OK`; inspect QMT UI order id and limit price after 14:57; remove the switch on any discrepancy.

- [ ] **Step 3: Run complete live-trading verification**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading -q
/opt/anaconda3/envs/qlib/bin/python -m compileall -q live_trading
git diff --check
```

Expected: zero pytest failures, compileall exit 0, and no whitespace errors.

- [ ] **Step 4: Review safety invariants and copy the verified source**

Confirm with `rg` that repository source keeps `MAX_ORDER_QUANTITY = 100`, runtime account changes are not copied back into repository defaults, no future `LIVE_OK` exists, and the only QMT price type in the active path is 11. Then copy the verified repository source to `/Volumes/qmt_bridge/strategy/qmt_signal_bridge.py` while preserving the runtime-only account/environment switches by applying the explicit four-line deployment customization after the copy.

- [ ] **Step 5: Commit and push**

Stage only close-auction implementation/docs plus any previously approved parity files; inspect `git diff --cached`; commit with `feat(live): switch to close auction execution`; push `main`, then synchronize the corresponding `exp/workspace` branch only if the repository's established sync procedure still applies. Do not include unrelated Tushare changes unless they have independently completed verification.
