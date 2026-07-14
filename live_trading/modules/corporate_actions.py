"""Tushare corporate-action event normalization.

Entitlement and settlement are handled transactionally by ``LiveRecorder``.
"""

import os


def _date(value) -> str:
    if value is None or value != value:
        return ""
    raw = str(value).replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        return ""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def _number(value) -> float:
    if value is None or value != value:
        return 0.0
    return float(value)


def fetch_dividend_events(date: str, pro=None) -> list:
    """Return implemented dividend events whose ex-date is ``date``."""
    if pro is None:
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError("TUSHARE_TOKEN 未设置，无法查询分红事件")
        import tushare as ts
        pro = ts.pro_api(token)
    df = pro.dividend(
        ex_date=date.replace("-", ""),
        fields=("ts_code,div_proc,stk_div,cash_div_tax,record_date,ex_date,"
                "pay_date,div_listdate,end_date"),
    )
    events = []
    for _, row in df.iterrows():
        if row["div_proc"] != "实施":
            continue
        stock_code = row["ts_code"]
        record_date = _date(row.get("record_date"))
        ex_date = _date(row.get("ex_date")) or date
        # Missing settlement dates must stay unknown. Falling back to ex-date
        # would make unreceived cash or unlisted shares tradable too early.
        pay_date = _date(row.get("pay_date"))
        div_listdate = _date(row.get("div_listdate"))
        end_date = _date(row.get("end_date"))
        event_key = "_".join(
            [stock_code, end_date.replace("-", ""),
             record_date.replace("-", ""), ex_date.replace("-", "")]
        )
        events.append({
            "event_key": event_key,
            "stock_code": stock_code,
            "end_date": end_date,
            "record_date": record_date,
            "ex_date": ex_date,
            "pay_date": pay_date,
            "div_listdate": div_listdate,
            "cash_div_tax": _number(row.get("cash_div_tax")),
            "stk_div": _number(row.get("stk_div")),
        })
    return events
