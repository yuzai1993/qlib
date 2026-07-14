# Live Trading Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate cross-batch orders and accounting corruption, make QMT execution restart-safe and cash-budgeted, and account for dividends by record/pay/list dates with a tax provision before the 2026-07-15 live launch.

**Architecture:** Keep the file bridge and SQLite ledger, but strengthen identities and transactional boundaries. Add a small corporate-action subledger and explicit snapshot adjustments; keep final dividend-tax settlement manual until broker cash-flow import or FIFO lots exist.

**Tech Stack:** Python 3, SQLite, pytest, pandas/Tushare, Qlib, QMT Python strategy, Bash/crontab.

## Global Constraints

- `client_order_id` format is `YYYYMMDD + batch_seq(3) + order_seq(3) + side(1)` and stays within 24 characters.
- Live fills must match a recorded batch and order before changing cash or positions.
- QMT restart behavior favors a missed order over a duplicate order.
- Live prediction must not fall back to stale features.
- Dividend entitlement comes only from the record-date position snapshot.
- Dividend cash is spendable only on `pay_date`; bonus shares enter listed positions only on `div_listdate`.
- The configured 20% dividend tax is a provision, not an assertion that the broker already withheld tax.
- Existing SQLite state is migrated in place with a backup; the local database is not reset by the shared-directory cleanup.
- No new third-party runtime dependency is added.

---

### Task 1: Order Identity, Fill Validation, and Incremental Accounting

**Files:**
- Modify: `live_trading/modules/signal_schema.py`
- Modify: `live_trading/modules/order_planner.py`
- Modify: `live_trading/scripts/run_publish_signals.py`
- Modify: `live_trading/modules/fill_importer.py`
- Modify: `live_trading/modules/fees.py`
- Test: `tests/live_trading/test_signal_schema.py`
- Test: `tests/live_trading/test_order_planner.py`
- Test: `tests/live_trading/test_fill_importer.py`
- Test: `tests/live_trading/test_fees_and_corporate_actions.py`

**Interfaces:**
- Produces: `make_client_order_id(trade_date: str, batch_seq: int, order_seq: int, side: str) -> str`
- Produces: `OrderPlanner.plan(..., batch_seq: int = 1) -> list[SignalOrder]`
- Produces: `LiveRecorder.apply_fill(fill: FillEvent) -> None`, which raises `SchemaError` before mutation on a mismatch.
- Produces: `fills.applied_amount` and composite `(batch_id, client_order_id)` keys.

- [ ] **Step 1: Write failing order-ID and schema tests**

```python
def test_client_order_id_includes_batch_sequence():
    assert make_client_order_id("2026-07-15", 1, 3, "BUY") == "20260715001003B"
    assert make_client_order_id("2026-07-15", 2, 3, "BUY") == "20260715002003B"

def test_validate_fill_rejects_overfill():
    with pytest.raises(SchemaError, match="filled_qty"):
        validate_fill(dataclasses.replace(_fill(), requested_qty=100, filled_qty=200))
```

- [ ] **Step 2: Run the new schema tests and verify they fail**

Run: `pytest -q tests/live_trading/test_signal_schema.py tests/live_trading/test_order_planner.py`

Expected: failures from the old three-argument ID function and missing fill quantity checks.

- [ ] **Step 3: Implement the new ID and structural validation**

```python
def make_client_order_id(trade_date: str, batch_seq: int,
                         order_seq: int, side: str) -> str:
    if not 1 <= batch_seq <= 999:
        raise ValueError("batch_seq out of range [1, 999]")
    if not 1 <= order_seq <= 999:
        raise ValueError("order_seq out of range [1, 999]")
    compact = trade_date.replace("-", "")
    coid = f"{compact}{batch_seq:03d}{order_seq:03d}{side[0]}"
    return coid

def validate_fill(fill: FillEvent) -> None:
    # existing mode/status/side checks remain
    if not isinstance(fill.requested_qty, int) or fill.requested_qty < 0:
        raise SchemaError("requested_qty must be a non-negative int")
    if not isinstance(fill.filled_qty, int) or not 0 <= fill.filled_qty <= fill.requested_qty:
        raise SchemaError("filled_qty must be between 0 and requested_qty")
    if fill.avg_price < 0 or (fill.filled_qty > 0 and fill.avg_price <= 0):
        raise SchemaError("avg_price must be positive when filled_qty > 0")
```

Pass `args.seq` through `OrderPlanner.plan(..., batch_seq=args.seq)`.

