import pandas as pd

from live_trading.modules.stock_names import fetch_stock_names


class FakePro:
    def __init__(self):
        self.statuses = []

    def stock_basic(self, exchange, list_status, fields):
        self.statuses.append(list_status)
        if list_status == "L":
            return pd.DataFrame([
                {"ts_code": "600000.SH", "name": "浦发银行"},
                {"ts_code": "000001.SZ", "name": "平安银行"},
                {"ts_code": "INVALID", "name": "忽略"},
            ])
        return pd.DataFrame(columns=["ts_code", "name"])


def test_fetch_stock_names_uses_live_identifiers_without_paper_db():
    pro = FakePro()

    rows = fetch_stock_names(pro)

    assert pro.statuses == ["L", "D", "P"]
    assert rows == [
        {
            "stock_code": "000001.SZ",
            "instrument": "SZ000001",
            "name": "平安银行",
        },
        {
            "stock_code": "600000.SH",
            "instrument": "SH600000",
            "name": "浦发银行",
        },
    ]
