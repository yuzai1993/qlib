#!/usr/bin/env python3
"""Paper trading system - main CLI entry point."""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("paper_trading")

os.environ["TUSHARE_TOKEN"] = "e7afca7966f571c3a526d94543b99198ccc06539325a065d03377a93"


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def setup_logging(config: dict, date_str: str = None):
    log_dir = PROJECT_ROOT / config["storage"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)

    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    log_file = log_dir / f"{date_str}.log"

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(log_file), encoding="utf-8"),
    ]

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        handlers=handlers,
    )


def init_qlib(config: dict):
    import qlib
    from qlib.constant import REG_CN

    provider_uri = config["data"]["qlib_dir"]
    qlib.init(provider_uri=provider_uri, region=REG_CN)
    logger.info("qlib initialized, data_path=%s", provider_uri)


def get_trading_calendar() -> list[str]:
    from qlib.data import D
    cal = D.calendar(start_time="2020-01-01", end_time="2030-12-31")
    return [d.strftime("%Y-%m-%d") for d in cal]


def is_trading_day(date_str: str, calendar: list[str]) -> bool:
    return date_str in calendar


def get_prev_trading_day(date_str: str, calendar: list[str]) -> str:
    idx = None
    for i, d in enumerate(calendar):
        if d == date_str:
            idx = i
            break
    if idx is not None and idx > 0:
        return calendar[idx - 1]
    return None


def fetch_stock_names(recorder):
    """Fetch stock names from Tushare and save to DB."""
    try:
        import tushare as ts
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            logger.warning("TUSHARE_TOKEN not set, skipping stock names update")
            return
        pro = ts.pro_api(token)
        df = pro.stock_basic(exchange='', list_status='L',
                             fields='ts_code,symbol,name')
        if df.empty:
            logger.warning("No stock names returned from tushare")
            return

        def ts_code_to_instrument(ts_code: str) -> str:
            code, market = ts_code.split(".")
            return market + code

        df["instrument"] = df["ts_code"].apply(ts_code_to_instrument)
        recorder.save_stock_names(df[["instrument", "ts_code", "name"]])
        logger.info("Stock names updated: %d records", len(df))
    except ImportError:
        logger.warning("tushare not installed, skipping stock names")
    except Exception as e:
        logger.error("Failed to fetch stock names: %s", e)


def check_data_ready(date_str: str, config: dict) -> bool:
    """Check if data for the given date is available in qlib."""
    from qlib.data import D
    try:
        cal = D.calendar(start_time=date_str, end_time=date_str)
        return len(cal) > 0
    except Exception:
        return False


# ==================== Commands ====================

def cmd_init(args, config):
    """Initialize paper trading: create DB, load model, first prediction."""
    from modules.recorder import Recorder
    from modules.signal_generator import SignalGenerator
    from modules.account_manager import AccountManager

    init_qlib(config)

    db_path = PROJECT_ROOT / config["storage"]["db_path"]
    recorder = Recorder(str(db_path))
    logger.info("Database initialized at %s", db_path)

    fetch_stock_names(recorder)

    start_date = config["paper_trading"]["start_date"]
    initial_cash = config["paper_trading"]["initial_cash"]

    recorder.save_account_summary({
        "date": "init",
        "cash": initial_cash,
        "total_value": initial_cash,
        "market_value": 0,
        "daily_return": 0,
        "cumulative_return": 0,
        "benchmark_return": 0,
        "benchmark_cumulative_return": 0,
        "excess_return": 0,
        "position_count": 0,
        "turnover": 0,
    })

    calendar = get_trading_calendar()
    pred_date = get_prev_trading_day(start_date, calendar)
    if pred_date is None:
        pred_date = start_date

    logger.info("Running initial prediction for date %s (to be used on %s)",
                pred_date, start_date)

    signal_gen = SignalGenerator(config, PROJECT_ROOT)
    signal_gen.load_model()
    scores = signal_gen.predict(pred_date)
    recorder.save_predictions(pred_date, scores)

    recorder.save_system_log("INFO", "init",
                             f"Paper trading initialized. Cash={initial_cash}, "
                             f"start_date={start_date}, prediction for {pred_date}")
    logger.info("Initialization complete. System ready for trading from %s", start_date)


def cmd_daily(args, config):
    """Execute daily routine: trade with T-1 signal, then predict for T+1."""
    init_qlib(config)

    today = datetime.now().strftime("%Y-%m-%d")
    calendar = get_trading_calendar()

    if not is_trading_day(today, calendar):
        logger.info("%s is not a trading day, skipping", today)
        return

    _run_single_day(today, config, calendar)


