"""每日快照估值纯函数（qlib 取价由调用方注入）。

设计文档 §4.3：
- 缺价股票按 avg_cost 保守估值（close_price/profit 记 None/0），缺价列表由
  调用方转成 PRICE_MISSING 告警；
- 日收益剔除当日外部出入金（external_flow：DEPOSIT/WITHDRAW 净额），
  分红派息计入收益；累计收益按日收益链式累乘，不受出入金扭曲。
"""

import math

# 会改变持仓/现金、计入换手的 LIVE 终态
_TRADED_STATUS = {"FILLED", "PARTIAL"}


def sum_live_fills_amount(fills: list) -> float:
    """当日 LIVE 终态成交额（买+卖绝对值之和），用于 turnover。"""
    total = 0.0
    for f in fills:
        if f.get("mode") == "LIVE" and f.get("status") in _TRADED_STATUS:
            total += abs((f.get("filled_qty") or 0) * (f.get("avg_price") or 0.0))
    return total


def build_snapshot(date, positions, cash, prices, bench_close,
                   prev_snapshot, fills_amount, external_flow=0.0,
                   fees=0.0, receivables=0.0, pending_shares=None,
                   tax_provision=0.0, account_value_adjustment=0.0):
    """构建每日快照。

    Args:
        date: 交易日 YYYY-MM-DD
        positions: {stock_code: {"shares": int, "avg_cost": float}}
        cash: 账本现金
        prices: {stock_code: 未复权收盘价}；缺失的股票按 avg_cost 估值
        bench_close: 基准指数收盘价，取不到传 None
        prev_snapshot: 前一交易日 daily_snapshot 行（dict）或 None
        fills_amount: 当日 LIVE 终态成交额
        external_flow: 当日外部出入金净额（正=入金），日收益计算时剔除
        fees: 当日已扣交易费用合计（透传入快照行，供日报展示）

    Returns:
        (daily_row, position_rows, missing_price_codes)
    """
    position_rows = []
    missing = []
    market_value = 0.0
    pending_market_value = 0.0

    for code in sorted(positions):
        p = positions[code]
        shares, avg_cost = p["shares"], p["avg_cost"]
        close = prices.get(code)
        if close is None:
            missing.append(code)
            mv = shares * avg_cost   # 保守估值
            profit = 0.0
        else:
            mv = shares * close
            profit = (close - avg_cost) * shares
        market_value += mv
        position_rows.append({
            "stock_code": code,
            "shares": shares,
            "avg_cost": avg_cost,
            "close_price": close,
            "market_value": mv,
            "profit": profit,
            "weight": None,  # 需 total_value，下面回填
        })

    for code, shares in sorted((pending_shares or {}).items()):
        close = prices.get(code)
        if close is None:
            if code not in missing:
                missing.append(code)
            continue
        pending_market_value += int(shares) * close

    total_value = (
        cash + market_value + float(receivables) + pending_market_value
        - float(tax_provision) + float(account_value_adjustment)
    )
    for row in position_rows:
        row["weight"] = (row["market_value"] / total_value) if total_value else None

    prev_total = prev_snapshot["total_value"] if prev_snapshot else None
    daily_return = (
        (total_value - external_flow) / prev_total - 1 if prev_total else None
    )
    # 累计收益按日收益链式累乘：出入金只改基数，不计入业绩
    if daily_return is None:
        cumulative_return = 0.0  # 本次即首个快照
    else:
        prev_cum = prev_snapshot.get("cumulative_return") or 0.0
        cumulative_return = (1 + prev_cum) * (1 + daily_return) - 1

    prev_bench = prev_snapshot.get("benchmark_close") if prev_snapshot else None
    bench_daily = (
        bench_close / prev_bench - 1
        if bench_close is not None and prev_bench else None
    )
    prev_bench_cum = (
        prev_snapshot.get("benchmark_cumulative_return") if prev_snapshot else None
    )
    if bench_close is None:
        bench_cum = None
    elif prev_bench_cum is None or bench_daily is None:
        bench_cum = 0.0  # 基准累计从首个有基准的快照起算
    else:
        bench_cum = (1 + prev_bench_cum) * (1 + bench_daily) - 1

    excess = (
        daily_return - bench_daily
        if daily_return is not None and bench_daily is not None else None
    )

    daily_row = {
        "date": date,
        "cash": cash,
        "market_value": market_value,
        "receivables": float(receivables),
        "pending_market_value": pending_market_value,
        "tax_provision": float(tax_provision),
        "account_value_adjustment": float(account_value_adjustment),
        "total_value": total_value,
        "daily_return": daily_return,
        "cumulative_return": cumulative_return,
        "benchmark_close": bench_close,
        "benchmark_daily_return": bench_daily,
        "benchmark_cumulative_return": bench_cum,
        "excess_return": excess,
        "position_count": len(position_rows),
        "turnover": (fills_amount / total_value) if total_value else None,
        "fees": fees,
        "external_flow": external_flow,
    }
    return daily_row, position_rows, missing


