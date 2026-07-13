"""公司行为（分红/送股/转增）自动入账。

规则（2026 现行）：
- 除息日（ex_date）股价除权除息，现金红利与送转股按当日入账，账户净值连续；
  实际到账日（pay_date）通常晚 0~3 天，差异忽略。
- 红利税差别化征收（财税〔2015〕101号）：持股 ≤1 月 20%、1月~1年 10%、
  >1 年免税；派发时不预扣，卖出时由券商按先进先出补扣。
  本策略平均持有期短，入账时按 dividend_tax_rate（默认 20%）预提估算；
  与券商实扣的差额用 CORRECTION 流水校正。
- 数据源 tushare ``dividend`` 接口（div_proc=实施），ts_code 与 QMT 代码同格式。
"""

import logging
import os

logger = logging.getLogger("live_trading.corporate_actions")


def fetch_dividend_events(date: str, stock_codes: list) -> list:
    """取除息日为 date、且在持仓列表内的实施分红事件。

    Returns:
        [{stock_code, cash_div_tax(每股税前), stk_div(每股送转)}, ...]
        TUSHARE_TOKEN 未设置或接口异常时抛 RuntimeError。
    """
    if not stock_codes:
        return []
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未设置，无法查询分红事件")
    import tushare as ts
    pro = ts.pro_api(token)
    df = pro.dividend(
        ex_date=date.replace("-", ""),
        fields="ts_code,div_proc,stk_div,cash_div_tax",
    )
    held = set(stock_codes)
    events = []
    for _, row in df.iterrows():
        if row["ts_code"] not in held or row["div_proc"] != "实施":
            continue
        events.append({
            "stock_code": row["ts_code"],
            "cash_div_tax": float(row["cash_div_tax"] or 0.0),
            "stk_div": float(row["stk_div"] or 0.0),
        })
    return events


def apply_corporate_actions(recorder, date: str, events: list,
                            dividend_tax_rate: float = 0.20) -> list:
    """将分红事件入账（幂等，dedup_key 去重）。

    Args:
        recorder: LiveRecorder
        events: fetch_dividend_events 的返回
        dividend_tax_rate: 红利税预提税率

    Returns:
        实际入账的描述列表（重复调用返回空）。
    """
    applied = []
    positions = recorder.get_positions()
    for ev in events:
        code = ev["stock_code"]
        pos = positions.get(code)
        if not pos:
            continue
        shares = pos["shares"]

        gross = round(shares * ev["cash_div_tax"], 2)
        if gross > 0:
            if recorder.record_cash_flow(
                    date, "DIVIDEND", gross, stock_code=code,
                    note=f"每股派 {ev['cash_div_tax']:.4f} x {shares} 股（税前）",
                    dedup_key=f"DIV_{code}_{date}"):
                applied.append(f"DIVIDEND {code} +{gross:.2f}")
            tax = round(gross * dividend_tax_rate, 2)
            if tax > 0 and recorder.record_cash_flow(
                    date, "DIVIDEND_TAX", -tax, stock_code=code,
                    note=f"红利税预提 {dividend_tax_rate*100:.0f}%（券商卖出时实扣，差额用 CORRECTION 校正）",
                    dedup_key=f"DIVTAX_{code}_{date}"):
                applied.append(f"DIVIDEND_TAX {code} -{tax:.2f}")

        bonus = int(shares * ev["stk_div"])
        if bonus > 0:
            if recorder.record_cash_flow(
                    date, "BONUS_SHARES", 0.0, stock_code=code,
                    note=f"送转 {ev['stk_div']:.4f}/股 到账 {bonus} 股",
                    dedup_key=f"BONUS_{code}_{date}"):
                recorder.apply_bonus_shares(code, bonus)
                applied.append(f"BONUS_SHARES {code} +{bonus}股")

    for msg in applied:
        logger.info("corporate action applied: %s", msg)
    return applied
