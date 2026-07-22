# TopkDropout Live/Backtest Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the shared paper/live order manager reproduce Qlib TopkDropout's default top-buy/bottom-sell behavior, including automatically filling a portfolio shortfall and converging an overfilled portfolio on the next newly generated batch.

**Architecture:** Keep `OrderManager` as the lightweight shared boundary used by paper trading and live publishing. Use the signed `position_delta`, non-negative candidate/buy counts, combined ranking, and sell/buy calculations used by `TopkDropoutStrategy`; fail closed when effective signals are empty. Leave sizing, order planning, QMT execution, immutable published batches, and fill accounting unchanged.

**Tech Stack:** Python 3.12, pandas, pytest, Qlib repository modules.

## Global Constraints

- Apply the change to `paper_trading/modules/order_manager.py` so paper trading and live trading share one behavior.
- Match Qlib's default `method_buy="top"` and `method_sell="bottom"` selection semantics for normal and underfilled portfolios.
- Converge 11- and 12-stock portfolios to `topk` without negative slicing.
- Return no orders when effective scores are empty, so data/model failures cannot create sell-only orders.
- Do not modify or republish existing signal batches, positions, cash, or fill history.
- Do not add intraday retry, repricing, cancellation, or QMT behavior.
- Preserve the existing 95% cash usage, estimated sell proceeds, price validation, and board-lot rounding.
- Do not stage or commit unrelated pre-existing workspace changes.

---

### Task 1: Implement TopkDropout parity with TDD

**Files:**
- Create: `tests/paper_trading/test_order_manager.py`
- Modify: `paper_trading/modules/order_manager.py:39-104`

**Interfaces:**
- Consumes: `OrderManager.generate_orders(scores, current_positions, cash, close_prices, total_value) -> list[dict]`
- Produces: Qlib-equivalent default BUY/SELL selection for normal, underfilled, and overfilled portfolios, with fail-closed empty-signal handling and regression coverage for selection and sizing rules.

- [ ] **Step 1: Write shared test helpers and four selection tests**

```python
import pandas as pd

from paper_trading.modules.order_manager import OrderManager


def _manager():
    return OrderManager({
        "strategy": {"topk": 10, "n_drop": 2},
        "exchange": {"trade_unit": 100},
    })


def _scores(count=14):
    instruments = [f"SH600{i:03d}" for i in range(count)]
    return pd.Series(
        range(count, 0, -1), index=instruments, dtype=float,
    )


def _positions(instruments):
    return {
        instrument: {"shares": 100, "cost_price": 10.0}
        for instrument in instruments
    }


def _prices(scores):
    return {instrument: 10.0 for instrument in scores.index}


def _instruments(orders, direction):
    return [
        order["instrument"]
        for order in orders
        if order["direction"] == direction
    ]


def test_full_portfolio_rotates_two_positions():
    scores = _scores()
    held = list(scores.index[:8]) + list(scores.index[10:12])

    orders = _manager().generate_orders(
        scores, _positions(held), 10_000.0, _prices(scores), 20_000.0,
    )

    assert set(_instruments(orders, "SELL")) == set(scores.index[10:12])
    assert _instruments(orders, "BUY") == list(scores.index[8:10])


def test_underfilled_portfolio_rotates_and_fills_gap():
    scores = _scores()
    held = list(scores.index[:7]) + list(scores.index[10:12])

    orders = _manager().generate_orders(
        scores, _positions(held), 10_000.0, _prices(scores), 19_000.0,
    )

    assert set(_instruments(orders, "SELL")) == set(scores.index[10:12])
    assert _instruments(orders, "BUY") == list(scores.index[7:10])


def test_underfilled_top_ranked_portfolio_only_fills_gap():
    scores = _scores()
    held = list(scores.index[:9])

    orders = _manager().generate_orders(
        scores, _positions(held), 10_000.0, _prices(scores), 19_000.0,
    )

    assert _instruments(orders, "SELL") == []
    assert _instruments(orders, "BUY") == [scores.index[9]]


def test_empty_portfolio_buys_topk():
    scores = _scores()

    orders = _manager().generate_orders(
        scores, {}, 100_000.0, _prices(scores), 100_000.0,
    )

    assert _instruments(orders, "SELL") == []
    assert _instruments(orders, "BUY") == list(scores.index[:10])
```