- [ ] **Step 4: Run schema and planner tests**

Run: `pytest -q tests/live_trading/test_signal_schema.py tests/live_trading/test_order_planner.py`

Expected: PASS.

- [ ] **Step 5: Write failing recorder tests for isolation and accounting**

```python
def test_same_day_batches_keep_distinct_orders(recorder):
    recorder.record_batch("b1", "2026-07-15", "LIVE", 1)
    recorder.record_batch("b2", "2026-07-15", "LIVE", 1)
    recorder.record_orders("b1", [_order(batch_id="b1", client_order_id="same")])
    recorder.record_orders("b2", [_order(batch_id="b2", client_order_id="same")])
    assert len(recorder.get_orders("b1")) == len(recorder.get_orders("b2")) == 1

def test_fill_must_match_recorded_order(recorder):
    _record_live_order(recorder, batch_id="b1", code="600000.SH", qty=200)
    with pytest.raises(SchemaError, match="stock_code"):
        recorder.apply_fill(_fill(batch_id="b1", stock_code="000001.SZ"))
    assert recorder.get_cash() == 100000.0
    assert recorder.get_positions() == {}

def test_partial_fill_average_change_uses_amount_delta(recorder):
    _record_live_order(recorder, qty=200)
    recorder.apply_fill(_fill(requested_qty=200, filled_qty=100, avg_price=10.0,
                              status="ACCEPTED"))
    recorder.apply_fill(_fill(requested_qty=200, filled_qty=200, avg_price=11.0,
                              status="FILLED"))
    assert recorder.get_positions()["600000.SH"]["avg_cost"] == pytest.approx(11.0)
    assert recorder.get_cash() == pytest.approx(100000 - 2200 - order_total_fee("BUY", 2200, DEFAULT_FEES))
```

Also cover mode mismatch, side mismatch, requested quantity above plan, decreasing cumulative quantity/amount, and idempotent duplicate fills.

- [ ] **Step 6: Run recorder tests and verify the new cases fail**

Run: `pytest -q tests/live_trading/test_fill_importer.py`

Expected: failures showing overwritten orders, accepted mismatches, and a 100-yuan partial-fill accounting error.

- [ ] **Step 7: Implement schema migration and semantic validation**

Create `fills_new` and `signal_orders_new` with:

```sql
PRIMARY KEY (batch_id, client_order_id)
```

Copy old rows, compute `applied_amount = applied_qty * COALESCE(avg_price, 0)`, replace tables, and recreate indexes in one migration transaction. Before the migration, copy the database to `<db>.pre_hardening_<timestamp>.bak` only when the legacy primary key is detected.

In `apply_fill`, query both batch and order by composite key, run all consistency checks, then compute:

```python
cumulative_amount = float(fill.filled_qty) * float(fill.avg_price)
delta_qty = fill.filled_qty - applied_qty
delta_amount = cumulative_amount - applied_amount
```

Use `delta_amount` for BUY cost basis and cash. Upsert only mutable execution fields; never overwrite batch, mode, stock, side, or requested quantity after the first accepted row.

- [ ] **Step 8: Validate fee input and run Task 1 tests**

`order_total_fee` must reject an unknown side and non-finite/negative rates or amounts. Run:

`pytest -q tests/live_trading/test_signal_schema.py tests/live_trading/test_order_planner.py tests/live_trading/test_fill_importer.py tests/live_trading/test_fees_and_corporate_actions.py`

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

```bash
git add live_trading/modules/signal_schema.py live_trading/modules/order_planner.py \
  live_trading/scripts/run_publish_signals.py live_trading/modules/fill_importer.py \
  live_trading/modules/fees.py tests/live_trading
git commit -m "fix(live_trading): isolate batches and validate fills"
```

### Task 2: Durable QMT Batch State and Buy-Side Reservation

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`
- Modify: `live_trading/qmt_strategy/README_QMT.md`
- Test: `tests/live_trading/test_qmt_bridge_logic.py`

**Interfaces:**
- Consumes: globally unique `client_order_id` from Task 1.
- Produces: `state/active_<batch_id>.json` and restart recovery from `processing/`.
- Produces: `_max_affordable_quantity(cash, price, requested_qty) -> int`.

- [ ] **Step 1: Write failing restart and reservation tests**

```python
def test_claim_keeps_files_in_processing_until_finalize(bridge, batch_files):
    bridge._claim_new_batch()
    assert list((Path(bridge.BRIDGE_ROOT) / "processing").glob("signal_*.jsonl"))
    assert not list((Path(bridge.BRIDGE_ROOT) / "archive").glob("signal_*.jsonl"))

