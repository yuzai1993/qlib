# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Tushare Pro 日线采集与归一化，产出 qlib bin 格式。
- close 存真实收盘价；adjclose = close/adj_factor（前复权）；涨跌幅使用 Tushare 接口返回的 pct_chg。
- 支持天级增量更新，并根据复权因子回溯更新历史价格、成交量（close 不变）。
"""

import os
import sys
import datetime
import importlib
from pathlib import Path
from typing import Iterable, List

import fire
import numpy as np
import pandas as pd
from loguru import logger

import qlib
from qlib.data import D
from qlib.tests.data import GetData
from qlib.utils import code_to_fname, fname_to_code, exists_qlib_data
from qlib.constant import REG_CN as REGION_CN

CUR_DIR = Path(__file__).resolve().parent
sys.path.append(str(CUR_DIR.parent.parent))

from dump_bin import DumpDataAll
from data_collector.base import BaseCollector, BaseNormalize, BaseRun, Normalize
from data_collector.utils import deco_retry, get_calendar_list, get_hs_stock_symbols

os.environ["TUSHARE_TOKEN"] = "e7afca7966f571c3a526d94543b99198ccc06539325a065d03377a93"

def _symbol_to_ts_code(symbol: str) -> str:
    """内部 symbol (如 000001.sz) -> Tushare ts_code (000001.SZ)."""
    s = symbol.strip().upper()
    if ".SS" in s or ".SS" == s[-3:]:
        return s.replace(".SS", ".SH") if s.endswith(".SS") else s.split(".")[0] + ".SH"
    if ".SZ" in s or s.endswith(".SZ"):
        return s.replace(".SZ", ".SZ") if ".SZ" in s else s.split(".")[0] + ".SZ"
    return symbol


def _ts_code_to_symbol(ts_code: str) -> str:
    """Tushare ts_code (000001.SZ) -> 内部 symbol (000001.sz)."""
    code, ex = ts_code.upper().split(".")
    return f"{code}.sz" if ex == "SZ" else f"{code}.ss"


def _fetch_and_write_day_future(qlib_dir: str) -> None:
    """使用 Tushare 交易日历接口（doc_id=26）拉取 20040101 至本年最后一天的所有交易日，写入 calendars/day_future.txt。"""
    root = Path(qlib_dir).expanduser().resolve()
    future_path = root.joinpath("calendars", "day_future.txt")
    calendars_dir = future_path.parent
    calendars_dir.mkdir(parents=True, exist_ok=True)

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        logger.warning("TUSHARE_TOKEN not set, skip writing day_future from Tushare")
        return
    try:
        import tushare as ts  # pylint: disable=C0415
        pro = ts.pro_api(token)
    except Exception as e:
        logger.warning(f"Tushare pro_api init failed: {e}, skip writing day_future")
        return

    end_year = datetime.date.today().year
    start_date = "20040101"
    end_date = f"{end_year}1231"
    try:
        # 上交所交易日历即可代表 A 股交易日，接口文档 https://tushare.pro/document/2?doc_id=26
        df = pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_date, is_open="1")
        if df is None or df.empty:
            return
        dates = pd.to_datetime(df["cal_date"]).dt.normalize().sort_values().unique()
        lines = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates]
        np.savetxt(future_path, lines, fmt="%s", encoding="utf-8")
        logger.info(f"day_future.txt updated from Tushare trade_cal ({start_date}~{end_date}), {len(lines)} days")
    except Exception as e:
        logger.warning(f"Tushare trade_cal failed: {e}, skip writing day_future")


def _default_start_date_from_calendars(qlib_dir: str) -> str:
    """当 start_date 未传时：先用 Tushare 接口刷新 day_future.txt，再取在 day_future 中但不在 day.txt 中的最早日期。"""
    root = Path(qlib_dir).expanduser().resolve()
    day_path = root.joinpath("calendars", "day.txt")
    future_path = root.joinpath("calendars", "day_future.txt")

    # 读取 day_future 前，先用 Tushare 拉取 20040101 至本年末交易日并写入 day_future
    _fetch_and_write_day_future(qlib_dir)

    future_df = pd.read_csv(future_path, header=None)
    future_dates = set(pd.to_datetime(future_df.iloc[:, 0]).dt.normalize())
    if day_path.exists():
        day_df = pd.read_csv(day_path, header=None)
        day_dates = set(pd.to_datetime(day_df.iloc[:, 0]).dt.normalize())
        diff = sorted(future_dates - day_dates)
    else:
        diff = sorted(future_dates)
    if not diff:
        if day_path.exists():
            day_df = pd.read_csv(day_path, header=None)
            return (pd.Timestamp(day_df.iloc[-1, 0]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        return (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    return pd.Timestamp(diff[0]).strftime("%Y-%m-%d")


class TushareCollectorCN(BaseCollector):
    """A 股日线采集（Tushare Pro）：日线 + 复权因子，close 为真实收盘价，支持增量与复权回溯。"""

    retry = 2

    def __init__(
        self,
        save_dir: [str, Path],
        start=None,
        end=None,
        interval="1d",
        max_workers=4,
        max_collector_count=2,
        delay=0,
        check_data_length: int = None,
        limit_nums: int = None,
    ):
        if interval != "1d":
            raise ValueError("TushareCollectorCN only supports interval='1d'")
        super().__init__(
            save_dir=save_dir,
            start=start,
            end=end,
            interval=interval,
            max_workers=max_workers,
            max_collector_count=max_collector_count,
            delay=delay,
            check_data_length=check_data_length,
            limit_nums=limit_nums,
        )

    def get_instrument_list(self) -> List[str]:
        logger.info("get HS stock symbols (Tushare)...")
        symbols = get_hs_stock_symbols()
        logger.info(f"get {len(symbols)} symbols.")
        return symbols

    def normalize_symbol(self, symbol: str) -> str:
        parts = symbol.split(".")
        if len(parts) != 2:
            return symbol
        return f"sh{parts[0]}" if parts[-1].lower() == "ss" else f"sz{parts[0]}"

    def _get_pro(self):
        import tushare as ts  # pylint: disable=C0415
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            raise ValueError(
                "TUSHARE_TOKEN is not set. Get token at https://tushare.pro/user/token"
            )
        return ts.pro_api(token)

    @deco_retry(retry=retry)
    def get_data(
        self,
        symbol: str,
        interval: str,
        start_datetime: pd.Timestamp,
        end_datetime: pd.Timestamp,
    ) -> pd.DataFrame:
        if interval != "1d":
            return pd.DataFrame()
        self.sleep()
        ts_code = _symbol_to_ts_code(symbol)
        fmt = "%Y%m%d"
        start_date = start_datetime.strftime(fmt)
        end_date = end_datetime.strftime(fmt)

        pro = self._get_pro()
        # 日线
        daily = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if daily is None or daily.empty:
            logger.info(f"empty daily: {symbol} {start_date}~{end_date}")
        daily = daily.rename(columns={"trade_date": "date", "vol": "volume"})
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.sort_values("date")
        # 涨跌幅：使用接口返回的 pct_chg（%），落盘为 pct_chg 供 Normalize 转为 change
        if "pct_chg" in daily.columns:
            daily["pct_chg"] = daily["pct_chg"].astype(float)
        else:
            daily["pct_chg"] = np.nan

        # 复权因子（同一区间）
        adj = pro.adj_factor(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if adj is None or adj.empty:
            daily["adj_factor"] = 1.0
        else:
            adj = adj.rename(columns={"trade_date": "date"})
            adj["date"] = pd.to_datetime(adj["date"])
            daily = daily.merge(adj[["date", "adj_factor"]], on="date", how="left")
            daily["adj_factor"] = daily["adj_factor"].ffill().bfill().fillna(1.0)

        cols = ["date", "open", "high", "low", "close", "volume", "adj_factor", "pct_chg"]
        keep = [c for c in cols if c in daily.columns]
        out = daily[keep].copy()
        out["symbol"] = symbol
        return out


# --------------- Normalize：前复权、factor、change（前复权涨跌幅） ---------------


class TushareNormalize1d(BaseNormalize):
    """Tushare 日线归一化：factor=当日复权因子/最后一天复权因子，复权价格=价格*factor，复权成交量=volume/factor；close 与其余价格按首日 adjclose 标准化。"""

    DAILY_FORMAT = "%Y-%m-%d"
    COLUMNS = ["open", "high", "low", "close", "volume"]

    def _get_calendar_list(self) -> Iterable[pd.Timestamp]:
        return get_calendar_list("ALL")

    def _get_first_adjclose(self, df: pd.DataFrame) -> float:
        """取首个有效（非 NaN）adjclose，用于 _manual_adj_data 标准化。"""
        if df.empty or "adjclose" not in df.columns:
            return 1.0
        idx = df["adjclose"].first_valid_index()
        if idx is None:
            return 1.0
        return float(df.loc[df["adjclose"].first_valid_index() :, "adjclose"].iloc[0])

    def _manual_adj_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """按首日 adjclose 标准化：除 symbol/change 外，价格类（含 close、adjclose）除以首日 adjclose，volume 乘以首日 adjclose。"""
        if df.empty:
            return df
        df = df.copy()
        date_col = self._date_field_name
        sym_col = self._symbol_field_name
        df = df.sort_values(date_col).set_index(date_col)
        _adjclose = self._get_first_adjclose(df.reset_index())
        for _col in df.columns:
            if _col in (sym_col, "change", "factor"):
                continue
            if _col == "volume":
                df[_col] = df[_col] * _adjclose
            else:
                df[_col] = df[_col] / _adjclose
        return df.reset_index()

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        date_col = self._date_field_name
        sym_col = self._symbol_field_name
        if "adj_factor" not in df.columns:
            df["adj_factor"] = 1.0
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.sort_values(date_col).drop_duplicates(subset=[date_col], keep="last")
        symbol = df[sym_col].iloc[0] if sym_col in df.columns else "unknown"
        df = df.set_index(date_col)

        # 前复权：factor = 当日复权因子 / 最后一天的复权因子；复权价格 = 价格 * factor
        adj_series = df["adj_factor"].replace(0, np.nan).ffill().bfill().fillna(1.0)
        adj_last = float(adj_series.iloc[-1])
        df["factor"] = adj_series / adj_last
        df["adjclose"] = df["close"] * df["factor"]
        for c in ["open", "high", "low"]:
            if c in df.columns:
                df[c] = df[c] * df["factor"]
        if "volume" in df.columns:
            df["volume"] = df["volume"] / df["factor"]

        # 涨跌幅：使用 Tushare 接口返回的 pct_chg（%），转为小数
        if "pct_chg" in df.columns:
            df["change"] = df["pct_chg"].astype(float) / 100.0
        else:
            df["change"] = np.nan
        df.loc[(df["volume"] <= 0) | df["volume"].isna(), self.COLUMNS + ["change"]] = np.nan
        df[sym_col] = symbol
        df = df.reset_index()
        # 与日历对齐
        cal = list(self._calendar_list)
        if cal:
            min_d, max_d = df[date_col].min(), df[date_col].max()
            cal_dates = [t for t in cal if min_d <= pd.Timestamp(t) <= max_d]
            if cal_dates:
                df = df.set_index(date_col)
                df = df.reindex(pd.DatetimeIndex(cal_dates))
                df.index.name = date_col
                df[sym_col] = symbol
                df = df.reset_index()
        df = self._manual_adj_data(df)
        out_cols = [date_col, sym_col, "open", "high", "low", "close", "adjclose", "volume", "factor", "change"]
        return df[[c for c in out_cols if c in df.columns]]


# --------------- Run 与 dump 集成 ---------------


class Run(BaseRun):
    def __init__(
        self,
        source_dir=None,
        normalize_dir=None,
        max_workers=1,
        interval="1d",
        region=REGION_CN,
    ):
        if source_dir is None:
            source_dir = Path(self.default_base_dir).joinpath("source")
        if normalize_dir is None:
            normalize_dir = Path(self.default_base_dir).joinpath("normalize")
        super().__init__(source_dir, normalize_dir, max_workers, interval)
        self.region = region

    @property
    def collector_class_name(self):
        return "TushareCollectorCN"

    @property
    def normalize_class_name(self):
        return "TushareNormalize1d"

    @property
    def default_base_dir(self):
        return CUR_DIR

    def download_data(
        self,
        max_collector_count=2,
        delay=0,
        start=None,
        end=None,
        check_data_length=None,
        limit_nums=None,
    ):
        if pd.Timestamp(end or datetime.date.today()) > pd.Timestamp(datetime.date.today()):
            raise ValueError("end_date cannot be later than today.")
        if pd.Timestamp(start) > pd.Timestamp(end):
            logger.info("start_date cannot be later than end_date.")
            return False
        _class = getattr(self._cur_module, self.collector_class_name)
        _class(
            self.source_dir,
            max_workers=self.max_workers,
            max_collector_count=max_collector_count,
            delay=delay,
            start=start,
            end=end,
            interval=self.interval,
            check_data_length=check_data_length,
            limit_nums=limit_nums,
        ).collector_data()

        return True

    def normalize_data(
        self,
        date_field_name: str = "date",
        symbol_field_name: str = "symbol",
        end_date: str = None,
        **kwargs,
    ):
        _class = getattr(self._cur_module, self.normalize_class_name)
        Normalize(
            source_dir=self.source_dir,
            target_dir=self.normalize_dir,
            normalize_class=_class,
            max_workers=self.max_workers,
            date_field_name=date_field_name,
            symbol_field_name=symbol_field_name,
            end_date=end_date,
            **kwargs,
        ).normalize()

    def update_data_to_bin(
        self,
        qlib_dir: str,
        start_date: str = None,
        end_date: str = None,
        check_data_length: int = None,
        delay: float = 1,
        exists_skip: bool = False,
    ):
        qlib_dir = str(Path(qlib_dir).expanduser().resolve())
        if not exists_qlib_data(qlib_dir):
            GetData().qlib_data(
                target_dir=qlib_dir, interval=self.interval, region=self.region, exists_skip=exists_skip
            )
        if start_date is None:
            start_date = _default_start_date_from_calendars(qlib_dir)
        if end_date is None:
            end_date = pd.Timestamp(datetime.date.today()).strftime("%Y-%m-%d")
        download_success = self.download_data(delay=delay, start=start_date, end=end_date, check_data_length=check_data_length)
        if not download_success:
            logger.info("download_data failed, skip normalize and dump.")
            return
        self.normalize_data()
        _dump = DumpDataAll(
            data_path=self.normalize_dir,
            qlib_dir=qlib_dir,
            exclude_fields="symbol,date",
            max_workers=max(getattr(self, "max_workers", 1), 16),
        )
        _dump.dump()
        _region = getattr(self, "region", "cn").lower()
        if _region == "cn":
            index_mod = importlib.import_module("data_collector.cn_index.collector")
            for _index in ["CSI100", "CSI300"]:
                getattr(index_mod, "get_instruments")(qlib_dir, _index, market_index="cn_index")


if __name__ == "__main__":
    fire.Fire(Run)
