# QMT Dynamic Order Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Price each first QMT live order from the current opposing quote with a 0.3% marketability buffer, remove the previous-close ±1% hard boundary, and clamp the result to QMT's daily price limits.

**Architecture:** Keep the existing JSONL protocol and one-shot order state machine unchanged. Extend the QMT bridge's read-only market-data helpers so `_effective_price` selects ask-one for buys or bid-one for sells, falls back to last price and finally the signal fallback price, then applies `UpStopPrice` or `DownStopPrice` when QMT supplies a valid instrument boundary.

**Tech Stack:** Python 3.6-compatible QMT built-in strategy code, Python 3 + pytest test suite, Markdown operations documentation.

## Global Constraints

- The QMT strategy must remain Python 3.6 compatible and use only the standard library.
- The QMT strategy file must remain ASCII-only despite its GBK declaration.
- Keep `SignalOrder.limit_price`, `buy_slippage`, `sell_slippage`, and schema version 1.0 unchanged for wire compatibility.
- Keep `INTRADAY_BUY_SLIPPAGE = 0.003` and `INTRADAY_SELL_SLIPPAGE = 0.003` unchanged.
- Do not add cancel/reprice behavior or change sell-before-buy, cash reservation, 14:56 cancellation, or 14:57 finalization.
- Do not modify backtest execution behavior or the user's existing changes in `backtest/scripts/config_loader.py` and `backtest/scripts/run_backtest.py`.

---

### Task 1: Price first submissions from the opposing quote

**Files:**
- Modify: `tests/live_trading/test_qmt_bridge_logic.py`
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`
- Modify: `live_trading/README.md`

**Interfaces:**
- Consumes: `ContextInfo.get_full_tick([stock_code]) -> dict`, `ContextInfo.get_instrumentdetail(stock_code) -> dict`, `order["side"]`, `order["stock_code"]`, and `order["limit_price"]`.
- Produces: `_positive_price(value) -> float`, `_tick_field(tick, name, default=None) -> object`, `_get_tick(ContextInfo, stock_code) -> dict|object|None`, `_get_price_limits(ContextInfo, stock_code) -> tuple[float, float]`, and `_effective_price(ContextInfo, order) -> float`.

- [ ] **Step 1: Replace the old capped-price tests with failing dynamic-price tests**

Replace `_TickCtx` and the existing effective-price tests in `tests/live_trading/test_qmt_bridge_logic.py` with:

```python
class _TickCtx:
    """Fake ContextInfo exposing QMT tick and instrument-detail fields."""
    def __init__(
        self, last_price, ask_price=None, bid_price=None,
        up_stop=0.0, down_stop=0.0, detail_error=False,
    ):
        self._last = last_price
        self._ask = [] if ask_price is None else [ask_price]
        self._bid = [] if bid_price is None else [bid_price]
        self._up_stop = up_stop
        self._down_stop = down_stop
        self._detail_error = detail_error

    def get_full_tick(self, codes):
        return {
            c: {
                "lastPrice": self._last,
                "askPrice": self._ask,
                "bidPrice": self._bid,
            }
            for c in codes
        }

    def get_instrumentdetail(self, stock_code):
        if self._detail_error:
            raise RuntimeError("instrument detail unavailable")
        return {
            "UpStopPrice": self._up_stop,
            "DownStopPrice": self._down_stop,
        }


def test_effective_price_buy_uses_ask_without_signal_cap(bridge):
    order = {"stock_code": "000001.SZ", "side": "BUY", "limit_price": 10.10}
    ctx = _TickCtx(10.50, ask_price=10.51, bid_price=10.49, up_stop=11.00)

    assert bridge._effective_price(ctx, order) == 10.54


def test_effective_price_sell_uses_bid_without_signal_floor(bridge):
    order = {"stock_code": "000001.SZ", "side": "SELL", "limit_price": 9.90}
    ctx = _TickCtx(9.50, ask_price=9.51, bid_price=9.49, down_stop=9.00)

    assert bridge._effective_price(ctx, order) == 9.46


