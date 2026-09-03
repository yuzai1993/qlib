"""回执导入后推进账本：汇总口径、幂等、空层占位、卖不掉的残量挂账。"""

from live_trading.modules.cohort_advance import advance_after_import, day_executions
from live_trading.modules.cohort_store import CohortState
from live_trading.modules.fill_importer import LiveRecorder


def _fill(**kw):
    base = {
        "batch_id": "b1", "client_order_id": "c1", "mode": "LIVE",
        "stock_code": "SH600000", "side": "BUY", "status": "FILLED",
        "requested_qty": 100, "filled_qty": 100, "applied_qty": 100,
        "avg_price": 10.0, "netted_qty": 0,
    }
    base.update(kw)
    return base


def _recorder(tmp_path, state):
    recorder = LiveRecorder(str(tmp_path / "l.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(state)
    return recorder


def _stub_fills(monkeypatch, recorder, fills, *, has_batch=True):
    monkeypatch.setattr(
        recorder, "get_batches_by_date",
        lambda trade_date, strategy_id=None: (
            [{"batch_id": "b1"}] if has_batch else []
        ),
    )
    monkeypatch.setattr(recorder, "get_fills", lambda batch_id: fills)


def test_day_executions_splits_sides_and_sums_applied_qty():
    sold, filled = day_executions([
        _fill(client_order_id="c1", side="BUY", stock_code="SH600000", applied_qty=300),
        _fill(client_order_id="c2", side="BUY", stock_code="SH600000", applied_qty=200),
        _fill(client_order_id="c3", side="SELL", stock_code="SZ000001", applied_qty=400),
    ])

    assert filled == {"SH600000": 500.0}
    assert sold == {"SZ000001": 400.0}


def test_day_executions_normalizes_qmt_codes_to_qlib():
    """回执是 QMT 码，发布对账是 qlib 码；不统一就会把当日新层吸进 pending。"""
    sold, filled = day_executions([
        _fill(side="BUY", stock_code="003816.SZ", applied_qty=13600),
        _fill(side="SELL", stock_code="601998.SH", applied_qty=3000),
    ])

    assert filled == {"SZ003816": 13600.0}
    assert sold == {"SH601998": 3000.0}


def test_day_executions_ignores_non_live_and_non_terminal_fills():
    sold, filled = day_executions([
        _fill(client_order_id="c1", mode="SIMULATE", applied_qty=100),
        _fill(client_order_id="c2", status="SUBMITTED", applied_qty=0),
        _fill(client_order_id="c3", side="BUY", stock_code="SZ000001", applied_qty=100),
    ])

    assert filled == {"SZ000001": 100.0}
    assert sold == {}


def test_day_executions_drops_zero_applied_quantities():
    sold, filled = day_executions([
        _fill(client_order_id="c1", side="BUY", status="REJECTED", applied_qty=0),
    ])

    assert filled == {}
    assert sold == {}


def test_advance_after_import_appends_layer_from_actual_fills(tmp_path, monkeypatch):
    recorder = _recorder(
        tmp_path,
        CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={}),
    )
    _stub_fills(
        monkeypatch, recorder,
        [_fill(side="BUY", stock_code="SZ000001", applied_qty=200)],
    )

    state = advance_after_import(
        recorder, trade_date="2026-08-20", horizon=5, strategy_id="s",
    )

    assert state.layers == (
        ("2026-08-19", {"SH600000": 100}),
        ("2026-08-20", {"SZ000001": 200}),
    )
    assert recorder.load_cohort_state() == state


def test_advance_after_import_is_idempotent(tmp_path, monkeypatch):
    """回执导入可能一天跑多次，重复推进必须被拒而不是叠出第 6 层。"""
    recorder = _recorder(tmp_path, CohortState(layers=(), pending={}))
    _stub_fills(
        monkeypatch, recorder,
        [_fill(side="BUY", stock_code="SZ000001", applied_qty=200)],
    )

    first = advance_after_import(
        recorder, trade_date="2026-08-20", horizon=5, strategy_id="s",
    )
    second = advance_after_import(
        recorder, trade_date="2026-08-20", horizon=5, strategy_id="s",
    )

    assert second is None
    assert recorder.load_cohort_state() == first


def test_advance_after_import_parks_unsold_due_amount(tmp_path, monkeypatch):
    recorder = _recorder(tmp_path, CohortState(
        layers=tuple(
            (f"2026-08-1{i}", {"SH600000": 500} if i == 0 else {})
            for i in range(5)
        ),
        pending={},
    ))
    # 到期 500 股只卖掉 200：停牌 / 无对手盘
    _stub_fills(
        monkeypatch, recorder,
        [_fill(side="SELL", stock_code="SH600000", applied_qty=200)],
    )

    state = advance_after_import(
        recorder, trade_date="2026-08-20", horizon=5, strategy_id="s",
    )

    assert state.pending == {"SH600000": 300}
    assert state.layers[-1] == ("2026-08-20", {})


def test_advance_after_import_records_empty_layer_when_no_batch(tmp_path, monkeypatch):
    """当天没有批次（停市 / 发布失败）也要占位，否则阶梯账龄提前一天。"""
    recorder = _recorder(
        tmp_path,
        CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={}),
    )
    _stub_fills(monkeypatch, recorder, [], has_batch=False)

    state = advance_after_import(
        recorder, trade_date="2026-08-20", horizon=5, strategy_id="s",
    )

    assert state.layers[-1] == ("2026-08-20", {})


def test_netted_shares_count_as_sold_and_bought_without_any_market_fill():
    """B > S 的净买：卖腿一股没成交，但 S 股是转记走的，到期层必须退掉。"""
    sold, filled = day_executions([
        _fill(client_order_id="c1", side="SELL", stock_code="SH600000",
              status="SKIPPED", applied_qty=0, netted_qty=300),
        _fill(client_order_id="c2", side="BUY", stock_code="SH600000",
              applied_qty=200, netted_qty=300),
    ])

    assert sold == {"SH600000": 300.0}
    assert filled == {"SH600000": 500.0}


def test_residual_sell_adds_to_the_transferred_amount():
    """B < S 的净卖：转记 B，残余卖单成交 g，到期层退 B + g。"""
    sold, filled = day_executions([
        _fill(client_order_id="c1", side="SELL", stock_code="SH600000",
              status="PARTIAL", applied_qty=100, netted_qty=200),
        _fill(client_order_id="c2", side="BUY", stock_code="SH600000",
              status="SKIPPED", applied_qty=0, netted_qty=200),
    ])

    assert sold == {"SH600000": 300.0}
    assert filled == {"SH600000": 200.0}


def test_fully_offset_pair_produces_no_orders_but_still_rolls_the_ledger():
    sold, filled = day_executions([
        _fill(client_order_id="c1", side="SELL", stock_code="SH600000",
              status="SKIPPED", applied_qty=0, netted_qty=300),
        _fill(client_order_id="c2", side="BUY", stock_code="SH600000",
              status="SKIPPED", applied_qty=0, netted_qty=300),
    ])

    assert sold == {"SH600000": 300.0}
    assert filled == {"SH600000": 300.0}
