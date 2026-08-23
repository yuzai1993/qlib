"""真阶梯（cohort ladder）组合簿记：按持有天数到期退出，而不是按打分排名。

Phase M 主格 top-k × h 的评估年化 ``mean(p) × 238/h``（见
``backtest/scripts/eval_ic_multi_pool.py``）恒等于这样一条组合的算术年化：k·h 个
等额仓位、每日买入当日 top-k、每只持满 h 天无条件卖出。

与 ``TopkDropoutStrategy`` 有两处根本区别，这也是 25 槽 TopkDropout 复刻不了主格的
原因（实测入场中位名次 6~7、最差 18，装的是 top20 篮子）：

1. 退出按**持有天数**到期，不看当日排名；
2. 同一只票可以被多个分层同时持有——连续上榜就自动加仓，永远不会因为「前排都在手上」
   而被迫去买后排。

本模块只做纯簿记与选股，不碰交易所；下单与撮合在
``qlib.contrib.strategy.signal_strategy.CohortLadderStrategy``。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Optional

import pandas as pd

from .topk_dropout import stable_rank_scores

_EPS = 1e-9


def select_ladder_buys(
    scores: pd.Series,
    *,
    k: int,
    is_buyable: Optional[Callable[[str], bool]] = None,
) -> tuple[str, ...]:
    """返回当日分层要买的至多 ``k`` 只。

    ``is_buyable`` 为 ``None`` 表示不做可买过滤；给定时按分数顺延取下一个可买的，
    这与评估先剔 t+1 涨停再取 top-k 等价。判定是惰性的：沿排名走到凑够 k 只就停，
    不对整个全A 截面逐只查交易所。

    阶梯**故意不去重**：已被其他分层持有的票照样入选，连续上榜就自动加仓。
    """
    if k <= 0:
        raise ValueError("k must be a positive integer")
    ranked = stable_rank_scores(scores)
    if is_buyable is None:
        return tuple(ranked.index[:k])
    picked: list[str] = []
    for name in ranked.index:
        if is_buyable(name):
            picked.append(name)
            if len(picked) >= k:
                break
    return tuple(picked)


def select_ladder_refills(
    scores: pd.Series,
    *,
    n: int,
    exclude: Iterable[str],
    is_buyable: Optional[Callable[[str], bool]] = None,
) -> tuple[str, ...]:
    """强平之后顺延补 ``n`` 只新票，跳过当日 top-k 和仍持有的名字。"""
    if n < 0:
        raise ValueError("n must be a non-negative integer")
    if n == 0:
        return ()
    ranked = stable_rank_scores(scores)
    blocked = {str(name) for name in exclude}
    picked: list[str] = []
    for name in ranked.index:
        if name in blocked:
            continue
        if is_buyable is not None and not is_buyable(name):
            continue
        picked.append(name)
        if len(picked) >= n:
            break
    return tuple(picked)


def force_sell_names(
    scores: pd.Series,
    holdings: Iterable[str],
    force_sell_rank: Optional[int],
) -> tuple[str, ...]:
    """持仓里排名差于 ``force_sell_rank``（1-based）或没有有限分数的票。"""
    if force_sell_rank is None:
        return ()
    cutoff = int(force_sell_rank)
    if cutoff < 1:
        raise ValueError("force_sell_rank must be a positive integer or None")
    ranked = stable_rank_scores(scores)
    rank_pos = {inst: i + 1 for i, inst in enumerate(ranked.index)}
    return tuple(
        name for name in holdings if rank_pos.get(name, cutoff + 1) > cutoff
    )


def cohort_budget(
    *, total_value: float, cash: float, risk_degree: float, horizon: int
) -> float:
    """当日分层的总买入预算：目标暴露的 1/h，且不得透支现金。"""
    if horizon <= 0:
        raise ValueError("horizon must be a positive integer")
    target = float(total_value) * float(risk_degree) / float(horizon)
    return max(min(target, float(cash)), 0.0)


class CohortLedger:
    """按买入日分层记录股数，供「持满 h 天到期卖出」使用。

    ``_cohorts`` 按买入先后排列，索引 0 最老。``_pending`` 是已到期但当日没卖掉的
    残量（停牌 / 跌停），必须挂账重试，否则这部分持仓会从台账消失而实际还在账户里。
    """

    def __init__(self, horizon: int) -> None:
        if horizon <= 0:
            raise ValueError("horizon must be a positive integer")
        self.horizon = int(horizon)
        self._cohorts: list[dict[str, float]] = []
        self._pending: dict[str, float] = {}

    @property
    def cohort_count(self) -> int:
        return len(self._cohorts)

    def holdings(self) -> dict[str, float]:
        """台账口径的合计持仓（含待清算残量）。"""
        total: dict[str, float] = {}
        for cohort in [*self._cohorts, self._pending]:
            for name, amount in cohort.items():
                total[name] = total.get(name, 0.0) + amount
        return {name: amount for name, amount in total.items() if amount > _EPS}

    def due(self) -> dict[str, float]:
        """今天该清掉的股数：满 h 天的那层 + 历史没卖掉的残量。"""
        out = dict(self._pending)
        if len(self._cohorts) >= self.horizon:
            for name, amount in self._cohorts[0].items():
                out[name] = out.get(name, 0.0) + amount
        return {name: amount for name, amount in out.items() if amount > _EPS}

    def settle(self, sold: Mapping[str, float]) -> None:
        """按实际成交扣减；到期层无论卖光与否都退出阶梯，残量转入待清算。"""
        remaining = dict(self._pending)
        if len(self._cohorts) >= self.horizon:
            matured = self._cohorts.pop(0)
            for name, amount in matured.items():
                remaining[name] = remaining.get(name, 0.0) + amount
        for name, amount in sold.items():
            if name in remaining:
                remaining[name] -= float(amount)
        self._pending = {
            name: amount for name, amount in remaining.items() if amount > _EPS
        }

    def extract(self, names: Iterable[str]) -> dict[str, float]:
        """从所有分层和待清算残量里抽出这些票，供提前强平。"""
        wanted = {str(name) for name in names}
        pulled: dict[str, float] = {}
        if not wanted:
            return pulled
        for bucket in [*self._cohorts, self._pending]:
            for name in list(bucket):
                if name not in wanted:
                    continue
                pulled[name] = pulled.get(name, 0.0) + float(bucket.pop(name))
        return {name: amount for name, amount in pulled.items() if amount > _EPS}

    def park_unsold(self, remaining: Mapping[str, float]) -> None:
        """强平没卖完的残量挂回待清算，次日重试。"""
        for name, amount in remaining.items():
            qty = float(amount)
            if qty > _EPS:
                self._pending[name] = self._pending.get(name, 0.0) + qty

    def add(self, filled: Mapping[str, float]) -> None:
        """记入今天买成的新分层。空分层也要占位，否则阶梯的账龄会错位。"""
        self._cohorts.append(
            {name: float(amount) for name, amount in filled.items() if amount > _EPS}
        )

    def reconcile(self, actual: Mapping[str, float]) -> None:
        """把台账收敛到真实持仓。

        买单可能整单落空（决策时可买、撮合时涨停），此时台账会比账户多。多出来的部分
        从**最新**分层开始削——最近那笔买入落空的可能性最大；待清算残量最后削，因为
        它已经被证实存在过。
        """
        actual_amounts = {
            name: float(amount) for name, amount in actual.items() if amount > _EPS
        }
        for name, ledger_total in self.holdings().items():
            surplus = ledger_total - actual_amounts.get(name, 0.0)
            if surplus <= _EPS:
                continue
            for bucket in [*reversed(self._cohorts), self._pending]:
                if surplus <= _EPS:
                    break
                held = bucket.get(name)
                if held is None:
                    continue
                cut = min(held, surplus)
                bucket[name] = held - cut
                surplus -= cut
                if bucket[name] <= _EPS:
                    del bucket[name]

    def to_state(self) -> dict[str, Any]:
        """导出可序列化的台账快照，供跨进程持久化。

        ``cohorts`` 保持索引 0 最老的层序；空层照样导出，否则重建后阶梯账龄会错位。
        """
        return {
            "horizon": self.horizon,
            "cohorts": [dict(cohort) for cohort in self._cohorts],
            "pending": dict(self._pending),
        }

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "CohortLedger":
        """从 ``to_state`` 的快照重建台账。

        层数的合法区间是 ``[0, horizon]``：每日先 ``settle``（层数达 ``horizon`` 时
        弹出最老层）再 ``add``（恒定追加一层）。超出即说明有人重复推进过某一天，
        此时宁可拒绝也不能放行——多出来的层会让后续所有到期日集体错位。
        """
        ledger = cls(int(state["horizon"]))
        cohorts = list(state.get("cohorts") or [])
        if len(cohorts) > ledger.horizon:
            raise ValueError(
                f"cohorts ({len(cohorts)}) exceeds horizon ({ledger.horizon})"
            )
        ledger._cohorts = [
            {
                str(name): float(amount)
                for name, amount in cohort.items()
                if float(amount) > _EPS
            }
            for cohort in cohorts
        ]
        ledger._pending = {
            str(name): float(amount)
            for name, amount in (state.get("pending") or {}).items()
            if float(amount) > _EPS
        }
        return ledger


def ledger_sell_amounts(
    due: Mapping[str, float], position_amounts: Mapping[str, float]
) -> dict[str, float]:
    """到期股数与真实持仓取小，避免下出超过持仓的卖单。"""
    out: dict[str, float] = {}
    for name, amount in due.items():
        available = float(position_amounts.get(name, 0.0))
        sell = min(float(amount), available)
        if sell > _EPS:
            out[name] = sell
    return out
