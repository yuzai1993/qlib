"""分层账本状态与台账视图之间的纯转换。

权威副本始终是 SQLite；``CohortLedger`` 只是单次进程内的临时视图。发布（16:00）与
回执导入（15:31 之后）是两个进程，中间隔着 QMT 执行，任何一步失败都不写回，下次从
DB 重建并对券商快照 reconcile。

每层的买入日**不在** ``CohortLedger`` 里（``_cohorts`` 只是 list，``add`` 不接收
日期），所以由本模块维护，且不靠事后反推层数变化——``settle`` 的弹出条件在调用前就能
由 ``len(layers) >= horizon`` 算出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from qlib.contrib.strategy.cohort_ladder import CohortLedger


@dataclass(frozen=True)
class CohortState:
    """账本的持久化形态。``layers`` 索引 0 最老，空层照样占位。"""

    layers: tuple[tuple[str, dict[str, int]], ...] = ()
    pending: dict[str, int] = field(default_factory=dict)


EMPTY_COHORT_STATE = CohortState()


def _whole_shares(amounts: Mapping[str, float]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, amount in amounts.items():
        value = float(amount)
        if abs(value - round(value)) > 1e-6:
            raise ValueError(f"cohort ledger holds whole shares only: {name}={value}")
        shares = int(round(value))
        if shares > 0:
            out[str(name)] = shares
    return out


def state_to_ledger(state: CohortState, *, horizon: int) -> CohortLedger:
    """把持久化状态还原成台账视图。"""
    return CohortLedger.from_state({
        "horizon": horizon,
        "cohorts": [dict(shares) for _, shares in state.layers],
        "pending": dict(state.pending),
    })


def _ledger_to_state(ledger: CohortLedger, dates: list[str]) -> CohortState:
    snapshot: dict[str, Any] = ledger.to_state()
    cohorts = snapshot["cohorts"]
    if len(cohorts) != len(dates):
        raise ValueError(
            f"layer/date count mismatch: {len(cohorts)} layers vs {len(dates)} dates"
        )
    return CohortState(
        layers=tuple(
            (date, _whole_shares(shares)) for date, shares in zip(dates, cohorts)
        ),
        pending=_whole_shares(snapshot["pending"]),
    )


def reconciled_state(
    state: CohortState,
    broker_positions: Mapping[str, float],
    *,
    horizon: int,
) -> tuple[CohortState, dict[str, float]]:
    """先削减台账多出的部分，再吸收券商多出的部分。层数与层日期都不变。"""
    ledger = state_to_ledger(state, horizon=horizon)
    ledger.reconcile(broker_positions)
    absorbed = ledger.absorb_broker_excess(broker_positions)
    dates = [date for date, _ in state.layers]
    return _ledger_to_state(ledger, dates), absorbed


def advanced_state(
    state: CohortState,
    *,
    horizon: int,
    trade_date: str,
    sold: Mapping[str, float],
    filled: Mapping[str, float],
) -> CohortState:
    """按当日实际成交推进一天：``settle(卖出)`` 后 ``add(买入)``。

    ``settle`` 在层数达 ``horizon`` 时弹出最老层，``add`` 恒定追加一层，所以新的
    日期列表由旧列表按同一规则推导。重复推进同一天会被拒——否则阶梯会涨到
    ``horizon + 1`` 层，后续所有到期日集体错位。
    """
    if any(date == trade_date for date, _ in state.layers):
        raise ValueError(f"cohort layer for {trade_date} already exists")
    ledger = state_to_ledger(state, horizon=horizon)
    matured = len(state.layers) >= horizon
    ledger.settle(sold)
    ledger.add(_whole_shares(filled))
    dates = [date for date, _ in state.layers]
    if matured:
        dates = dates[1:]
    dates.append(trade_date)
    return _ledger_to_state(ledger, dates)
