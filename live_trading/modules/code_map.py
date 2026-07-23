"""qlib ↔ QMT 股票代码双向转换。

qlib instrument: ``SH600000`` / ``SZ000001`` / ``BJ835185``
QMT stock_code:  ``600000.SH`` / ``000001.SZ`` / ``835185.BJ``
"""

VALID_MARKETS = {"SH", "SZ", "BJ"}


def qlib_to_qmt(code: str) -> str:
    """``SH600000`` -> ``600000.SH``。非法输入抛 ValueError。"""
    if not isinstance(code, str) or len(code) != 8:
        raise ValueError(f"invalid qlib instrument: {code!r}")
    market, symbol = code[:2], code[2:]
    if market not in VALID_MARKETS:
        raise ValueError(f"unknown market in qlib instrument: {code!r}")
    if not symbol.isdigit():
        raise ValueError(f"symbol must be 6 digits: {code!r}")
    return f"{symbol}.{market}"


def qmt_to_qlib(code: str) -> str:
    """``600000.SH`` -> ``SH600000``。非法输入抛 ValueError。"""
    if not isinstance(code, str) or "." not in code:
        raise ValueError(f"invalid qmt stock_code: {code!r}")
    symbol, _, market = code.partition(".")
    if market not in VALID_MARKETS:
        raise ValueError(f"unknown market in qmt stock_code: {code!r}")
    if len(symbol) != 6 or not symbol.isdigit():
        raise ValueError(f"symbol must be 6 digits: {code!r}")
    return f"{market}{symbol}"
