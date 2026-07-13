#!/usr/bin/env python3
"""手工记录资金流水（出入金 / 校正 / 补录分红），并同步调整账本现金。

用法：
    # 入金 50 万
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live \\
        --type DEPOSIT --amount 500000 --note "追加资金"

    # 出金用负数
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live \\
        --type WITHDRAW --amount -100000

    # 与券商对账后的现金校正（差额，正负均可）
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live \\
        --type CORRECTION --amount -12.35 --note "对账差额：红利税实扣"

    # 手工补录分红（自动处理漏掉时）
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live \\
        --type DIVIDEND --amount 380.0 --stock-code 600036.SH --note "招商银行派息"

    # 查看最近流水
    python live_trading/scripts/record_cash_flow.py --config csi300_topk10_live --list

流水类型：
    DEPOSIT / WITHDRAW / CORRECTION —— 外部出入金，快照日收益会剔除其影响
    DIVIDEND / DIVIDEND_TAX / BONUS_SHARES —— 公司行为，计入投资收益
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

FLOW_TYPES = ["DEPOSIT", "WITHDRAW", "CORRECTION",
              "DIVIDEND", "DIVIDEND_TAX", "BONUS_SHARES"]


def main():
    p = argparse.ArgumentParser(description="Record manual cash flow")
    p.add_argument("--config", required=True, help="live config id (configs/*.yaml)")
    p.add_argument("--type", dest="flow_type", choices=FLOW_TYPES)
    p.add_argument("--amount", type=float, help="正数入金，负数出金")
    p.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认今天）")
    p.add_argument("--stock-code", default=None, help="关联股票（QMT 格式，可选）")
    p.add_argument("--note", default="", help="备注")
    p.add_argument("--list", action="store_true", help="仅列出最近流水")
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

    if not args.flow_type or args.amount is None:
        p.error("--type 与 --amount 必填（或使用 --list）")

    trade_date = args.date or _date.today().strftime("%Y-%m-%d")
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
