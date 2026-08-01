# CSI1000 B6-M Post-Close Live System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated CSI1000 paper-trading system using the frozen B6-M model, staged Top2 initialization, Top30/Drop2/Hold20 steady state, and QMT after-hours fixed-price orders.

**Architecture:** Ranking and staged initialization live in the shared Qlib TopkDropout selector; the live ledger supplies actual positions and trading-day ages. Protocol-v2 publishes target-value BUY intents on T-1 evening, and an idempotent QMT timer converts them to board-lot quantities using the official T close before submitting `prType=49` orders. A standalone config, copied model artifact, parity gate, independent SQLite ledger, generic wrappers, and fail-closed monitoring isolate the new paper account from the inactive CSI300 system.

**Tech Stack:** Python 3.12, Qlib, pandas, SQLite, pytest, YAML/JSONL, Bash, QMT built-in Python 3.6.

## Global Constraints

- Pool CSI1000; benchmark `SH000852`; opening capital `500000.0`.
- Frozen B6-M seed 4000 SHA-256: `368a503c7df8233022e6d7f9f1398711c64c819d1d55c487ef974a3c060e6325`.
- Steady state: Top30 / Drop2 / Hold20 / risk degree 0.95.
- Initialization: explicit `initial_buy_count=2`; no sells before actual holdings reach 30.
- Each BUY target is `decision_total_value * 0.95 / 30`, never remaining cash divided by two candidates.
- T-1 prediction drives T execution; QMT uses `prType=49` in the 15:05-15:30 window.
- Account environment is SIMULATION; no automatic real-money activation path.
- `/Users/yuxianqi/Project/qlib_exp` is read-only; runtime artifacts are copied and verified in this repository.
- No training, sweep, registry mutation, baseline promotion, or test-set tuning.
- QMT bridge remains Python-3.6-compatible ASCII.

---

### Task 1: Shared staged TopkDropout selection and sizing

**Files:**
- Modify: `tests/backtest/test_topk_dropout_selection.py`
- Modify: `qlib/contrib/strategy/topk_dropout.py`
- Modify: `qlib/contrib/strategy/signal_strategy.py`

**Interfaces:**
- Consumes: `select_topk_dropout(..., initial_buy_count=None)`.
- Produces: deterministic staged selection and `TopkDropoutStrategy(initial_buy_count=None)`.

- [ ] **Step 1: Write failing selector tests**

```python
def test_staged_initialization_buys_two_unheld_and_never_sells():
    scores = pd.Series(range(40, 0, -1), index=[f"S{i:02d}" for i in range(40)])
    result = select_topk_dropout(
        scores, ["S00", "S05"], topk=30, n_drop=2, initial_buy_count=2,
    )
    assert result.sell == ()
    assert result.buy == ("S01", "S02")

def test_default_initialization_keeps_legacy_full_fill():
    result = select_topk_dropout(scores, [], topk=30, n_drop=2)
    assert result.buy == tuple(scores.index[:30])
```

Also assert 29 holdings buy only one, 30 holdings use Drop2, zero/negative/bool initialization values fail, and input ordering cannot change ties.

- [ ] **Step 2: Run and verify RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_topk_dropout_selection.py -q
```

Expected: new calls fail because `initial_buy_count` is not accepted.

- [ ] **Step 3: Implement the minimal shared branch**

Validate `initial_buy_count` as `None` or a positive integer. Before the existing dropout branch, return no sells and the first `min(initial_buy_count, topk-len(current))` unheld ranked names while underfilled. Pass the parameter from `TopkDropoutStrategy`; in the staged branch size each backtest buy with `current_temp.calculate_value() * risk_degree / topk`. Leave the `None` path byte-for-byte compatible in behavior.

- [ ] **Step 4: Run and verify GREEN**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_topk_dropout_selection.py tests/backtest/test_file_strategy.py -q
```

- [ ] **Step 5: Commit**

```bash
git add qlib/contrib/strategy/topk_dropout.py qlib/contrib/strategy/signal_strategy.py tests/backtest/test_topk_dropout_selection.py
git commit -m "feat(strategy): support staged Topk initialization"
```

---

### Task 2: Live slot budgets and Hold20 from actual fills