def test_restart_restores_submitted_without_resubmit(bridge, live_batch):
    bridge._save_active_state(live_batch)
    bridge.g.batch = None
    bridge._recover_processing_batch()
    assert live_batch.orders[0]["client_order_id"] in bridge.g.batch.submitted

def test_buy_budget_is_reserved_across_orders(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account: 10000.0)
    # two 8,000-yuan orders: first submits, second shrinks or skips
    bridge._process_batch(context, batch)
    assert sum(call.notional for call in submitted_calls) <= 10000.0
```

- [ ] **Step 2: Run the QMT tests and verify failure**

Run: `pytest -q tests/live_trading/test_qmt_bridge_logic.py`

Expected: processing is archived immediately, no active-state API exists, and both buy orders consume the same reported cash.

- [ ] **Step 3: Implement atomic active state**

Extend `Batch` with `remaining_cash = None` and source processing paths. Serialize only JSON-safe state:

```python
payload = {
    "batch_id": batch.batch_id(),
    "phase": batch.phase,
    "submitted": sorted(batch.submitted),
    "fills": batch.fills,
    "remaining_cash": batch.remaining_cash,
}
```

Write `<path>.tmp`, flush/close, then `os.replace`. Save before `passorder`, after each fill update, on phase changes, and after budget changes. Remove active state only after finalized files are complete and the batch is marked processed.

- [ ] **Step 4: Keep processing files and recover on init**

Remove the claim-time archive call. `init` scans `processing/signal_*.done`, loads at most one active batch, applies saved state, and archives already-processed leftovers without executing them. `_finalize_batch` archives signal files after writing outbound `.done` and marking the batch processed.

- [ ] **Step 5: Implement one-budget buy reservation**

Use constants matching the configured default BUY fees:

```python
COMMISSION_RATE = 0.00025
MIN_COMMISSION = 5.0
TRANSFER_FEE_RATE = 0.00001
```

At BUY entry, initialize `remaining_cash` once. Calculate the actual submission limit once, find the largest whole-lot quantity whose notional plus estimated fee fits, submit that exact price/quantity, subtract the reserved amount, and persist.

- [ ] **Step 6: Run and commit Task 2**

Run: `pytest -q tests/live_trading/test_qmt_bridge_logic.py`

Expected: PASS.

```bash
git add live_trading/qmt_strategy/qmt_signal_bridge.py \
  live_trading/qmt_strategy/README_QMT.md tests/live_trading/test_qmt_bridge_logic.py
git commit -m "fix(live_trading): recover QMT batches and reserve cash"
```

### Task 3: Strict Live Features and Real Trading-Day Publication

**Files:**
- Modify: `paper_trading/modules/signal_generator.py`
- Modify: `live_trading/scripts/run_publish_signals.py`
- Create: `live_trading/scripts/next_trade_date.py`
- Modify: `live_trading/run_publish_cron.sh`
- Test: `tests/paper_trading/test_signal_generator.py`
- Create: `tests/live_trading/test_next_trade_date.py`

**Interfaces:**
- Produces: `SignalGenerator.predict(target_date: str, allow_stale: bool = True) -> pd.Series`.
- Produces: `next_open_date(after_date: str, pro=None) -> str`.

- [ ] **Step 1: Write failing strict-date and calendar tests**

```python
def test_predict_strict_rejects_missing_date(generator):
    with pytest.raises(ValueError, match="not in features"):
        generator.predict("2026-07-15", allow_stale=False)

def test_next_open_date_skips_weekend(fake_pro):
    fake_pro.trade_cal.return_value = pd.DataFrame([
        {"cal_date": "20260718", "is_open": 0},
        {"cal_date": "20260720", "is_open": 1},
    ])
    assert next_open_date("2026-07-17", fake_pro) == "2026-07-20"
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `pytest -q tests/live_trading/test_next_trade_date.py tests/paper_trading -k signal_generator`

Expected: missing module/signature failures.

- [ ] **Step 3: Implement strict prediction and Tushare calendar lookup**

Keep stale fallback as the default for paper compatibility. In live publishing call:

```python
scores = gen.predict(signal_date, allow_stale=False)
```

`next_open_date` requests `trade_cal(start_date=after+1, end_date=after+14)`, filters `is_open == 1`, sorts by `cal_date`, and raises a descriptive error for missing token, API failure, or empty results.

