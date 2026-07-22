# Remove Paper Trading and Align Live/Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the obsolete Paper Trading application and make Live Trading share deterministic TopkDropout decisions and checked configuration assumptions with its designated Backtest.

**Architecture:** Move Live-owned inference and order construction into `live_trading`, extract deterministic TopkDropout selection into a Qlib strategy helper consumed by both Live and Backtest, and make the Live YAML standalone. Add a parity configuration validator that fails publishing closed when model, strategy, fee, account, or exchange assumptions drift.

**Tech Stack:** Python 3.12, pandas, PyYAML, pytest, SQLite, Qlib, QMT embedded Python 3.6.

## Global Constraints

- Preserve all existing Live SQLite data, positions, fills, batches, bridge archives, and Live logs.
- Do not modify or republish immutable signal batches.
- Do not include the user's unrelated CSI500 and SoftTopk working-tree changes.
- Keep the QMT signal protocol backward compatible; do not introduce dynamic cash-fraction sizing in this unattended change.
- Treat daily close as the explicit Backtest proxy for Live's 14:45 near-close execution, not as a claim of identical fills.
- Use test-first red/green cycles for every behavior or import-path change.

---

### Task 1: Move Live inference and order construction out of Paper Trading

**Files:**
- Create: `live_trading/modules/signal_generator.py`
- Create: `live_trading/modules/order_manager.py`
- Modify: `live_trading/scripts/run_publish_signals.py`
- Modify: `live_trading/scripts/backfill_predictions.py`
- Move tests into: `tests/live_trading/test_signal_generator.py`
- Move tests into: `tests/live_trading/test_order_manager.py`

**Interfaces:**
- Produces: `live_trading.modules.signal_generator.SignalGenerator`
- Produces: `live_trading.modules.order_manager.OrderManager.generate_orders(...)`
- Preserves: current order-intent dictionaries and strict stale-date behavior.

- [ ] **Step 1: Add failing tests for the new Live import paths**

Change imports in the two existing test files to:

```python
from live_trading.modules.signal_generator import SignalGenerator
from live_trading.modules.order_manager import OrderManager
```

- [ ] **Step 2: Run the migrated tests and verify collection fails**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_signal_generator.py \
  tests/live_trading/test_order_manager.py -q