**Files:**
- Modify: `tests/live_trading/test_order_manager.py`
- Modify: `tests/live_trading/test_fill_importer.py`
- Modify: `live_trading/modules/order_manager.py`
- Modify: `live_trading/modules/fill_importer.py`

**Interfaces:**
- Consumes: positions with `opened_trade_date`, signal date, and Qlib trading calendar.
- Produces: Hold20-filtered SELL intents and BUY intents with `target_value`.

- [ ] **Step 1: Write failing live strategy and ledger tests**

```python
def test_staged_buy_uses_one_slot_target_value():
    manager = _manager(topk=30, initial_buy_count=2, risk_degree=0.95)
    orders = manager.generate_orders(scores, {}, 500_000.0, {}, 500_000.0)
    assert [o["instrument"] for o in orders] == list(scores.index[:2])
    assert [o["target_value"] for o in orders] == [pytest.approx(15833.333333333334)] * 2

def test_underfilled_portfolio_never_sells_low_ranked_holding():
    assert not sell_orders(manager.generate_orders(scores, underfilled, 400_000, {}, 500_000))
```

Add literal trading-calendar tests for day 19/day 20 eligibility and importer tests proving first BUY sets the batch trade date, add-on BUY preserves it, full SELL removes it, and re-entry resets it.

- [ ] **Step 2: Run and verify RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_order_manager.py tests/live_trading/test_fill_importer.py -q
```

- [ ] **Step 3: Implement target values, age filtering, and migration**

`OrderManager` passes `initial_buy_count` to the shared selector, filters only steady-state sells by inclusive Qlib trading-day age, and emits each BUY as:

```python
{"instrument": inst, "direction": "BUY", "target_value": total_value * risk_degree / topk}
```

Add nullable `opened_trade_date TEXT` to `positions`. Pass the batch trade date into the fill position update; write it only when old shares are zero, preserve it on add-ons/bonus shares, and remove it on full exit. Return it from `get_positions()`.

Add `account.opening_cash` support to `LiveRecorder`: `INSERT OR IGNORE` seeds it only when the database has no batches, fills, positions, or existing cash. A nonempty ledger is never reset from configuration. Cover fresh seed, repeat construction, and refusal to seed an already-used ledger.

- [ ] **Step 4: Run and verify GREEN, then commit**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_order_manager.py tests/live_trading/test_fill_importer.py -q
git add live_trading/modules/order_manager.py live_trading/modules/fill_importer.py tests/live_trading/test_order_manager.py tests/live_trading/test_fill_importer.py
git commit -m "feat(live): enforce staged sizing and holding age"
```

---

### Task 3: B6-M artifact, generic inference, standalone config, and parity

**Files:**
- Create: `live_trading/models/b6_m/seed4000/trained_model`
- Create: `live_trading/models/b6_m/seed4000/manifest.json`
- Create: `live_trading/configs/csi1000_b6m_b2s_postclose.yaml`
- Create: `backtest/configs/csi1000_b6m_b2s_postclose_parity.yaml`
- Modify: `live_trading/modules/signal_generator.py`
- Modify: `live_trading/modules/live_config.py`
- Modify: `live_trading/modules/backtest_parity.py`
- Modify: `tests/live_trading/test_signal_generator.py`
- Modify: `tests/live_trading/test_live_config.py`
- Modify: `tests/live_trading/test_backtest_parity.py`

**Interfaces:**
- Consumes: a SHA-verified Qlib model and one-day feature frame.
- Produces: inference via `model.predict(dataset)` and exact live/parity config gates.

- [ ] **Step 1: Write failing model/config/parity tests**

Use a fake model with only `predict(dataset, segment="test")`; assert the adapter returns the exact frame with NaNs. Assert the real config has CSI1000, SH000852, 500000, Top30/Drop2/initial2/Hold20, independent DB, `broker_environment: SIMULATION`, and `allow_real_money: false`. Add parity drift cases for initialization and handler feature groups.

- [ ] **Step 2: Run and verify RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_signal_generator.py tests/live_trading/test_live_config.py tests/live_trading/test_backtest_parity.py -q
```

- [ ] **Step 3: Implement the generic dataset adapter and config guards**

```python
class _InferenceDataset:
    def __init__(self, features):
        self.features = features
    def prepare(self, segment, *, col_set, data_key):
        if col_set != "feature" or data_key != DataHandlerLP.DK_I:
            raise ValueError("inference dataset only provides infer features")
        return self.features