@pytest.mark.parametrize(
    "side,ctx,expected",
    [
        (
            "BUY",
            _TickCtx(10.99, ask_price=10.99, bid_price=10.98, up_stop=11.00),
            11.00,
        ),
        (
            "SELL",
            _TickCtx(9.01, ask_price=9.02, bid_price=9.01, down_stop=9.00),
            9.00,
        ),
    ],
)
def test_effective_price_clamps_to_daily_price_limit(bridge, side, ctx, expected):
    order = {"stock_code": "000001.SZ", "side": side, "limit_price": 10.00}

    assert bridge._effective_price(ctx, order) == expected


@pytest.mark.parametrize(
    "side,expected",
    [("BUY", 10.03), ("SELL", 9.97)],
)
def test_effective_price_falls_back_from_empty_book_to_last(
    bridge, side, expected,
):
    order = {"stock_code": "000001.SZ", "side": side, "limit_price": 8.88}

    assert bridge._effective_price(_TickCtx(10.00), order) == expected


def test_effective_price_falls_back_to_signal_price_without_live_reference(bridge):
    order = {"stock_code": "000001.SZ", "side": "BUY", "limit_price": 10.10}
    ctx = _TickCtx(0.0, ask_price=0.0, bid_price=float("nan"))

    assert bridge._effective_price(ctx, order) == 10.10


def test_effective_price_survives_missing_instrument_detail(bridge):
    order = {"stock_code": "000001.SZ", "side": "BUY", "limit_price": 10.10}
    ctx = _TickCtx(10.50, ask_price=10.51, detail_error=True)

    assert bridge._effective_price(ctx, order) == 10.54
```

- [ ] **Step 2: Run the focused tests and verify the old implementation fails**

Run:

```bash
pytest -q \
  tests/live_trading/test_qmt_bridge_logic.py::test_effective_price_buy_uses_ask_without_signal_cap \
  tests/live_trading/test_qmt_bridge_logic.py::test_effective_price_sell_uses_bid_without_signal_floor \
  tests/live_trading/test_qmt_bridge_logic.py::test_effective_price_clamps_to_daily_price_limit \
  tests/live_trading/test_qmt_bridge_logic.py::test_effective_price_falls_back_from_empty_book_to_last \
  tests/live_trading/test_qmt_bridge_logic.py::test_effective_price_falls_back_to_signal_price_without_live_reference \
  tests/live_trading/test_qmt_bridge_logic.py::test_effective_price_survives_missing_instrument_detail
```

Expected: FAIL because the current `_effective_price` ignores `askPrice`, `bidPrice`, `UpStopPrice`, and `DownStopPrice`, and still caps or floors against `limit_price`.

- [ ] **Step 3: Replace the last-price-only helpers with validated tick and boundary helpers**

Replace `_get_last_price` in `live_trading/qmt_strategy/qmt_signal_bridge.py` with:

```python
def _positive_price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(price) or price <= 0.0:
        return 0.0
    return price


def _tick_field(tick, name, default=None):
    if isinstance(tick, dict):
        return tick.get(name, default)
    return getattr(tick, name, default)


def _get_tick(ContextInfo, stock_code):
    try:
        ticks = ContextInfo.get_full_tick([stock_code])
        return ticks.get(stock_code) if ticks else None
    except Exception:
        _log("get_full_tick failed for %s:\n%s"
             % (stock_code, traceback.format_exc()))
        return None


def _first_book_price(tick, field):
    levels = _tick_field(tick, field, [])
    if not isinstance(levels, (list, tuple)) or not levels:
        return 0.0
    return _positive_price(levels[0])