```

Expected: import failure because the Live modules do not exist.

- [ ] **Step 3: Move the two implementations and update runtime imports**

Copy behavior without changing it yet, change logger namespaces to `live_trading.*`, and update both Live scripts to import the new paths.

- [ ] **Step 4: Run the migrated tests and Live suite**

Expected: migrated tests and all `tests/live_trading` tests pass.

- [ ] **Step 5: Commit the relocation**

```bash
git add live_trading tests/live_trading paper_trading/modules
git commit -m "refactor: move live dependencies out of paper trading"
```

### Task 2: Make the Live configuration and stock-name maintenance independent

**Files:**
- Modify: `live_trading/configs/csi300_topk10_live.yaml`
- Modify: `live_trading/modules/live_config.py`
- Modify: `live_trading/modules/signal_generator.py`
- Modify: `live_trading/modules/stock_names.py`
- Modify: `live_trading/web/api.py`
- Create: `live_trading/scripts/refresh_stock_names.py`
- Rename: `live_trading/scripts/backfill_orders_and_names.py` to `live_trading/scripts/backfill_orders.py`
- Modify: `tests/live_trading/test_live_config.py`
- Create: `tests/live_trading/test_stock_names.py`

**Interfaces:**
- `load_live_config(path, project_root=None)` loads exactly one standalone YAML.
- `fetch_stock_names(pro)` converts Tushare `ts_code/name` rows into Live/Qlib identifiers.
- `SignalGenerator` reads explicit `handler.fit_start_time` and `infer_processors`.

- [ ] **Step 1: Write failing standalone-config and handler-kwargs tests**

Assert the real Live YAML has no `base_config`, contains all model/data/handler/strategy/exchange fields, and passes `fit_start_time=2006-01-02` to handler construction.

- [ ] **Step 2: Write failing stock-name tests**

Use a fake Tushare client returning `600000.SH` and `000001.SZ`; assert conversion to Live rows and ensure Web no longer resolves a Paper database.

- [ ] **Step 3: Verify the tests fail for the old dependency paths**

Expected: base-config assertion and missing Tushare function fail.

- [ ] **Step 4: Implement standalone config, explicit handler settings, and Live-only names**

Copy current production model IDs and strategy values into the Live YAML. Remove `_deep_merge` and Paper DB fallback. Make the refresh script require `TUSHARE_TOKEN` and save directly to Live SQLite.

- [ ] **Step 5: Run config, signal, stock-name, and Web tests**

Expected: all selected tests pass.

- [ ] **Step 6: Commit the independence change**

```bash
git add live_trading tests/live_trading
git commit -m "refactor: make live trading self contained"
```

### Task 3: Share deterministic TopkDropout selection with Backtest

**Files:**
- Create: `qlib/contrib/strategy/topk_dropout.py`
- Modify: `qlib/contrib/strategy/signal_strategy.py`
- Modify: `live_trading/modules/order_manager.py`
- Create: `tests/backtest/test_topk_dropout_selection.py`
- Modify: `tests/live_trading/test_order_manager.py`

**Interfaces:**
- `stable_rank_scores(scores: pd.Series) -> pd.Series`
- `select_topk_dropout(scores, current_stock_list, topk, n_drop) -> TopkSelection`
- `TopkSelection.sell` and `.buy` are deterministic tuples of instruments.

- [ ] **Step 1: Write failing deterministic tie tests**

Construct three equal-score instruments across the top-10 boundary, permute the input Series and current-position order, and assert identical sell/buy tuples using code-ascending tie breaks.

- [ ] **Step 2: Write failing 9/10/11/12-position shared-core tests**

Assert the same convergence behavior already required by Live OrderManager.

- [ ] **Step 3: Verify failure because the helper does not exist**

Run the new Backtest test file alone and confirm import failure.

- [ ] **Step 4: Implement the pure helper and route both systems through it**

Use the helper only for Qlib's configured deterministic path (`top`/`bottom`, `only_tradable=false`). Preserve random and tradability-filtered branches.

- [ ] **Step 5: Run shared-core, Live order, and Qlib strategy tests**

Expected: all selected tests pass and no existing strategy tests regress.

- [ ] **Step 6: Commit deterministic selection**

```bash
git add qlib/contrib/strategy live_trading/modules/order_manager.py tests
git commit -m "fix: share deterministic TopkDropout selection"
```

### Task 4: Align Live sizing assumptions that do not require a protocol change

**Files:**
- Modify: `live_trading/modules/order_manager.py`
- Modify: `tests/live_trading/test_order_manager.py`

**Interfaces:**
- OrderManager reads `strategy.risk_degree` rather than hard-coding `0.95`.
- Estimated sell cash equals gross proceeds minus `order_total_fee("SELL", ...)`.

- [ ] **Step 1: Write failing risk-degree and net-proceeds tests**

Use one sell and two buys with a non-zero sell fee. Assert buy shares derive from net cash and the configured risk degree.

- [ ] **Step 2: Verify the old implementation overallocates**

Expected: target shares differ because the old implementation uses gross proceeds and hard-coded 95%.

- [ ] **Step 3: Implement the minimal sizing fix**

Reuse `live_trading.modules.fees.order_total_fee`; leave QMT's execution-time shrink safety unchanged.

- [ ] **Step 4: Run Live order, fee, planner, bridge, and importer tests**

Expected: all selected tests pass.

- [ ] **Step 5: Commit sizing alignment**

```bash
git add live_trading/modules/order_manager.py tests/live_trading
git commit -m "fix: align live order budgets with backtest"
```

### Task 5: Add a designated parity Backtest and fail-closed config gate

**Files:**
- Create: `backtest/configs/csi300_live_parity.yaml`
- Create: `live_trading/modules/backtest_parity.py`
- Create: `live_trading/scripts/check_backtest_parity.py`
- Modify: `live_trading/scripts/run_publish_signals.py`
- Modify: `live_trading/configs/csi300_topk10_live.yaml`
- Create: `tests/live_trading/test_backtest_parity.py`

**Interfaces:**
- `validate_backtest_parity(live_config, backtest_config) -> None`
- Raises `ParityError` listing exact mismatched field paths.

- [ ] **Step 1: Write failing parity success and drift tests**

Load both real YAML files and assert success. Parameterize mutations of model source, universe, handler, topk/n_drop, risk degree, hold threshold, tradability flags, account, trade unit, fees, limit threshold, and deal price; each must raise with its field path.

- [ ] **Step 2: Verify failure because config/helper are absent**

- [ ] **Step 3: Add the parity Backtest config and validator**

Use account `10000000`, open cost `0.00021`, close cost `0.00071`, min cost `5`, `deal_price=close`, `trade_unit=100`, and explicit strategy kwargs.

- [ ] **Step 4: Call the gate before Live publishing performs durable writes**

Resolve `parity.backtest_config` relative to the repository root. On mismatch, exit without recording or publishing a batch.

- [ ] **Step 5: Run validator tests and a dry-run publish-path unit test**

Expected: matching real configs pass; every mutation fails closed.

- [ ] **Step 6: Commit the parity gate**

```bash
git add backtest/configs live_trading tests/live_trading
git commit -m "feat: gate live publishing on backtest parity"
```

### Task 6: Delete Paper Trading code and active documentation references

**Files:**
- Delete: `paper_trading/`
- Delete: `tests/paper_trading/`
- Delete: `docs/vibe_coding/paper_trading_plan.md`
- Modify: `.gitignore`
- Modify: `live_trading/README.md`
- Modify: `docs/qmt_qlib_live_guide.md`
- Modify: current parity design/plan references where paths moved.

**Interfaces:**
- No executable Live source imports or opens any `paper_trading` path.

- [ ] **Step 1: Add a failing repository-boundary test**

Assert `paper_trading/` and `tests/paper_trading/` do not exist and scan executable `live_trading/*.py` files for `paper_trading` references.

- [ ] **Step 2: Verify the boundary test fails before deletion**

- [ ] **Step 3: Delete tracked Paper files and update active docs**

Archived dated specs/plans remain historical records. Remove active instructions that tell operators to use Paper components.

- [ ] **Step 4: Verify no runtime references remain**

Run `rg` over Live Python/shell/config files and the repository boundary test.

- [ ] **Step 5: Commit tracked cleanup**

```bash
git add -A paper_trading tests/paper_trading live_trading docs .gitignore
git commit -m "chore: remove obsolete paper trading"
```

### Task 7: Remove ignored Paper runtime artifacts and verify the full result

**Files outside Git:**
- Delete: `/Users/yuxianqi/Project/qlib/paper_trading/data/csi300_topk10.db`
- Delete: `/Users/yuxianqi/Project/qlib/paper_trading/logs/*.log`
- Preserve: `/Users/yuxianqi/Project/qlib/live_trading/data/csi300_topk10_live.db`
- Preserve: `/Users/yuxianqi/Project/qlib/live_trading/logs/*`

- [ ] **Step 1: Reconfirm Live stock-name count before deleting Paper DB**

Expected: Live and Paper both report 5,544 names, and Live data remains readable.

- [ ] **Step 2: Remove only the explicitly authorized Paper runtime directory**

Do not remove or rewrite any Live database/log/bridge file.

- [ ] **Step 3: Run focused verification**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading \
  tests/backtest/test_topk_dropout_selection.py -q
```

- [ ] **Step 4: Run broader relevant and full tests**

Run the repository's complete test suite when feasible; report any pre-existing or environment-only failures separately.

- [ ] **Step 5: Run static verification**

```bash
git diff --check
git status --short
rg -n "paper_trading" live_trading --glob '*.py' --glob '*.yaml' --glob '*.sh'
```

Expected: zero whitespace errors, no runtime Paper references, and only scoped branch changes.

- [ ] **Step 6: Review commits and final branch diff**

Confirm the branch contains no user's unrelated main-worktree changes and all new behavior is backed by a red/green test cycle.