```

Normalize model output to the input MultiIndex and reject duplicate/missing scores. Make `allow_stale=False` the inference default and require explicit `True` only for diagnostics. New configs require simulation environment, false real-money flag, positive `account.opening_cash`, and valid initialization count. Extend parity with initialization, handler feature groups, account value, fees, and close-price execution.

- [ ] **Step 4: Copy and verify the model**

Copy the external seed4000 artifact into `live_trading/models/b6_m/seed4000/`, create a production manifest, then run:

```bash
openssl dgst -sha256 live_trading/models/b6_m/seed4000/trained_model
```

Expected digest is the Global Constraints digest and size is 3391157 bytes.

- [ ] **Step 5: Create YAMLs and run a real smoke prediction**

Use `Alpha158Technical`, `feature_groups: [range]`, the new strategy ID/DB, `default_mode: SIMULATE`, no slippage, max 40 orders, and the copied artifact. Run the real model from a normal script file (never stdin/heredoc) against one available CSI1000 day; require finite, unique scores.

- [ ] **Step 6: Run focused tests and commit**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_signal_generator.py tests/live_trading/test_live_config.py tests/live_trading/test_backtest_parity.py -q
git add live_trading/models/b6_m live_trading/configs/csi1000_b6m_b2s_postclose.yaml backtest/configs/csi1000_b6m_b2s_postclose_parity.yaml live_trading/modules/signal_generator.py live_trading/modules/live_config.py live_trading/modules/backtest_parity.py tests/live_trading/test_signal_generator.py tests/live_trading/test_live_config.py tests/live_trading/test_backtest_parity.py
git commit -m "feat(live): add frozen B6 CSI1000 configuration"
```

---

### Task 4: Protocol-v2 target-value orders and durable empty batches

**Files:**
- Modify: `live_trading/modules/signal_schema.py`
- Modify: `live_trading/modules/order_planner.py`
- Modify: `live_trading/modules/signal_publisher.py`
- Modify: `live_trading/modules/fill_importer.py`
- Modify: `live_trading/scripts/run_publish_signals.py`
- Modify: `tests/live_trading/test_signal_schema.py`
- Modify: `tests/live_trading/test_order_planner.py`
- Modify: `tests/live_trading/test_signal_publisher.py`
- Modify: `tests/live_trading/test_fill_importer.py`

**Interfaces:**
- Produces protocol-v2 orders: SELL has positive quantity; BUY has positive `target_value` and QMT-resolved quantity. Zero-order batches remain durable and publishable.

- [ ] **Step 1: Write failing protocol tests**

Test BUY target-value validation, SELL quantity validation, schema version 2, simulation account scope, empty batch round-trip, exact retry success, and conflicting retry failure. Assert a zero-order plan is committed before `.jsonl/.done` becomes visible.

```python
def test_after_hours_buy_carries_target_value_not_stale_quantity():
    order = _order(side="BUY", quantity=0, target_value=15833.33,
                   price_type="AFTER_HOURS_CLOSE", limit_price=0.0)
    validate_order(order)

def test_empty_batch_is_valid_and_publishable(tmp_path):
    path = SignalPublisher(tmp_path).publish(_header(order_count=0), [])
    assert path.exists()
    assert len(path.read_text().splitlines()) == 1
```

