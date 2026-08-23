"""分层账本状态层：状态 ↔ 台账视图的转换，以及每层买入日的维护。"""

import pytest

from live_trading.modules.cohort_store import (
    EMPTY_COHORT_STATE,
    CohortState,
    advanced_state,
    reconciled_state,
    state_to_ledger,
)
from live_trading.modules.fill_importer import LiveRecorder


def _recorder(tmp_path):
    return LiveRecorder(str(tmp_path / "ladder.db"), opening_cash=1_000_000.0)


def test_state_to_ledger_preserves_layer_order():
    state = CohortState(
        layers=(
            ("2026-08-17", {"SH600000": 100}),
            ("2026-08-18", {}),
            ("2026-08-19", {"SZ000001": 200}),
        ),
        pending={"SH600519": 300},
    )

    ledger = state_to_ledger(state, horizon=5)

    assert ledger.horizon == 5
    assert ledger.to_state()["cohorts"] == [
        {"SH600000": 100.0}, {}, {"SZ000001": 200.0},
    ]
    assert ledger.to_state()["pending"] == {"SH600519": 300.0}


def test_advanced_state_appends_layer_without_maturing_below_horizon():
    state = CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={})

    out = advanced_state(
        state, horizon=5, trade_date="2026-08-20",
        sold={}, filled={"SZ000001": 200},
    )

    assert out.layers == (
        ("2026-08-19", {"SH600000": 100}),
        ("2026-08-20", {"SZ000001": 200}),
    )
    assert out.pending == {}


def test_advanced_state_pops_oldest_layer_at_horizon():
    state = CohortState(
        layers=tuple(
            (f"2026-08-1{i}", {f"SH60000{i}": 100}) for i in range(5)
        ),
        pending={},
    )

    out = advanced_state(
        state, horizon=5, trade_date="2026-08-20",
        sold={"SH600000": 100}, filled={"SZ000001": 200},
    )

    dates = [date for date, _ in out.layers]
    assert dates == [
        "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-20",
    ]
    assert out.pending == {}


def test_advanced_state_parks_unsold_due_amount_in_pending():
    state = CohortState(
        layers=tuple(
            (f"2026-08-1{i}", {"SH600000": 500} if i == 0 else {}) for i in range(5)
        ),
        pending={},
    )

    # 到期层 500 股只卖掉 200（停牌 / 跌停），300 股必须挂账次日重试
    out = advanced_state(
        state, horizon=5, trade_date="2026-08-20",
        sold={"SH600000": 200}, filled={},
    )

    assert out.pending == {"SH600000": 300}
    assert out.layers[-1] == ("2026-08-20", {})


def test_advanced_state_records_empty_layer_when_all_buys_miss():
    state = CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={})

    out = advanced_state(
        state, horizon=5, trade_date="2026-08-20", sold={}, filled={},
    )

    assert out.layers[-1] == ("2026-08-20", {})


def test_advanced_state_rejects_duplicate_trade_date():
    state = CohortState(layers=(("2026-08-20", {"SH600000": 100}),), pending={})

    with pytest.raises(ValueError, match="already exists"):
        advanced_state(
            state, horizon=5, trade_date="2026-08-20", sold={}, filled={},
        )


def test_advanced_state_rejects_fractional_shares():
    state = CohortState(layers=(), pending={})

    with pytest.raises(ValueError, match="whole shares"):
        advanced_state(
            state, horizon=5, trade_date="2026-08-20",
            sold={}, filled={"SH600000": 100.5},
        )


def test_reconciled_state_prunes_ledger_surplus_and_keeps_dates():
    state = CohortState(
        layers=(
            ("2026-08-19", {"SH600000": 100}),
            ("2026-08-20", {"SH600000": 200}),
        ),
        pending={},
    )

    # 券商只有 100 股：最新一层的买单整单落空
    out, absorbed = reconciled_state(state, {"SH600000": 100}, horizon=5)

    assert absorbed == {}
    assert out.layers == (
        ("2026-08-19", {"SH600000": 100}),
        ("2026-08-20", {}),
    )


def test_reconciled_state_absorbs_broker_excess_and_reports_it():
    state = CohortState(
        layers=(
            ("2026-08-19", {"SH600000": 100}),
            ("2026-08-20", {"SH600000": 300}),
        ),
        pending={},
    )

    out, absorbed = reconciled_state(state, {"SH600000": 520}, horizon=5)

    assert absorbed == {"SH600000": 120.0}
    assert out.layers == (
        ("2026-08-19", {"SH600000": 130}),
        ("2026-08-20", {"SH600000": 390}),
    )


def test_empty_state_advances_from_scratch():
    out = advanced_state(
        EMPTY_COHORT_STATE, horizon=5, trade_date="2026-08-20",
        sold={}, filled={"SH600000": 100},
    )

    assert out.layers == (("2026-08-20", {"SH600000": 100}),)


def test_load_cohort_state_is_empty_on_fresh_db(tmp_path):
    assert _recorder(tmp_path).load_cohort_state() == EMPTY_COHORT_STATE


def test_save_then_load_cohort_state_round_trips(tmp_path):
    recorder = _recorder(tmp_path)
    state = CohortState(
        layers=(
            ("2026-08-17", {"SH600000": 100}),
            ("2026-08-18", {}),
            ("2026-08-19", {"SZ000001": 200, "SH600519": 300}),
        ),
        pending={"SH601318": 400},
    )

    recorder.save_cohort_state(state)

    assert recorder.load_cohort_state() == state


def test_saved_empty_layer_keeps_its_ladder_slot(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.save_cohort_state(
        CohortState(
            layers=(("2026-08-18", {}), ("2026-08-19", {"SH600000": 100})),
            pending={},
        )
    )

    loaded = recorder.load_cohort_state()

    assert [date for date, _ in loaded.layers] == ["2026-08-18", "2026-08-19"]
    assert loaded.layers[0][1] == {}


def test_save_cohort_state_replaces_previous_snapshot(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.save_cohort_state(
        CohortState(
            layers=(("2026-08-19", {"SH600000": 100}),), pending={"SZ000001": 50},
        )
    )

    recorder.save_cohort_state(
        CohortState(layers=(("2026-08-20", {"SH600519": 200}),), pending={})
    )

    loaded = recorder.load_cohort_state()
    assert loaded.layers == (("2026-08-20", {"SH600519": 200}),)
    assert loaded.pending == {}


def test_cohort_state_survives_a_full_day_advance(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.save_cohort_state(
        CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={})
    )

    state = advanced_state(
        recorder.load_cohort_state(), horizon=5, trade_date="2026-08-20",
        sold={}, filled={"SZ000001": 200},
    )
    recorder.save_cohort_state(state)

    assert recorder.load_cohort_state() == state
