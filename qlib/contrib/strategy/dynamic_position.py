"""TopkDropout + 指数均线择时的动态仓位策略。

思路（方案 B）：
- 选股沿用 TopkDropoutStrategy 的 topk/n_drop 轮换逻辑；
- 每个交易日按基准指数（默认 SH000300）的双均线状态确定目标仓位 risk_degree：
    bull    : close > MA(fast) > MA(slow)   → risk_bull（默认 0.95）
    bear    : close < MA(fast) < MA(slow)   → risk_bear（默认 0.40）
    neutral : 其余（含均线未形成的暖机期）  → risk_neutral（默认 0.70）
- 原生 TopkDropout 的 risk_degree 只影响买入使用现金的比例，降仓不会卖出；
  本策略在超出目标仓位时按比例减持所有持仓（风控减仓不受 hold_thresh 限制），
  买入预算改为「组合总值 × 目标仓位 − 当前股票市值」，实现双向仓位控制。
"""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from qlib.backtest.decision import Order, OrderDir, TradeDecisionWO
from qlib.backtest.position import Position
from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy
from qlib.data import D
from qlib.log import get_module_logger


def compute_risk_degree_series(
    close: pd.Series,
    fast_window: int = 20,
    slow_window: int = 60,
    risk_bull: float = 0.95,
    risk_neutral: float = 0.70,
    risk_bear: float = 0.40,
) -> pd.Series:
    """由收盘价序列计算逐日目标仓位。

    暖机期（慢均线未形成）与均线纠缠时取 risk_neutral。
    """
    close = close.dropna().astype(float)
    ma_fast = close.rolling(fast_window).mean()
    ma_slow = close.rolling(slow_window).mean()

    risk = pd.Series(risk_neutral, index=close.index, dtype=float)
    valid = ma_slow.notna()
    bull = valid & (close > ma_fast) & (ma_fast > ma_slow)
    bear = valid & (close < ma_fast) & (ma_fast < ma_slow)
    risk[bull] = risk_bull
    risk[bear] = risk_bear
    return risk


def lookup_risk_degree(risk_series: pd.Series | None, date: pd.Timestamp, default: float) -> float:
    """取 date 当日（或之前最近一日）的目标仓位；序列为空或 date 早于首日时返回 default。"""
    if risk_series is None or len(risk_series) == 0:
        return default
    s = risk_series.sort_index()
    pos = s.index.searchsorted(pd.Timestamp(date), side="right")
    if pos == 0:
        return default
    return float(s.iloc[pos - 1])