def cmd_run(args, config):
    """Run for a specific date or date range."""
    init_qlib(config)
    calendar = get_trading_calendar()

    if args.date:
        dates = [args.date]
    elif args.start:
        end = args.end or datetime.now().strftime("%Y-%m-%d")
        dates = [d for d in calendar if args.start <= d <= end]
    else:
        logger.error("Must specify --date or --start")
        return

    for date_str in dates:
        if not is_trading_day(date_str, calendar):
            logger.info("%s is not a trading day, skipping", date_str)
            continue
        _run_single_day(date_str, config, calendar)


def _run_single_day(date_str: str, config: dict, calendar: list[str]):
    """Core daily routine for a single trading day."""
    from modules.recorder import Recorder
    from modules.signal_generator import SignalGenerator
    from modules.execution_engine import ExecutionEngine
    from modules.account_manager import AccountManager
    from modules.order_manager import OrderManager
    from modules.reporter import Reporter
    from modules.alert import AlertManager

    db_path = PROJECT_ROOT / config["storage"]["db_path"]
    recorder = Recorder(str(db_path))

    if recorder.has_date_executed(date_str):
        logger.info("%s already executed, skipping (idempotent)", date_str)
        return

    if not check_data_ready(date_str, config):
        logger.error("Data not ready for %s, aborting", date_str)
        recorder.save_system_log("ERROR", "daily", f"Data not ready for {date_str}")
        return

    maybe_update_stock_names(recorder, config)

    logger.info("===== Processing %s =====", date_str)

    signal_gen = SignalGenerator(config, PROJECT_ROOT)
    exec_engine = ExecutionEngine(config)
    account = AccountManager(config, recorder)
    order_mgr = OrderManager(config)
    reporter = Reporter(recorder, config["paper_trading"]["initial_cash"])
    alert_mgr = AlertManager(config, recorder)

    # --- Phase 1: Execute trades using T-1 signal ---
    prev_day = get_prev_trading_day(date_str, calendar)
    account.load_state()

    scores = recorder.get_latest_prediction_scores()
    if scores is None:
        logger.warning("No prediction signals found, skipping trading for %s", date_str)
    else:
        instruments = list(scores.index) + list(account.positions.keys())
        instruments = list(set(instruments))
        close_prices = exec_engine.get_all_close_prices(date_str, instruments)

        total_value = account.cash + sum(
            close_prices.get(inst, pos.get("current_price", pos["cost_price"])) * pos["shares"]
            for inst, pos in account.positions.items()
        )

        orders = order_mgr.generate_orders(
            scores, account.positions, account.cash, close_prices, total_value,
        )

        executed = exec_engine.execute_orders(date_str, orders)
        recorder.save_orders(executed)

        account.apply_orders(executed)
        account.update_market_values(close_prices)

        buy_orders = [o for o in executed if o["direction"] == "BUY" and o["status"] in ("FILLED", "PARTIAL")]
        sell_orders = [o for o in executed if o["direction"] == "SELL" and o["status"] in ("FILLED", "PARTIAL")]

        recorder.save_trade_summary({
            "date": date_str,
            "buy_count": len(buy_orders),
            "sell_count": len(sell_orders),
            "buy_amount": sum(o["amount"] for o in buy_orders),
            "sell_amount": sum(o["amount"] for o in sell_orders),
            "total_commission": sum(o["commission"] for o in executed if o["status"] in ("FILLED", "PARTIAL")),
            "net_inflow": sum(o["amount"] for o in sell_orders) - sum(o["amount"] for o in buy_orders),
        })

    summary = account.calculate_summary(date_str)
    recorder.save_account_summary(summary)
    recorder.save_positions(
        date_str,
        account.get_position_details(date_str, summary["total_value"]),
    )

    # --- Phase 2: Generate prediction for T+1 ---
    logger.info("Generating prediction signal for future use...")
    try:
        signal_gen.load_model()
        new_scores = signal_gen.predict(date_str)
        recorder.save_predictions(date_str, new_scores)
        logger.info("Prediction saved for %s (%d instruments)", date_str, len(new_scores))
    except Exception as e:
        logger.error("Failed to generate prediction: %s", e)
        recorder.save_system_log("ERROR", "prediction", f"Prediction failed for {date_str}: {e}")

    # --- Phase 3: Post-trade processing ---
    alerts = alert_mgr.check_alerts(date_str, summary)
    daily_text = reporter.daily_summary_text(date_str)
    logger.info("\n%s", daily_text)
    recorder.save_system_log("INFO", "daily", f"Daily routine completed for {date_str}")


