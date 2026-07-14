#!/usr/bin/env python3
"""手工记录资金流水或结算券商实际红利税。

用法：
    # 入金 50 万
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live \\
        --type DEPOSIT --amount 500000 --note "追加资金"

    # 出金用负数
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live \\
        --type WITHDRAW --amount -100000

    # 与券商对账后的现金校正（计入当日投资损益，必须写原因）
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live \\
        --type CORRECTION --amount -12.35 --note "成交/费用对账差额"

    # 券商卖出时实扣红利税（正数金额，结算对应准备金）
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live \
        --type DIVIDEND_TAX_SETTLEMENT --event-key <event_key> --amount 50

    # 手工补录分红（自动处理漏掉时）
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live \\
        --type DIVIDEND --amount 380.0 --stock-code 600036.SH --note "招商银行派息"

    # 查看最近流水
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live --list

    # 查看分红事件及 event_key
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live --list-events

流水类型：
    DEPOSIT / WITHDRAW —— 外部出入金，快照日收益会剔除其影响
    CORRECTION / DIVIDEND —— 投资相关现金变化，计入投资收益
    DIVIDEND_TAX_SETTLEMENT —— 券商实际扣税并释放对应红利税准备
"""

import argparse
import sys
from datetime import date as _date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.live_config import load_live_config

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"

FLOW_TYPES = [
    "DEPOSIT", "WITHDRAW", "CORRECTION", "DIVIDEND",
    "DIVIDEND_TAX_SETTLEMENT",
]


def main():
    p = argparse.ArgumentParser(description="Record manual cash flow")
    p.add_argument("--config", required=True, help="live config id (configs/*.yaml)")
    p.add_argument("--type", dest="flow_type", choices=FLOW_TYPES)
    p.add_argument("--amount", type=float, help="正数入金，负数出金")
    p.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认今天）")
    p.add_argument("--stock-code", default=None, help="关联股票（QMT 格式，可选）")
    p.add_argument("--event-key", default=None,
                   help="红利税结算对应 corporate action event_key")
    p.add_argument("--note", default="", help="备注")
    p.add_argument("--list", action="store_true", help="仅列出最近流水")
    p.add_argument("--list-events", action="store_true", help="列出分红事件与结算状态")
    args = p.parse_args()

    config = load_live_config(CONFIGS_DIR / f"{args.config}.yaml", PROJECT_ROOT)
    recorder = LiveRecorder(str(PROJECT_ROOT / config["storage"]["db_path"]))

    if args.list:
        rows = recorder.get_cash_flows(limit=30)
        if not rows:
            print("暂无资金流水")
        for r in rows:
            print(f"{r['trade_date']}  {r['flow_type']:<13} "
                  f"{r['amount']:>14,.2f}  {r['stock_code'] or '':<10} {r['note'] or ''}")
        print(f"\n当前现金: {recorder.get_cash():,.2f}")
        return 0

    if args.list_events:
        rows = recorder.get_corporate_actions(limit=100)
        if not rows:
            print("暂无分红事件")
        for row in rows:
            print(
                f"{row['event_key']}  {row['stock_code']}  "
                f"登记 {row['record_date']} 除息 {row['ex_date']} "
                f"派息 {row['pay_date']} 上市 {row['div_listdate']}  "
                f"应收 {row['gross_cash']:,.2f} 准备 {row['tax_provision']:,.2f}  "
                f"cash={row['cash_settled']} bonus={row['bonus_settled']} "
                f"tax={row['tax_settled']}"
            )
        return 0

    if not args.flow_type or args.amount is None:
        p.error("--type 与 --amount 必填（或使用 --list）")

    trade_date = args.date or _date.today().strftime("%Y-%m-%d")
    if args.flow_type == "DIVIDEND_TAX_SETTLEMENT":
        if not args.event_key:
            p.error("DIVIDEND_TAX_SETTLEMENT 必须提供 --event-key")
        ok = recorder.settle_dividend_tax(
            args.event_key, trade_date, actual_tax=args.amount,
        )
        print(f"{'已结算' if ok else '已结算过，跳过'}: {trade_date} "
              f"DIVIDEND_TAX {args.amount:,.2f}")
        print(f"当前现金: {recorder.get_cash():,.2f}")
        return 0

    ok = recorder.record_cash_flow(
        trade_date, args.flow_type, args.amount,
        stock_code=args.stock_code, note=args.note,
    )
    print(f"{'已入账' if ok else '重复流水，已跳过'}: {trade_date} {args.flow_type} "
          f"{args.amount:+,.2f}")
    print(f"当前现金: {recorder.get_cash():,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