- [ ] **Step 2: Run and verify RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_signal_schema.py tests/live_trading/test_order_planner.py tests/live_trading/test_signal_publisher.py tests/live_trading/test_fill_importer.py -q
```

- [ ] **Step 3: Implement protocol-v2 and database migration**

Set schema version `2.0`; add `account_environment` to `BatchHeader` and `target_value` to `SignalOrder`/SQLite. BUY requires `AFTER_HOURS_CLOSE`, target value greater than zero, and planned quantity zero. SELL requires a positive legal quantity and target value zero. For BUY fill events, accept the positive board-lot `requested_qty` resolved by QMT even though planned quantity is zero; keep the planned `target_value` immutable and reject a positive filled amount above that value. SELL fills remain bounded by planned quantity. Allow an empty checksum and an atomic header-only publication. Existing visible files become idempotent success only when their bytes/checksum exactly match; otherwise fail without overwrite.

- [ ] **Step 4: Update planning and publication flow**

Remove previous-close/slippage sizing for BUY. Planner maps BUY target values and SELL quantities to protocol-v2. Publisher always records and emits a batch; no intents create `NO_ORDERS`. The new config resolves `QMT_SIM_ACCOUNT_ID`, records `account_environment=SIMULATION`, and refuses real-money configuration.

- [ ] **Step 5: Run focused tests and commit**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_signal_schema.py tests/live_trading/test_order_planner.py tests/live_trading/test_signal_publisher.py tests/live_trading/test_fill_importer.py -q
git add live_trading/modules/signal_schema.py live_trading/modules/order_planner.py live_trading/modules/signal_publisher.py live_trading/modules/fill_importer.py live_trading/scripts/run_publish_signals.py tests/live_trading/test_signal_schema.py tests/live_trading/test_order_planner.py tests/live_trading/test_signal_publisher.py tests/live_trading/test_fill_importer.py
git commit -m "feat(live): publish target-value after-hours batches"
```

---

### Task 5: QMT fixed-price after-hours timer state machine

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`
- Modify: `live_trading/qmt_strategy/README_QMT.md`
- Modify: `tests/live_trading/test_qmt_bridge_logic.py`

**Interfaces:**
- Consumes: protocol-v2 batches and QMT account/tick/order APIs.
- Produces: timer/handlebar-shared `advance(ContextInfo)` with durable sell/wait/buy/cancel/finalize phases.

- [ ] **Step 1: Replace intraday tests with failing after-hours tests**

Assert the 15:05/15:28/15:30 constants, positive post-close `lastPrice` requirement, target-value board-lot calculation, cash/fee trimming, `prType=49`, four-minute sell gate, restart recovery, duplicate-wakeup idempotency, and empty-batch finalization.

```python
def test_submit_uses_after_hours_price_type(bridge, monkeypatch):
    submitted = []
    monkeypatch.setattr(bridge, "passorder", lambda *args: submitted.append(args), raising=False)
    bridge._submit(ctx, batch, resolved_order, True, official_close=10.25)
    assert submitted[0][4] == 49
    assert submitted[0][5] == 0
```

- [ ] **Step 2: Run and verify RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py -q
```

- [ ] **Step 3: Implement official-close execution**

Delete ask/bid/slippage/fallback pricing. Require protocol-v2 simulation account scope. Resolve each BUY quantity once from target value, official close, fees, and actual cash; persist before submission. Submit all executable orders using:

```python
passorder(op_type, 1101, account_id, stock_code, 49, 0,
          quantity, STRATEGY_NAME, 2, client_order_id, ContextInfo)
```

Retain the uncertainty marker so a crash favors a missed order over a duplicate.

- [ ] **Step 4: Add timer scheduling with a shared advance loop**

`init` calls `ContextInfo.schedule_run(timer_callback, first_time, -1, datetime.timedelta(seconds=3), name)`. `timer_callback` and `handlebar` call the same throttled `advance` path. Cancellation/finalization continue after the execute marker is removed if any order was submitted.

- [ ] **Step 5: Verify and commit**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py -q
/opt/anaconda3/envs/qlib/bin/python -m py_compile live_trading/qmt_strategy/qmt_signal_bridge.py
LC_ALL=C grep -n '[^ -~]' live_trading/qmt_strategy/qmt_signal_bridge.py
git add live_trading/qmt_strategy/qmt_signal_bridge.py live_trading/qmt_strategy/README_QMT.md tests/live_trading/test_qmt_bridge_logic.py
git commit -m "feat(qmt): execute fixed-price after-hours batches"
```

Expected grep output is empty; the source is ASCII under its GBK declaration.

---

### Task 6: Generic scheduling and no-order-aware monitoring

**Files:**
- Create: `live_trading/scripts/batch_status.py`
- Modify: `live_trading/modules/pipeline_monitor.py`
- Modify: `live_trading/run_publish_cron.sh`
- Modify: `live_trading/run_publish_catchup_cron.sh`
- Modify: `live_trading/run_import_cron.sh`
- Modify: `live_trading/run_monitor_cron.sh`
- Modify: `tests/live_trading/test_pipeline_monitor.py`
- Modify: `tests/live_trading/test_repository_boundaries.py`
- Modify: `live_trading/README.md`
- Modify: `docs/qmt_qlib_live_guide.md`

**Interfaces:**
- Wrappers use positional config ID, then `QLIB_LIVE_CONFIG_ID`, then the new CSI1000 ID; none hardcode CSI300.
- `batch_status.py` reads the YAML-selected DB and distinguishes durable batch, missing batch, and database error by exit code.

- [ ] **Step 1: Write failing monitor and wrapper tests**

Add a case with `planned=0`, `terminal=0` that produces no `FILLS_MISSING`. Assert wrappers contain no `csi300_topk10_live`, no stdin Python/heredoc, and no `|| true` swallowing monitor status.

- [ ] **Step 2: Run and verify RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_pipeline_monitor.py tests/live_trading/test_repository_boundaries.py -q
```