def maybe_update_stock_names(recorder, config):
    """Update stock names if stale."""
    from datetime import datetime, timedelta
    interval = config.get("stock_names", {}).get("update_interval_days", 7)
    last_update = recorder.get_stock_names_updated_at()
    if last_update:
        last_dt = datetime.fromisoformat(last_update)
        if datetime.now() - last_dt < timedelta(days=interval):
            return
    fetch_stock_names(recorder)


def cmd_status(args, config):
    """Show current account status."""
    from modules.recorder import Recorder
    from modules.reporter import Reporter

    db_path = PROJECT_ROOT / config["storage"]["db_path"]
    recorder = Recorder(str(db_path))

    summary = recorder.get_latest_account_summary()
    if not summary:
        print("No trading data found. Run 'init' first.")
        return

    names = recorder.get_stock_names()
    print(f"\n===== 模拟盘状态 (截至 {summary['date']}) =====")
    print(f"账户总资产: ¥{summary['total_value']:,.2f}")
    print(f"现金余额:   ¥{summary['cash']:,.2f}")
    print(f"持仓市值:   ¥{summary['market_value']:,.2f}")
    print(f"累计收益率:  {summary['cumulative_return'] * 100:+.2f}%")
    print(f"超额收益:    {summary['excess_return'] * 100:+.2f}%")
    print(f"持仓数量:    {int(summary['position_count'])}")

    positions = recorder.get_positions()
    if not positions.empty:
        print(f"\n{'代码':<12} {'名称':<10} {'股数':>8} {'成本价':>10} "
              f"{'现价':>10} {'盈亏%':>8} {'占比%':>8}")
        print("-" * 76)
        for _, p in positions.iterrows():
            name = names.get(p["instrument"], p.get("name", ""))
            print(f"{p['instrument']:<12} {name:<10} {int(p['shares']):>8} "
                  f"{p['cost_price']:>10.2f} {p['current_price']:>10.2f} "
                  f"{p['profit_rate']*100:>+7.2f}% {p['weight']*100:>7.1f}%")


def cmd_positions(args, config):
    """Show positions for a specific date."""
    from modules.recorder import Recorder

    db_path = PROJECT_ROOT / config["storage"]["db_path"]
    recorder = Recorder(str(db_path))
    names = recorder.get_stock_names()

    positions = recorder.get_positions(args.date)
    if positions.empty:
        print(f"No positions found for {args.date or 'latest'}")
        return

    date_show = args.date or positions.iloc[0]["date"]
    print(f"\n===== 持仓明细 ({date_show}) =====")
    print(f"{'代码':<12} {'名称':<10} {'股数':>8} {'成本价':>10} "
          f"{'现价':>10} {'盈亏额':>12} {'盈亏%':>8} {'占比%':>8} {'持有天数':>6}")
    print("-" * 100)
    for _, p in positions.iterrows():
        name = names.get(p["instrument"], p.get("name", ""))
        print(f"{p['instrument']:<12} {name:<10} {int(p['shares']):>8} "
              f"{p['cost_price']:>10.2f} {p['current_price']:>10.2f} "
              f"{p['profit']:>12,.2f} {p['profit_rate']*100:>+7.2f}% "
              f"{p['weight']*100:>7.1f}% {int(p['holding_days']):>6}")


def cmd_orders(args, config):
    """Show order history."""
    from modules.recorder import Recorder

    db_path = PROJECT_ROOT / config["storage"]["db_path"]
    recorder = Recorder(str(db_path))
    names = recorder.get_stock_names()

    orders = recorder.get_orders(start=args.start, end=args.end)
    if orders.empty:
        print("No orders found.")
        return

    print(f"\n===== 交易记录 =====")
    print(f"{'日期':<12} {'代码':<12} {'名称':<10} {'方向':<6} "
          f"{'成交股数':>8} {'成交价':>10} {'成交额':>12} {'佣金':>8} {'状态':<10}")
    print("-" * 110)
    for _, o in orders.iterrows():
        name = names.get(o["instrument"], o.get("name", ""))
        print(f"{o['date']:<12} {o['instrument']:<12} {name:<10} {o['direction']:<6} "
              f"{int(o.get('filled_shares', 0)):>8} {o.get('price', 0):>10.2f} "
              f"{o.get('amount', 0):>12,.2f} {o.get('commission', 0):>8.2f} {o['status']:<10}")