def _get_price_limits(ContextInfo, stock_code):
    try:
        detail = ContextInfo.get_instrumentdetail(stock_code)
    except Exception:
        _log("get_instrumentdetail failed for %s:\n%s"
             % (stock_code, traceback.format_exc()))
        return 0.0, 0.0
    if detail is None:
        return 0.0, 0.0
    if isinstance(detail, dict):
        upper = detail.get("UpStopPrice")
        lower = detail.get("DownStopPrice")
    else:
        upper = getattr(detail, "UpStopPrice", None)
        lower = getattr(detail, "DownStopPrice", None)
    return _positive_price(upper), _positive_price(lower)
```

- [ ] **Step 4: Implement opposing-quote pricing and daily-limit clamping**

Replace `_effective_price` with:

```python
def _effective_price(ContextInfo, order):
    """Marketable first-order price with the signal price as data fallback."""
    fallback_price = float(order["limit_price"])
    tick = _get_tick(ContextInfo, order["stock_code"])
    if tick is None:
        return fallback_price

    last = _positive_price(_tick_field(tick, "lastPrice"))
    if order["side"] == "BUY":
        reference = _first_book_price(tick, "askPrice") or last
    else:
        reference = _first_book_price(tick, "bidPrice") or last
    if reference <= 0.0:
        return fallback_price

    upper, lower = _get_price_limits(ContextInfo, order["stock_code"])
    if order["side"] == "BUY":
        price = round(reference * (1.0 + INTRADAY_BUY_SLIPPAGE), 2)
        if upper > 0.0:
            price = min(price, upper)
    else:
        price = round(reference * (1.0 - INTRADAY_SELL_SLIPPAGE), 2)
        if lower > 0.0:
            price = max(price, lower)
    return round(price, 2)
```

Update the file header and intraday-pricing comments so they state that pricing uses ask-one/bid-one with last-price fallback and daily-limit clamping. In `_submit`, change the log label from `mac_limit` to `fallback_price`; do not change the `passorder` arguments.

- [ ] **Step 5: Run the focused tests and verify they pass**

Run the exact pytest command from Step 2.

Expected: `8 passed` because the two parametrized functions contribute four cases in addition to the four single-case tests.

- [ ] **Step 6: Update operator documentation**

In `live_trading/README.md`, replace the 14:45 execution description with:

```markdown
不需要盯盘。策略在 **14:45 尾盘窗口**消费信号（贴近回测的收盘价成交口径）：先卖后买，买入锚定实时卖一价、卖出锚定实时买一价，并加入小缓冲（买 +0.3% / 卖 -0.3%）；对手盘缺失时退回最新价，最终价格受 QMT 当日涨跌停价约束。信号里的 `limit_price`（昨收 ±1%）只在实时行情完全不可用时作为故障回退价。14:56 自动撤未成单，14:57 强制写回执。想看进度就看 QMT 策略输出日志或 `outbound/fills_*.jsonl`。
```

Replace the slippage-review bullet with:

```markdown
- 成交质量回顾：比较 `fills.avg_price` 与下单日志中的实时参考价；如果成交率仍偏低，再单独评估撤单追价，不要通过收紧昨收边界控制风险；
```

- [ ] **Step 7: Run the complete live-trading regression suite**

Run:

```bash
pytest -q tests/live_trading
```

Expected: all tests pass with no failures.

- [ ] **Step 8: Run static and compatibility checks**

Run:

```bash
python -m py_compile live_trading/qmt_strategy/qmt_signal_bridge.py
python - <<'PY'
from pathlib import Path

path = Path("live_trading/qmt_strategy/qmt_signal_bridge.py")
path.read_bytes().decode("ascii")
print("ASCII OK")
PY
git diff --check
```

Expected: compilation succeeds, output contains `ASCII OK`, and `git diff --check` reports no whitespace errors.

- [ ] **Step 9: Commit the implementation**

```bash
git add \
  tests/live_trading/test_qmt_bridge_logic.py \
  live_trading/qmt_strategy/qmt_signal_bridge.py \
  live_trading/README.md
git commit -m "fix(live_trading): price orders from live book"
```

The commit must not include `backtest/scripts/config_loader.py` or `backtest/scripts/run_backtest.py`.