- [ ] **Step 4: Update cron and run Task 3 tests**

When no positional date is supplied, `run_publish_cron.sh` calls the helper instead of `date -v+1d`. Keep `--mode LIVE` and update the stale SIMULATE comments.

Run: `pytest -q tests/live_trading/test_next_trade_date.py tests/paper_trading -k signal_generator`

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add paper_trading/modules/signal_generator.py live_trading/scripts \
  live_trading/run_publish_cron.sh tests/live_trading/test_next_trade_date.py tests/paper_trading
git commit -m "fix(live_trading): require fresh signals for open dates"
```

### Task 4: Record-Date Corporate Actions and Tax Provision

**Files:**
- Modify: `live_trading/modules/corporate_actions.py`
- Modify: `live_trading/modules/fill_importer.py`
- Modify: `live_trading/modules/monitor_store.py`
- Modify: `live_trading/modules/snapshot.py`
- Modify: `live_trading/scripts/run_monitor.py`
- Modify: `live_trading/scripts/record_cash_flow.py`
- Modify: `live_trading/web/api.py`
- Modify: `live_trading/web/static/js/app.js`
- Modify: `live_trading/README.md`
- Modify: `docs/superpowers/specs/2026-07-13-live-monitor-platform-design.md`
- Test: `tests/live_trading/test_fees_and_corporate_actions.py`
- Test: `tests/live_trading/test_snapshot.py`
- Test: `tests/live_trading/test_monitor_web_api.py`

**Interfaces:**
- Produces: `fetch_dividend_events(ex_date: str) -> list[dict]` including record/pay/list dates.
- Produces: `LiveRecorder.accrue_corporate_action(event: dict, entitled_shares: int, tax_rate: float) -> bool`.
- Produces: `LiveRecorder.settle_due_corporate_actions(date: str) -> list[str]`.
- Produces: `LiveRecorder.get_corporate_balances() -> dict` with `receivables`, `tax_provision`, and `pending_shares`.

- [ ] **Step 1: Replace current company-action tests with date-correct failing tests**

```python
def test_ex_date_uses_record_date_snapshot(recorder, store):
    store.upsert_position_snapshots("2026-07-14", [_position("600000.SH", 1000)])
    # recorder intentionally has no current position: the shares were sold on ex-date
    applied, findings = run_corporate_actions("2026-07-15", recorder, store, config)
    assert recorder.get_corporate_balances()["receivables"] == 500.0
    assert recorder.get_cash() == 10000.0

def test_pay_and_list_dates_settle_separately(recorder):
    recorder.accrue_corporate_action(_event(pay="2026-07-16", list_date="2026-07-17"), 1000, .20)
    recorder.settle_due_corporate_actions("2026-07-16")
    assert recorder.get_cash() == 10500.0
    assert recorder.get_positions()["600000.SH"]["shares"] == 1000
    recorder.settle_due_corporate_actions("2026-07-17")
    assert recorder.get_positions()["600000.SH"]["shares"] == 1100