def value_live_book(positions, cash, prices):
    """按给定现价给当前账本标价。缺价的票 close/市值/盈亏为 None，不计入市值。

    总资产 = 现金 + 已标价股票市值（不含应收、待上市、红利税、账户调整）。
    """
    rows = []
    market_value = 0.0
    cash = float(cash)
    for code in sorted(positions):
        p = positions[code]
        shares = int(p["shares"])
        avg_cost = float(p["avg_cost"])
        close = prices.get(code)
        if close is None:
            mv = None
            profit = None
        else:
            close = float(close)
            mv = shares * close
            profit = (close - avg_cost) * shares
            market_value += mv
        rows.append({
            "stock_code": code,
            "shares": shares,
            "avg_cost": avg_cost,
            "close_price": close,
            "market_value": mv,
            "profit": profit,
            "weight": None,
        })
    total_value = cash + market_value
    for row in rows:
        if row["market_value"] is not None and total_value:
            row["weight"] = row["market_value"] / total_value
    return {
        "positions": rows,
        "cash": cash,
        "market_value": market_value,
        "total_value": total_value,
        "cash_weight": (cash / total_value) if total_value else None,
    }


def apply_benchmark_closes(snapshots, closes_by_date):
    """用指数收盘价回填快照的基准收益字段；缺价日保持 None。"""
    out = []
    prev_close = None
    prev_cum = None
    for row in sorted(snapshots, key=lambda r: r["date"]):
        row = dict(row)
        close = closes_by_date.get(row["date"])
        if close is None:
            row["benchmark_close"] = None
            row["benchmark_daily_return"] = None
            row["benchmark_cumulative_return"] = None
            row["excess_return"] = None
        else:
            close = float(close)
            daily = (close / prev_close - 1.0) if prev_close else None
            if prev_cum is None or daily is None:
                cum = 0.0
            else:
                cum = (1.0 + prev_cum) * (1.0 + daily) - 1.0
            row["benchmark_close"] = close
            row["benchmark_daily_return"] = daily
            row["benchmark_cumulative_return"] = cum
            dr = row.get("daily_return")
            row["excess_return"] = (
                dr - daily if dr is not None and daily is not None else None
            )
            prev_close = close
            prev_cum = cum
        out.append(row)
    return out


# 与 EXPERIMENT_STANDARD 执行层夏普一致：算术年化 / (日收益标准差 ×√250)
_SHARPE_DAYS = 250


def compute_performance_metrics(snapshots):
    """由日快照序列计算净值、夏普、最大回撤。

    净值从 1.0 起算：``1 + cumulative_return``（出入金已从日收益剔除）。
    夏普：``mean(r) / std(r, ddof=1) * sqrt(250)``；日收益不足 2 个则为 None。
    最大回撤：净值相对历史峰值的最大跌幅（``min(nav / peak - 1)``）。
    """
    if not snapshots:
        return {"nav": None, "sharpe": None, "max_drawdown": None,
                "n_returns": 0}

    rows = sorted(snapshots, key=lambda r: r["date"])
    latest = rows[-1]
    cum = latest.get("cumulative_return")
    nav = None if cum is None else 1.0 + float(cum)

    returns = [
        float(r["daily_return"])
        for r in rows if r.get("daily_return") is not None
    ]
    sharpe = None
    if len(returns) >= 2:
        n = len(returns)
        mean = sum(returns) / n
        var = sum((x - mean) ** 2 for x in returns) / (n - 1)
        std = math.sqrt(var)
        if std > 0:
            sharpe = mean / std * math.sqrt(_SHARPE_DAYS)

    max_dd = None
    navs = [
        1.0 + float(r["cumulative_return"])
        for r in rows if r.get("cumulative_return") is not None
    ]
    if navs:
        peak = navs[0]
        max_dd = 0.0
        for value in navs:
            if value > peak:
                peak = value
            if peak > 0:
                dd = value / peak - 1.0
                if dd < max_dd:
                    max_dd = dd

    return {
        "nav": nav, "sharpe": sharpe, "max_drawdown": max_dd,
        "n_returns": len(returns),
    }
