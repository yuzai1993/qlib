"""Performance reporting and analytics."""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .recorder import Recorder

logger = logging.getLogger("paper_trading.reporter")


def _f(val) -> float:
    """Safely convert a value to float, handling None/bytes/NaN."""
    if val is None:
        return 0.0
    try:
        result = float(val)
        return result if not np.isnan(result) else 0.0
    except (TypeError, ValueError):
        return 0.0


class Reporter:
    """Generates performance metrics and daily summaries."""

    def __init__(self, recorder: Recorder, initial_cash: float):
        self.recorder = recorder
        self.initial_cash = initial_cash

    def daily_summary_text(self, date_str: str) -> str:
        """Generate a text summary for a specific trading day."""
        summary_df = self.recorder.get_account_summary(start=date_str, end=date_str)
        orders_df = self.recorder.get_orders(start=date_str, end=date_str)
        positions_df = self.recorder.get_positions(date_str)
        names = self.recorder.get_stock_names()

        lines = [f"===== 模拟盘日报 {date_str} =====", ""]

        if not summary_df.empty:
            s = summary_df.iloc[0]
            lines.append(f"账户总资产: ¥{_f(s['total_value']):,.2f}")
            lines.append(f"现金余额:   ¥{_f(s['cash']):,.2f}")
            lines.append(f"持仓市值:   ¥{_f(s['market_value']):,.2f}")
            lines.append(f"当日收益率:  {_f(s['daily_return']) * 100:+.2f}%")
            lines.append(f"累计收益率:  {_f(s['cumulative_return']) * 100:+.2f}%")
            lines.append(f"基准收益率:  {_f(s['benchmark_cumulative_return']) * 100:+.2f}%")
            lines.append(f"超额收益:    {_f(s['excess_return']) * 100:+.2f}%")
            lines.append(f"持仓数量:    {int(_f(s['position_count']))}")
            lines.append(f"换手率:      {_f(s['turnover']) * 100:.2f}%")
            lines.append("")

        if not orders_df.empty:
            filled = orders_df[orders_df["status"].isin(["FILLED", "PARTIAL"])]
            buys = filled[filled["direction"] == "BUY"]
            sells = filled[filled["direction"] == "SELL"]
            rejected = orders_df[orders_df["status"] == "REJECTED"]

            if not sells.empty:
                lines.append("【卖出】")
                for _, o in sells.iterrows():
                    name = names.get(o["instrument"], "")
                    lines.append(
                        f"  {o['instrument']} {name}: "
                        f"{int(_f(o['filled_shares']))}股 @ ¥{_f(o['price']):.2f} "
                        f"= ¥{_f(o['amount']):,.2f} (佣金 ¥{_f(o['commission']):.2f})"
                    )
                lines.append("")

            if not buys.empty:
                lines.append("【买入】")
                for _, o in buys.iterrows():
                    name = names.get(o["instrument"], "")
                    lines.append(
                        f"  {o['instrument']} {name}: "
                        f"{int(_f(o['filled_shares']))}股 @ ¥{_f(o['price']):.2f} "
                        f"= ¥{_f(o['amount']):,.2f} (佣金 ¥{_f(o['commission']):.2f})"
                    )
                lines.append("")

            if not rejected.empty:
                lines.append("【拒绝订单】")
                for _, o in rejected.iterrows():
                    name = names.get(o["instrument"], "")
                    lines.append(
                        f"  {o['instrument']} {name}: "
                        f"{o['direction']} - {o['reject_reason']}"
                    )
                lines.append("")

        if not positions_df.empty:
            lines.append("【当前持仓】")
            for _, p in positions_df.iterrows():
                name = names.get(p["instrument"], p.get("name", ""))
                lines.append(
                    f"  {p['instrument']} {name}: "
                    f"{int(_f(p['shares']))}股 成本¥{_f(p['cost_price']):.2f} "
                    f"现价¥{_f(p['current_price']):.2f} "
                    f"盈亏{_f(p['profit_rate']) * 100:+.2f}% "
                    f"占比{_f(p['weight']) * 100:.1f}%"
                )

        return "\n".join(lines)

    def calculate_performance(self, start: str = None, end: str = None) -> dict:
        """Calculate comprehensive performance metrics."""
        df = self.recorder.get_account_summary(start=start, end=end)
        if df.empty or len(df) < 2:
            return {}

        returns = df["daily_return"].dropna()
        total_value = df["total_value"]
        n_days = len(returns)

        cumulative_return = (total_value.iloc[-1] - self.initial_cash) / self.initial_cash
        annual_factor = 252 / n_days if n_days > 0 else 0
        annualized_return = (1 + cumulative_return) ** annual_factor - 1 if annual_factor > 0 else 0

        peak = total_value.expanding().max()
        drawdown = (total_value - peak) / peak
        max_drawdown = drawdown.min()

        rf_daily = 0.03 / 252
        excess_daily = returns - rf_daily
        sharpe = (excess_daily.mean() / excess_daily.std() * np.sqrt(252)
                  if excess_daily.std() > 0 else 0)

        bench_returns = df["benchmark_return"].dropna()
        tracking_diff = returns - bench_returns
        information_ratio = (tracking_diff.mean() / tracking_diff.std() * np.sqrt(252)
                             if tracking_diff.std() > 0 else 0)

        win_days = (returns > 0).sum()
        total_days = (returns != 0).sum()
        win_rate = win_days / total_days if total_days > 0 else 0

        avg_win = returns[returns > 0].mean() if (returns > 0).any() else 0
        avg_loss = abs(returns[returns < 0].mean()) if (returns < 0).any() else 0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

        avg_turnover = df["turnover"].mean() if "turnover" in df else 0

        total_commission = 0
        orders_df = self.recorder.get_orders(start=start, end=end)
        if not orders_df.empty:
            total_commission = orders_df["commission"].sum()

        return {
            "start_date": df["date"].iloc[0],
            "end_date": df["date"].iloc[-1],
            "trading_days": n_days,
            "cumulative_return": cumulative_return,
            "annualized_return": annualized_return,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe,
            "information_ratio": information_ratio,
            "win_rate": win_rate,
            "profit_loss_ratio": profit_loss_ratio,
            "avg_daily_turnover": avg_turnover,
            "total_commission": total_commission,
            "final_value": total_value.iloc[-1],
            "benchmark_cumulative_return": df["benchmark_cumulative_return"].iloc[-1],
            "excess_return": df["excess_return"].iloc[-1],
        }

    def monthly_returns(self, start: str = None, end: str = None) -> list[dict]:
        """Calculate monthly returns for heatmap."""
        df = self.recorder.get_account_summary(start=start, end=end)
        if df.empty:
            return []

        df["date"] = pd.to_datetime(df["date"])
        df["year"] = df["date"].dt.year
        df["month"] = df["date"].dt.month

        monthly = []
        for (year, month), group in df.groupby(["year", "month"]):
            start_val = group["total_value"].iloc[0]
            end_val = group["total_value"].iloc[-1]
            prev_rows = df[df["date"] < group["date"].iloc[0]]
            base_val = prev_rows["total_value"].iloc[-1] if not prev_rows.empty else self.initial_cash
            ret = (end_val - base_val) / base_val
            monthly.append({"year": int(year), "month": int(month), "return": ret})

        return monthly