- [ ] **Step 3: Implement generic jobs and explicit zero-order semantics**

Treat `planned_orders == 0` as terminal without fill rows. Use `batch_status.py` in catch-up instead of inline Python. Shell wrappers preserve the primary exit code and append status through an `EXIT` trap; config and account environment are explicit inputs.

- [ ] **Step 4: Document rollout and inactive external actions**

Document T-1 21:00 publish, T 15:05-15:30 QMT, 15:32 import, 15:35 report, environment variables, simulation guard, shadow test, one-lot gate, full-paper gate, rollback, and the fact that account/QMT/SMB/cron activation remains external until prerequisites exist.

- [ ] **Step 5: Verify and commit**

```bash
bash -n live_trading/run_publish_cron.sh live_trading/run_publish_catchup_cron.sh live_trading/run_import_cron.sh live_trading/run_monitor_cron.sh
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_pipeline_monitor.py tests/live_trading/test_repository_boundaries.py -q
git add live_trading/scripts/batch_status.py live_trading/modules/pipeline_monitor.py live_trading/run_publish_cron.sh live_trading/run_publish_catchup_cron.sh live_trading/run_import_cron.sh live_trading/run_monitor_cron.sh live_trading/README.md docs/qmt_qlib_live_guide.md tests/live_trading/test_pipeline_monitor.py tests/live_trading/test_repository_boundaries.py
git commit -m "feat(live): harden paper-trading orchestration"
```

---

### Task 7: Full verification and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-08-02-csi1000-postclose-live-system.md` (mark completed steps)

**Interfaces:**
- Produces fresh evidence for strategy compatibility, live integration, bridge syntax, artifact integrity, and clean diffs.

- [ ] **Step 1: Run full relevant suites**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading tests/backtest/test_topk_dropout_selection.py tests/backtest/test_file_strategy.py -q
```

- [ ] **Step 2: Run static and artifact checks**

```bash
/opt/anaconda3/envs/qlib/bin/python -m compileall -q live_trading
bash -n live_trading/run_publish_cron.sh live_trading/run_publish_catchup_cron.sh live_trading/run_import_cron.sh live_trading/run_monitor_cron.sh
openssl dgst -sha256 live_trading/models/b6_m/seed4000/trained_model
git diff --check
git status --short
```

- [ ] **Step 3: Audit design coverage and external prerequisites**

Map every design section to an implementation/test. Record as unactivated: new QMT account ID, Windows bridge installation, SMB mount, QMT timer process, cron entries, shadow observation days, one-lot promotion, and full-paper promotion.

- [ ] **Step 4: Perform inline final review**

Inspect `git diff origin/main...HEAD` for security, cash sizing, date shifts, schema migration, QMT restart behavior, account-mode confusion, and unrelated edits. Repository policy forbids unrequested review agents, so fix Critical/Important findings inline.

- [ ] **Step 5: Mark the plan complete and commit**

```bash
git add docs/superpowers/plans/2026-08-02-csi1000-postclose-live-system.md
git commit -m "docs: complete CSI1000 live implementation plan"
```

Do not merge, push, install QMT code, modify crontab, or enable a broker account automatically. Report the isolated branch/worktree, commits, verification evidence, and exact activation prerequisites.