def cmd_report(args, config):
    """Generate performance report."""
    from modules.recorder import Recorder
    from modules.reporter import Reporter

    init_qlib(config)
    db_path = PROJECT_ROOT / config["storage"]["db_path"]
    recorder = Recorder(str(db_path))
    reporter = Reporter(recorder, config["paper_trading"]["initial_cash"])

    perf = reporter.calculate_performance(start=args.start)
    if not perf:
        print("Not enough data for report.")
        return

    print(f"\n===== 绩效报告 ({perf['start_date']} ~ {perf['end_date']}) =====")
    print(f"交易天数:       {perf['trading_days']}")
    print(f"最终资产:       ¥{perf['final_value']:,.2f}")
    print(f"累计收益率:     {perf['cumulative_return']*100:+.2f}%")
    print(f"年化收益率:     {perf['annualized_return']*100:+.2f}%")
    print(f"最大回撤:       {perf['max_drawdown']*100:.2f}%")
    print(f"夏普比率:       {perf['sharpe_ratio']:.3f}")
    print(f"信息比率:       {perf['information_ratio']:.3f}")
    print(f"胜率:           {perf['win_rate']*100:.1f}%")
    print(f"盈亏比:         {perf['profit_loss_ratio']:.2f}")
    print(f"平均日换手率:   {perf['avg_daily_turnover']*100:.2f}%")
    print(f"累计手续费:     ¥{perf['total_commission']:,.2f}")
    print(f"基准累计收益:   {perf['benchmark_cumulative_return']*100:+.2f}%")
    print(f"超额收益:       {perf['excess_return']*100:+.2f}%")


def cmd_export(args, config):
    """Export a table to CSV."""
    from modules.recorder import Recorder

    db_path = PROJECT_ROOT / config["storage"]["db_path"]
    recorder = Recorder(str(db_path))
    recorder.export_table(args.table, args.output)
    print(f"Exported {args.table} to {args.output}")


def cmd_web(args, config):
    """Start the web dashboard."""
    from web.app import create_app
    import uvicorn

    port = args.port or config.get("web", {}).get("port", 8000)
    host = config.get("web", {}).get("host", "0.0.0.0")

    app = create_app(config, PROJECT_ROOT)
    logger.info("Starting web dashboard on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)


def main():
    parser = argparse.ArgumentParser(description="Paper Trading System")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("init", help="Initialize paper trading system")

    subparsers.add_parser("daily", help="Run daily routine")

    run_parser = subparsers.add_parser("run", help="Run for specific date(s)")
    run_parser.add_argument("--date", help="Single date (YYYY-MM-DD)")
    run_parser.add_argument("--start", help="Start date for range")
    run_parser.add_argument("--end", help="End date for range")

    subparsers.add_parser("status", help="Show account status")

    pos_parser = subparsers.add_parser("positions", help="Show positions")
    pos_parser.add_argument("--date", help="Date (YYYY-MM-DD)")

    ord_parser = subparsers.add_parser("orders", help="Show order history")
    ord_parser.add_argument("--start", help="Start date")
    ord_parser.add_argument("--end", help="End date")

    rpt_parser = subparsers.add_parser("report", help="Generate performance report")
    rpt_parser.add_argument("--start", help="Start date")

    exp_parser = subparsers.add_parser("export", help="Export table to CSV")
    exp_parser.add_argument("--table", required=True, help="Table name")
    exp_parser.add_argument("--output", required=True, help="Output CSV path")

    web_parser = subparsers.add_parser("web", help="Start web dashboard")
    web_parser.add_argument("--port", type=int, help="Port number")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    config = load_config()

    date_str = None
    if hasattr(args, "date") and args.date:
        date_str = args.date
    setup_logging(config, date_str)

    commands = {
        "init": cmd_init,
        "daily": cmd_daily,
        "run": cmd_run,
        "status": cmd_status,
        "positions": cmd_positions,
        "orders": cmd_orders,
        "report": cmd_report,
        "export": cmd_export,
        "web": cmd_web,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        try:
            cmd_func(args, config)
        except Exception as e:
            logger.exception("Command '%s' failed: %s", args.command, e)
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