class TopkDropoutTimingStrategy(TopkDropoutStrategy):
    """带指数双均线择时的 TopkDropout 策略。

    Parameters
    ----------
    timing_benchmark : str
        择时用的指数代码（qlib instrument，如 SH000300）。
    fast_window / slow_window : int
        快、慢均线窗口（交易日）。
    risk_bull / risk_neutral / risk_bear : float
        三种市场状态对应的目标仓位（占组合总值比例）。
    rebalance_tolerance : float
        仓位偏离容忍带（占组合总值比例）。当前股票市值超过
        目标市值 + tolerance×总值 时才触发减仓，避免小幅偏离反复交易。
    risk_series : pd.Series, optional
        直接注入逐日目标仓位（datetime index）。缺省时在首次调仓前
        由 timing_benchmark 的收盘价自动计算。

    其余参数（topk、n_drop、hold_thresh 等）与 TopkDropoutStrategy 一致。
    风控减仓不受 hold_thresh / n_drop 限制。
    """

    def __init__(
        self,
        *,
        timing_benchmark: str = "SH000300",
        fast_window: int = 20,
        slow_window: int = 60,
        risk_bull: float = 0.95,
        risk_neutral: float = 0.70,
        risk_bear: float = 0.40,
        rebalance_tolerance: float = 0.02,
        risk_series: pd.Series | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.timing_benchmark = timing_benchmark
        self.fast_window = int(fast_window)
        self.slow_window = int(slow_window)
        self.risk_bull = float(risk_bull)
        self.risk_neutral = float(risk_neutral)
        self.risk_bear = float(risk_bear)
        self.rebalance_tolerance = float(rebalance_tolerance)
        self._risk_series = risk_series
        self.logger = get_module_logger("TopkDropoutTimingStrategy")

    # ---- 择时信号 ----

    def _ensure_risk_series(self) -> None:
        if self._risk_series is not None:
            return
        start_time, end_time = self.trade_calendar.get_all_time()
        # 往前多取自然日，保证回测首日慢均线已形成
        fetch_start = pd.Timestamp(start_time) - pd.Timedelta(days=self.slow_window * 3)
        df = D.features(
            [self.timing_benchmark],
            ["$close"],
            start_time=fetch_start,
            end_time=end_time,
            freq="day",
        )
        close = df["$close"].xs(self.timing_benchmark, level="instrument")
        self._risk_series = compute_risk_degree_series(
            close,
            fast_window=self.fast_window,
            slow_window=self.slow_window,
            risk_bull=self.risk_bull,
            risk_neutral=self.risk_neutral,
            risk_bear=self.risk_bear,
        )
        self.logger.info(
            "timing risk series ready: %s [%s, %s], bull/neutral/bear = %d/%d/%d days",
            self.timing_benchmark,
            self._risk_series.index[0].date(),
            self._risk_series.index[-1].date(),
            int((self._risk_series == self.risk_bull).sum()),
            int((self._risk_series == self.risk_neutral).sum()),
            int((self._risk_series == self.risk_bear).sum()),
        )

    def _risk_for_date(self, date: pd.Timestamp) -> float:
        self._ensure_risk_series()
        return lookup_risk_degree(self._risk_series, date, default=self.risk_degree)

    def get_risk_degree(self, trade_step: int | None = None) -> float:
        if trade_step is None:
            trade_step = self.trade_calendar.get_trade_step()
        trade_start_time, _ = self.trade_calendar.get_step_time(trade_step)
        return self._risk_for_date(trade_start_time)

    # ---- 调仓 ----

    def generate_trade_decision(self, execute_result=None):
        # 与 TopkDropoutStrategy.generate_trade_decision 同构，改动点：
        # 1) 按当日择时结果确定 risk_degree；
        # 2) dropout 卖出后若仍超过目标仓位，按比例减持全部持仓；
        # 3) 买入预算 = 目标股票市值 − 当前股票市值（原实现为 cash × risk_degree）。
        trade_step = self.trade_calendar.get_trade_step()
        trade_start_time, trade_end_time = self.trade_calendar.get_step_time(trade_step)
        pred_start_time, pred_end_time = self.trade_calendar.get_step_time(trade_step, shift=1)
        pred_score = self.signal.get_signal(start_time=pred_start_time, end_time=pred_end_time)
        if isinstance(pred_score, pd.DataFrame):
            pred_score = pred_score.iloc[:, 0]
        if pred_score is None:
            return TradeDecisionWO([], self)

        risk_degree = self._risk_for_date(trade_start_time)

        if self.only_tradable:

            def get_first_n(li, n, reverse=False):
                cur_n = 0
                res = []
                for si in reversed(li) if reverse else li:
                    if self.trade_exchange.is_stock_tradable(
                        stock_id=si, start_time=trade_start_time, end_time=trade_end_time
                    ):
                        res.append(si)
                        cur_n += 1
                        if cur_n >= n:
                            break
                return res[::-1] if reverse else res

            def get_last_n(li, n):
                return get_first_n(li, n, reverse=True)

            def filter_stock(li):
                return [
                    si
                    for si in li
                    if self.trade_exchange.is_stock_tradable(
                        stock_id=si, start_time=trade_start_time, end_time=trade_end_time
                    )
                ]

        else:

            def get_first_n(li, n):
                return list(li)[:n]

            def get_last_n(li, n):
                return list(li)[-n:]

            def filter_stock(li):
                return li

        current_temp: Position = copy.deepcopy(self.trade_position)
        sell_order_list = []
        buy_order_list = []
        cash = current_temp.get_cash()
        current_stock_list = current_temp.get_stock_list()
        last = pred_score.reindex(current_stock_list).sort_values(ascending=False).index

        if self.method_buy == "top":
            today = get_first_n(
                pred_score[~pred_score.index.isin(last)].sort_values(ascending=False).index,
                self.n_drop + self.topk - len(last),
            )
        elif self.method_buy == "random":
            topk_candi = get_first_n(pred_score.sort_values(ascending=False).index, self.topk)
            candi = list(filter(lambda x: x not in last, topk_candi))
            n = self.n_drop + self.topk - len(last)
            try:
                today = np.random.choice(candi, n, replace=False)
            except ValueError:
                today = candi
        else:
            raise NotImplementedError(f"This type of input is not supported")

        comb = pred_score.reindex(last.union(pd.Index(today))).sort_values(ascending=False).index

        if self.method_sell == "bottom":
            sell = last[last.isin(get_last_n(comb, self.n_drop))]
        elif self.method_sell == "random":
            candi = filter_stock(last)
            try:
                sell = pd.Index(np.random.choice(candi, self.n_drop, replace=False) if len(last) else [])
            except ValueError:
                sell = candi
        else:
            raise NotImplementedError(f"This type of input is not supported")

        buy = today[: len(sell) + self.topk - len(last)]

        # ---- dropout 卖出（与基类一致）----
        for code in current_stock_list:
            if not self.trade_exchange.is_stock_tradable(
                stock_id=code,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else OrderDir.SELL,
            ):
                continue
            if code in sell:
                time_per_step = self.trade_calendar.get_freq()
                if current_temp.get_stock_count(code, bar=time_per_step) < self.hold_thresh:
                    continue
                sell_amount = current_temp.get_stock_amount(code=code)
                sell_order = Order(
                    stock_id=code,
                    amount=sell_amount,
                    start_time=trade_start_time,
                    end_time=trade_end_time,
                    direction=Order.SELL,
                )
                if self.trade_exchange.check_order(sell_order):
                    sell_order_list.append(sell_order)
                    trade_val, trade_cost, trade_price = self.trade_exchange.deal_order(
                        sell_order, position=current_temp
                    )
                    cash += trade_val - trade_cost

        # ---- 择时减仓：超过目标仓位时按比例减持 ----
        stock_value = current_temp.calculate_stock_value()
        total_value = stock_value + cash
        target_stock_value = total_value * risk_degree
        if stock_value > target_stock_value + self.rebalance_tolerance * total_value:
            trim_ratio = (stock_value - target_stock_value) / stock_value
            for code in current_temp.get_stock_list():
                if not self.trade_exchange.is_stock_tradable(
                    stock_id=code,
                    start_time=trade_start_time,
                    end_time=trade_end_time,
                    direction=None if self.forbid_all_trade_at_limit else OrderDir.SELL,
                ):
                    continue
                trim_amount = current_temp.get_stock_amount(code=code) * trim_ratio
                factor = self.trade_exchange.get_factor(
                    stock_id=code, start_time=trade_start_time, end_time=trade_end_time
                )
                trim_amount = self.trade_exchange.round_amount_by_trade_unit(trim_amount, factor)
                if trim_amount <= 0:
                    continue
                trim_order = Order(
                    stock_id=code,
                    amount=trim_amount,
                    start_time=trade_start_time,
                    end_time=trade_end_time,
                    direction=Order.SELL,
                )
                if self.trade_exchange.check_order(trim_order):
                    sell_order_list.append(trim_order)
                    trade_val, trade_cost, trade_price = self.trade_exchange.deal_order(
                        trim_order, position=current_temp
                    )
                    cash += trade_val - trade_cost

        # ---- 买入：预算受目标仓位约束 ----
        stock_value = current_temp.calculate_stock_value()
        total_value = stock_value + cash
        buy_budget = max(0.0, min(total_value * risk_degree - stock_value, cash))
        value = buy_budget / len(buy) if len(buy) > 0 else 0

        for code in buy:
            if not self.trade_exchange.is_stock_tradable(
                stock_id=code,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else OrderDir.BUY,
            ):
                continue
            buy_price = self.trade_exchange.get_deal_price(
                stock_id=code, start_time=trade_start_time, end_time=trade_end_time, direction=OrderDir.BUY
            )
            buy_amount = value / buy_price
            factor = self.trade_exchange.get_factor(stock_id=code, start_time=trade_start_time, end_time=trade_end_time)
            buy_amount = self.trade_exchange.round_amount_by_trade_unit(buy_amount, factor)
            buy_order = Order(
                stock_id=code,
                amount=buy_amount,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=Order.BUY,
            )
            buy_order_list.append(buy_order)
        return TradeDecisionWO(sell_order_list + buy_order_list, self)
