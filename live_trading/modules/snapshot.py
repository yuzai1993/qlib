"""每日快照估值纯函数（qlib 取价由调用方注入）。

设计文档 §4.3：
- 缺价股票按 avg_cost 保守估值（close_price/profit 记 None/0），缺价列表由
  调用方转成 PRICE_MISSING 告警；
- 日收益剔除当日外部出入金（external_flow：DEPOSIT/WITHDRAW/CORRECTION 净额），
  分红派息计入收益；累计收益按日收益链式累乘，不受出入金扭曲。
"""

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
                   fees=0.0):
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

    total_value = cash + market_value
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