- [ ] **Step 2: Add a sizing/price characterization test**

```python
def test_buy_orders_keep_price_filter_and_board_lot_rounding():
    scores = _scores()
    prices = _prices(scores)
    prices[scores.index[1]] = 0.0

    orders = _manager().generate_orders(
        scores, {}, 100_000.0, prices, 100_000.0,
    )

    buys = [order for order in orders if order["direction"] == "BUY"]
    assert scores.index[1] not in _instruments(orders, "BUY")
    assert buys
    assert all(order["target_shares"] > 0 for order in buys)
    assert all(order["target_shares"] % 100 == 0 for order in buys)
```

- [ ] **Step 3: Run the focused tests and verify the regression is red**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/paper_trading/test_order_manager.py -q
```

Expected: the new empty-effective-score and 11/12-stock convergence tests fail:
the current implementation can create sell-only orders from empty effective scores,
limits an 11-stock portfolio to one sell, and keeps a 12-stock portfolio at 12 by
buying two replacements. Existing full-portfolio, empty-portfolio, and precise
sizing tests pass.

- [ ] **Step 4: Replace top-10 set-difference selection with the Qlib calculation**

After sorting and dropping missing scores, return `[]` with a warning when no
effective scores remain. Otherwise use this selection block:

```python
current_stock_list = list(current_positions)
last = scores.reindex(current_stock_list).sort_values(ascending=False).index
position_delta = self.topk - len(last)

today_count = max(self.n_drop + position_delta, 0)
today = scores[~scores.index.isin(last)].head(today_count).index
combined = scores.reindex(last.union(today)).sort_values(ascending=False).index
bottom = set(combined[-self.n_drop:]) if self.n_drop > 0 else set()
sell_from_candidates = [instrument for instrument in last if instrument in bottom]
buy_count = max(len(sell_from_candidates) + position_delta, 0)
buy_list = list(today[:buy_count])
```

`today_count` and `buy_count` remain non-negative. With 11 stocks,
`position_delta=-1` produces two sells and one buy; with 12 stocks,
`position_delta=-2` produces two sells and zero buys. Keep order construction
and budget calculation unchanged.

- [ ] **Step 5: Run the focused tests and verify green**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/paper_trading/test_order_manager.py -q
```

Expected: all focused tests pass, including empty effective scores, 11/12-stock
convergence, exact invalid-price sizing (nine 900-share buys), and normal
two-sell/two-buy sizing (two 500-share buys).

- [ ] **Step 6: Run the existing paper/live unit suites**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/paper_trading tests/live_trading -q
```

Expected: all collected tests pass with zero failures. If `tests/paper_trading` contains only the new file, pytest still runs the shared regression tests plus the complete live-trading suite.

- [ ] **Step 7: Verify formatting and inspect the exact diff**

Run:

```bash
git diff --check -- paper_trading/modules/order_manager.py tests/paper_trading/test_order_manager.py
git diff -- paper_trading/modules/order_manager.py tests/paper_trading/test_order_manager.py
```

Expected: `git diff --check` prints nothing; the diff contains only the selection change and its tests.

- [ ] **Step 8: Commit only the implementation and regression test**

```bash
git add paper_trading/modules/order_manager.py tests/paper_trading/test_order_manager.py
git commit -m "fix: align live TopkDropout with backtest"
```

Expected: commit succeeds without staging unrelated workspace files.

### Task 2: Fresh post-commit verification

**Files:**
- Verify: `paper_trading/modules/order_manager.py`
- Verify: `tests/paper_trading/test_order_manager.py`

**Interfaces:**
- Consumes: committed implementation.
- Produces: fresh evidence that the committed change passes focused and integration tests and that unrelated changes remain uncommitted.

- [ ] **Step 1: Re-run focused and integration tests from the committed tree**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/paper_trading/test_order_manager.py tests/live_trading -q
```

Expected: all collected tests pass with zero failures.

- [ ] **Step 2: Confirm commit contents and workspace separation**

Run:

```bash
git show --stat --oneline --summary HEAD
git status --short
```

Expected: the implementation commit contains only `order_manager.py` and its new test file; pre-existing unrelated workspace changes remain unstaged.