```

Also assert missing record-date snapshots produce a finding and no guessed entitlement, repeated accrual/settlement is idempotent, and a forced exception rolls back the full event transaction.

- [ ] **Step 2: Run company-action tests and verify failure**

Run: `pytest -q tests/live_trading/test_fees_and_corporate_actions.py`

Expected: current ex-date position and immediate cash/tax/bonus behavior fail the new assertions.

- [ ] **Step 3: Add the corporate-action table and transactional APIs**

Create a table containing event identity, all dates, entitled shares, gross cash, provision, bonus shares, and three settlement flags. Accrual inserts once and does not touch cash or listed positions. Settlement updates cash and/or positions in the same connection and marks the corresponding flag.

`get_corporate_balances` returns:

```python
{
    "receivables": sum(gross_cash where cash_settled = 0),
    "tax_provision": sum(tax_provision where tax_settled = 0),
    "pending_shares": {stock_code: sum(bonus_shares where bonus_settled = 0)},
}
```

- [ ] **Step 4: Fetch full event dates and use record-date snapshots**

Request Tushare fields:

```text
ts_code,div_proc,stk_div,cash_div_tax,record_date,ex_date,pay_date,div_listdate,end_date
```

Normalize `YYYYMMDD` to `YYYY-MM-DD`. `run_corporate_actions` first settles stored events due today, then accrues ex-date events using `MonitorStore.get_position_snapshots(record_date)`. Missing dates use the conservative fallback `pay_date = ex_date` and `div_listdate = ex_date`, with a warning in the event note.

- [ ] **Step 5: Extend snapshots and tests**

Add `receivables`, `pending_market_value`, and `tax_provision` to `daily_snapshot`. Extend `build_snapshot` inputs and total-value calculation:

```python
total_value = cash + market_value + receivables + pending_market_value - tax_provision
```

Fetch prices for listed and pending stock codes. Add the new fields to API output and overview labels.

Run: `pytest -q tests/live_trading/test_snapshot.py tests/live_trading/test_monitor_web_api.py tests/live_trading/test_fees_and_corporate_actions.py`

Expected: PASS.

- [ ] **Step 6: Tighten manual cash-flow semantics**

Validate DEPOSIT positive, WITHDRAW negative, require a note for CORRECTION, remove CORRECTION from `EXTERNAL_FLOW_TYPES`, and prevent the CLI from directly creating internal `BONUS_SHARES` events.

- [ ] **Step 7: Update documentation and commit Task 4**

Document record/pay/list dates, tax provision, missing-snapshot alerts, and manual final-tax reconciliation.

```bash
git add live_trading docs/superpowers/specs/2026-07-13-live-monitor-platform-design.md tests/live_trading
git commit -m "fix(live_trading): account dividends by entitlement dates"
```

### Task 5: Full Verification, Shared-Directory Reset, and Live-Only Cron

**Files:**
- Modify: user crontab (external operational state)
- Replace: `/Volumes/qmt_bridge/strategy/qmt_signal_bridge.py`
- Delete contents only: `/Volumes/qmt_bridge/{archive,inbox,outbound,processing,logs,state}`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: clean bridge runtime for a newly generated 2026-07-15+ protocol batch.

- [ ] **Step 1: Run formatting/static sanity and all live tests**

Run:

```bash
git diff --check
python -m compileall -q live_trading paper_trading/modules/signal_generator.py
pytest -q tests/live_trading
```

Expected: no diff/compile errors; all live tests pass.

- [ ] **Step 2: Run focused non-live regression tests**

Run: `pytest -q tests/paper_trading -k 'signal or order'`

Expected: PASS, proving the optional stale fallback remains compatible.

- [ ] **Step 3: Exercise migration on a database copy**

Copy `live_trading/data/csi300_topk10_live.db` to a temporary directory, instantiate `LiveRecorder` on the copy, and verify batch/fill/order counts, cash, and positions before and after migration are unchanged.

- [ ] **Step 4: Exercise bridge end to end in a temporary directory**

Publish two same-day batches, claim/recover one, generate simulated fills, import them, and assert both batches remain isolated. Never point this smoke test at `/Volumes/qmt_bridge`.

- [ ] **Step 5: Clean and deploy the shared bridge**

After explicit filesystem approval, delete files under runtime subdirectories while preserving the directories. Remove the obsolete 2026-07-15 signal and old LIVE_OK/processed state. Copy the verified strategy to `/Volumes/qmt_bridge/strategy/qmt_signal_bridge.py` and compare byte-for-byte.

- [ ] **Step 6: Keep only live cron and its data dependency**

Install exactly:

```cron
# live: Tushare→bin 日更（信号与收盘价依赖）
30 17 * * 1-5 /Users/yuxianqi/Project/qlib/scripts/data_collector/tushare/run_update_to_bin.sh
# live: 导入 QMT 回执 + 盘后对账监控
0 16 * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/run_import_cron.sh && /Users/yuxianqi/Project/qlib/live_trading/run_monitor_cron.sh postmarket
# live: 快照 + 微信日报
30 20 * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/run_monitor_cron.sh report
# live: 发布下一交易日 LIVE 信号
30 21 * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/run_publish_cron.sh
# live: 次日信号发布检查
0 22 * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/run_monitor_cron.sh evening
```

Read it back with `crontab -l` and compare exactly.

- [ ] **Step 7: Generate the next live signal and final checks**

Run the publish wrapper only after confirming local cash/positions, `LIVE_TRADING_CONFIRM=YES`, next open date, and the updated QMT strategy. Confirm exactly one signal JSONL and `.done` pair exists in inbox and its order IDs use the new format. Create the QMT `LIVE_OK_<trade_date>` switch only for the intended live date.

- [ ] **Step 8: Final commit**

```bash
git add -A
git commit -m "docs(live_trading): finalize hardened live rollout"
```

Run `git status --short` and require a clean tree.
