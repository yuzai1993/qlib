"""A 股交易费用计算（纯函数）。

现行规则（2026，来源见设计文档 §9 费用与公司行为）：
- 佣金：双向，费率与券商协商（已含经手费/证管费），单笔最低 5 元
- 印花税：仅卖出，0.05%（2023-08-28 起减半征收）
- 过户费：买卖双向，成交金额的 0.001%（2022-04-29 起）
- 红利税：派发时暂不预扣，卖出时按持股期限补扣（≤1月 20%，1月~1年 10%，
  >1年 免）。本系统在派息入账时按 dividend_tax_rate 预提估算。
"""

DEFAULT_FEES = {
    "commission_rate": 0.00025,    # 万2.5，请按开户实际费率修改
    "min_commission": 5.0,         # 单笔最低佣金（元）
    "stamp_duty_rate": 0.0005,     # 印花税，卖出单边
    "transfer_fee_rate": 0.00001,  # 过户费，双向
    "dividend_tax_rate": 0.20,     # 红利税预提税率（短持仓策略按 20% 估）
}


def fees_from_config(config: dict) -> dict:
    """从 live 配置取 fees 段，与默认值合并。"""
    merged = dict(DEFAULT_FEES)
    merged.update((config or {}).get("fees") or {})
    return merged


def order_total_fee(side: str, cum_amount: float, fees: dict) -> float:
    """订单累计成交额对应的应计费用总额（佣金+过户费+卖出印花税）。

    按订单整体计费：最低佣金对整个订单只收一次，部分成交多次回执时
    调用方用「本次总应计 - 已计费用」得到增量，天然幂等。
    """
    if cum_amount <= 0:
        return 0.0
    commission = max(cum_amount * fees["commission_rate"], fees["min_commission"])
    transfer = cum_amount * fees["transfer_fee_rate"]
    stamp = cum_amount * fees["stamp_duty_rate"] if side == "SELL" else 0.0
    return commission + transfer + stamp
