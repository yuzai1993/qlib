# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
CSI Index instruments collector.

Uses Tushare index_weight API to fetch index constituents and derive
historical changes. Falls back to baostock for CSI500.

Caches index weight data locally to avoid redundant API calls on
subsequent daily runs.
"""

import json
import os
import abc
import sys
import time
from typing import List
from pathlib import Path

import fire
import pandas as pd
import baostock as bs
from tqdm import tqdm
from loguru import logger

CUR_DIR = Path(__file__).resolve().parent
sys.path.append(str(CUR_DIR.parent.parent))

from data_collector.index import IndexBase
from data_collector.utils import get_calendar_list, get_trading_date_by_shift
from data_collector.utils import get_instruments

NEW_COMPANIES_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{index_code}cons.xls"
)


def _get_tushare_pro():
    """Get tushare pro api instance."""
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError(
            "TUSHARE_TOKEN is not set. "
            "Get token at https://tushare.pro/user/token"
        )
    return ts.pro_api(token)


def _ts_code_to_symbol(ts_code: str) -> str:
    """Tushare con_code (000001.SZ) -> qlib symbol (SZ000001)."""
    code, ex = ts_code.upper().split(".")
    prefix = "SH" if ex == "SH" else "SZ"
    return f"{prefix}{code}"


class CSIIndex(IndexBase):
    """Base class for CSI indices using Tushare as data source."""

    @property
    def calendar_list(self) -> List[pd.Timestamp]:
        _calendar = getattr(self, "_calendar_list", None)
        if not _calendar:
            _calendar = get_calendar_list(bench_code=self.index_name.upper())
            setattr(self, "_calendar_list", _calendar)
        return _calendar

    @property
    @abc.abstractmethod
    def bench_start_date(self) -> pd.Timestamp:
        raise NotImplementedError("rewrite bench_start_date")

    @property
    @abc.abstractmethod
    def index_code(self) -> str:
        raise NotImplementedError("rewrite index_code")

    @property
    def tushare_index_code(self) -> str:
        """Tushare index code for index_weight API (e.g. 399300.SZ)."""
        code = self.index_code
        if code.startswith("000"):
            return f"{code}.SH"
        return f"{code}.SZ"

    @property
    def html_table_index(self) -> int:
        raise NotImplementedError("rewrite html_table_index")

    def format_datetime(self, inst_df: pd.DataFrame) -> pd.DataFrame:
        if self.freq != "day":
            inst_df[self.START_DATE_FIELD] = inst_df[self.START_DATE_FIELD].apply(
                lambda x: (pd.Timestamp(x) + pd.Timedelta(hours=9, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
            )
            inst_df[self.END_DATE_FIELD] = inst_df[self.END_DATE_FIELD].apply(
                lambda x: (pd.Timestamp(x) + pd.Timedelta(hours=15, minutes=0)).strftime("%Y-%m-%d %H:%M:%S")
            )
        return inst_df

    # ---- Tushare-based weight fetching with cache ----

    @property
    def _weight_cache_path(self) -> Path:
        return self.cache_dir / WEIGHT_CACHE_FILE

    @property
    def _changes_cache_path(self) -> Path:
        return self.cache_dir / CHANGES_CACHE_FILE

    def _load_weight_cache(self) -> pd.DataFrame:
        path = self._weight_cache_path
        if path.exists():
            try:
                return pd.read_pickle(path)
            except Exception as e:
                logger.warning(f"failed to load weight cache: {e}, will rebuild")
        return pd.DataFrame()

    def _save_weight_cache(self, df: pd.DataFrame):
        df.to_pickle(self._weight_cache_path)

    def _fetch_index_weight_from_tushare(
        self, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch index weight data from Tushare for a date range.

        index_weight is monthly data. We sample the first trading day
        of each month in the range to build the history.
        """
        pro = _get_tushare_pro()
        ts_index_code = self.tushare_index_code

        months = pd.date_range(start=start_date, end=end_date, freq="MS")
        all_dfs = []

        for month_start in tqdm(months, desc=f"Fetching {self.index_name} weights"):
            month_end = (month_start + pd.offsets.MonthEnd(0)).strftime("%Y%m%d")
            ms = month_start.strftime("%Y%m%d")
            try:
                df = pro.index_weight(
                    index_code=ts_index_code,
                    start_date=ms,
                    end_date=month_end,
                )
                if df is not None and not df.empty:
                    first_date = df["trade_date"].min()
                    df = df[df["trade_date"] == first_date]
                    all_dfs.append(df)
            except Exception as e:
                logger.warning(f"failed to fetch weight for {ms}: {e}")
            time.sleep(0.3)

        if not all_dfs:
            return pd.DataFrame()
        result = pd.concat(all_dfs, ignore_index=True)
        result["trade_date"] = pd.to_datetime(result["trade_date"])
        return result

    def _get_full_weight_history(self) -> pd.DataFrame:
        """Get full index weight history, using cache for known months."""
        cached = self._load_weight_cache()
        today = pd.Timestamp.now().normalize()

        if cached.empty:
            start = self.bench_start_date.strftime("%Y%m%d")
            logger.info(f"no weight cache, fetching full history from {start}")
        else:
            cached["trade_date"] = pd.to_datetime(cached["trade_date"])
            last_cached = cached["trade_date"].max()
            next_month = (last_cached + pd.offsets.MonthBegin(1))
            if next_month > today:
                logger.info(
                    f"weight cache up to date (last: {last_cached.strftime('%Y-%m-%d')}), "
                    f"no new months to fetch"
                )
                return cached
            start = next_month.strftime("%Y%m%d")
            logger.info(
                f"weight cache last: {last_cached.strftime('%Y-%m-%d')}, "
                f"fetching from {start}"
            )

        new_data = self._fetch_index_weight_from_tushare(
            start, today.strftime("%Y%m%d")
        )

        if new_data.empty and not cached.empty:
            return cached

        if cached.empty:
            result = new_data
        else:
            result = pd.concat([cached, new_data], ignore_index=True)
            result = result.drop_duplicates(
                subset=["index_code", "con_code", "trade_date"]
            )

        self._save_weight_cache(result)
        logger.info(f"weight cache updated: {len(result)} records")
        return result

    # ---- derive changes from weight snapshots ----

    def _derive_changes_from_weights(
        self, weight_df: pd.DataFrame
    ) -> pd.DataFrame:
        """Compare consecutive monthly snapshots to derive add/remove events."""
        if weight_df.empty:
            return pd.DataFrame()

        snapshots = (
            weight_df.groupby("trade_date")["con_code"]
            .apply(set)
            .sort_index()
        )

        results = []
        prev_codes = None
        for trade_date, codes in snapshots.items():
            if prev_codes is not None:
                added = codes - prev_codes
                removed = prev_codes - codes
                for c in added:
                    results.append({
                        self.SYMBOL_FIELD_NAME: _ts_code_to_symbol(c),
                        self.DATE_FIELD_NAME: trade_date,
                        "type": self.ADD,
                    })
                for c in removed:
                    results.append({
                        self.SYMBOL_FIELD_NAME: _ts_code_to_symbol(c),
                        self.DATE_FIELD_NAME: trade_date,
                        "type": self.REMOVE,
                    })
            prev_codes = codes

        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df[self.DATE_FIELD_NAME] = pd.to_datetime(df[self.DATE_FIELD_NAME])
        return df

    def get_changes(self) -> pd.DataFrame:
        """Get constituent changes derived from Tushare index_weight history."""
        logger.info("get companies changes (via Tushare index_weight)......")
        weight_df = self._get_full_weight_history()
        changes = self._derive_changes_from_weights(weight_df)
        logger.info(f"get companies changes finish: {len(changes)} records")
        return changes

    def get_new_companies(self) -> pd.DataFrame:
        """Fetch current constituents via Tushare index_weight.

        Uses the most recent month's weight data. end_date is set to
        today to ensure instruments cover the current trading day.
        """
        logger.info("get new companies (via Tushare)......")
        pro = _get_tushare_pro()
        today = pd.Timestamp.now().normalize()
        month_start = today.replace(day=1).strftime("%Y%m%d")
        month_end = today.strftime("%Y%m%d")

        df = pro.index_weight(
            index_code=self.tushare_index_code,
            start_date=month_start,
            end_date=month_end,
        )

        if df is None or df.empty:
            prev_month = today.replace(day=1) - pd.DateOffset(months=1)
            prev_end = prev_month + pd.offsets.MonthEnd(0)
            logger.info(
                f"no weight data for current month, trying {prev_month.strftime('%Y%m%d')}"
            )
            df = pro.index_weight(
                index_code=self.tushare_index_code,
                start_date=prev_month.strftime("%Y%m%d"),
                end_date=prev_end.strftime("%Y%m%d"),
            )

        if df is None or df.empty:
            raise ValueError(
                f"failed to get current constituents for {self.index_name} "
                f"from Tushare index_weight"
            )

        latest_date = df["trade_date"].max()
        df = df[df["trade_date"] == latest_date].copy()

        df[self.SYMBOL_FIELD_NAME] = df["con_code"].apply(_ts_code_to_symbol)
        df[self.END_DATE_FIELD] = today
        df[self.START_DATE_FIELD] = self.bench_start_date

        logger.info(
            f"end of get new companies: {len(df)} constituents "
            f"(weight date: {latest_date})"
        )
        return df[[self.SYMBOL_FIELD_NAME, self.START_DATE_FIELD, self.END_DATE_FIELD]]

    # ---- Tushare-aware parse_instruments (overrides IndexBase) ----
    #
    # The default `IndexBase.parse_instruments` initializes every current
    # constituent's start_date to `bench_start_date` (e.g. 2005-01-01) and
    # relies on the reverse pass over `changes_df` to fix it up. When
    # tushare `index_weight` history starts at e.g. 2016-01 (which is the
    # case for csi300/csi100), any add-event that happened *before* the
    # weight history start cannot be captured. As a result, the
    # start_date for stocks added between bench_start_date and
    # weight_history_start (e.g. 茅台 added in 2008, 美的 listed in 2013)
    # is left at 2005-01-01, leaking future-membership info into the
    # backtest universe (lookahead bias).
    #
    # This override fixes that by deriving an `effective_start_date` for
    # each symbol's first segment from the weight history itself:
    #   - if the symbol first appears in weight history "near" the
    #     history start (within 35 days), assume it has been in the index
    #     since `bench_start_date`;
    #   - otherwise, use its first weight-snapshot date as the effective
    #     start_date — this is the best estimate we have given monthly
    #     weight resolution.
    #
    # Other improvements bundled into this override:
    #   - O(N+M) symbol-wise add/remove pairing instead of O(N*M)
    #     row-by-row DataFrame mutation;
    #   - reuse weight_df for "current constituents" instead of an extra
    #     tushare API call (also makes the two halves consistent);
    #   - end_date is aligned to the most recent trading day;
    #   - output is sorted (symbol, start_date) for deterministic diffs;
    #   - additionally writes a date-stamped snapshot
    #     `{index}_{YYYYMMDD}.txt` so historical backtests can pin a
    #     specific universe version.
    def parse_instruments(self):
        logger.info(
            f"start parse {self.index_name.lower()} companies (tushare-aware)......"
        )

        # 1. Load weight history (cached, incremental).
        try:
            weight_df = self._get_full_weight_history()
        except Exception as e:
            logger.warning(
                f"failed to load weight history ({e}), "
                f"fallback to legacy parse_instruments"
            )
            return super().parse_instruments()

        if weight_df is None or weight_df.empty:
            logger.warning("weight history is empty, fallback to legacy")
            return super().parse_instruments()

        weight_df = weight_df.copy()
        weight_df["trade_date"] = pd.to_datetime(weight_df["trade_date"])
        weight_df["__symbol"] = weight_df["con_code"].apply(_ts_code_to_symbol)

        weight_history_min = weight_df["trade_date"].min()
        weight_start_buffer = weight_history_min + pd.Timedelta(days=35)
        first_appear = weight_df.groupby("__symbol")["trade_date"].min()

        # 1b. Load stock listing dates from instruments/all.txt. This is the
        #     critical fix for the most severe leak: stocks listed *after*
        #     bench_start_date that the weight history alone cannot
        #     identify (e.g. 美的 listed 2013-09-18, 比亚迪 listed
        #     2011-06-30) — without this, those stocks would be tagged
        #     start_date=2005-01-01 and pollute the historical universe.
        listing_dates = {}
        all_txt = self.instruments_dir.joinpath("all.txt")
        if all_txt.exists():
            try:
                all_df = pd.read_csv(
                    all_txt,
                    sep="\t",
                    header=None,
                    names=[
                        self.SYMBOL_FIELD_NAME,
                        self.START_DATE_FIELD,
                        self.END_DATE_FIELD,
                    ],
                )
                all_df[self.START_DATE_FIELD] = pd.to_datetime(
                    all_df[self.START_DATE_FIELD]
                )
                listing_dates = dict(
                    zip(
                        all_df[self.SYMBOL_FIELD_NAME],
                        all_df[self.START_DATE_FIELD],
                    )
                )
                logger.info(
                    f"loaded {len(listing_dates)} stock listing dates from all.txt"
                )
            except Exception as e:
                logger.warning(
                    f"failed to load listing dates from {all_txt} ({e}), "
                    f"listing-date constraint will be ignored"
                )
        else:
            logger.warning(
                f"{all_txt} not found, listing-date constraint will be ignored "
                f"(stocks listed after bench_start_date may still leak)"
            )

        # 2. Derive add/remove changes from monthly weight snapshots.
        changes_df = self._derive_changes_from_weights(weight_df)

        # 3. Determine current constituents from the latest snapshot
        #    (no extra API call, and guaranteed consistent with changes_df).
        latest_date = weight_df["trade_date"].max()
        new_syms = set(
            weight_df.loc[weight_df["trade_date"] == latest_date, "__symbol"]
        )
        if not new_syms:
            logger.warning(
                "no current constituents in weight history, fallback to legacy"
            )
            return super().parse_instruments()

        # 4. Align end_date to the most recent trading day (handles
        #    weekends / holidays correctly).
        today = pd.Timestamp.now().normalize()
        cal = self.calendar_list
        try:
            end_date_align = max(
                pd.Timestamp(d) for d in cal if pd.Timestamp(d) <= today
            )
        except ValueError:
            end_date_align = today

        # 5. Symbol-wise pairing of add/remove events into intervals.
        bench_start = self.bench_start_date
        all_syms = (
            set(changes_df[self.SYMBOL_FIELD_NAME].unique())
            if not changes_df.empty
            else set()
        ) | new_syms

        if not changes_df.empty:
            changes_df = changes_df.sort_values(
                [self.SYMBOL_FIELD_NAME, self.DATE_FIELD_NAME]
            )
            changes_by_sym = {
                sym: list(
                    grp[[self.DATE_FIELD_NAME, "type"]].itertuples(
                        index=False, name=None
                    )
                )
                for sym, grp in changes_df.groupby(self.SYMBOL_FIELD_NAME)
            }
        else:
            changes_by_sym = {}

        records = []
        leak_fixes = []  # for logging/diagnostics
        for sym in sorted(all_syms):
            is_current = sym in new_syms

            # Effective start of the *first* segment.
            if sym in first_appear.index:
                sym_first = first_appear[sym]
                if sym_first > weight_start_buffer:
                    # Symbol entered the index mid-history → its first
                    # weight-snapshot date is the best estimate of the
                    # actual addition date.
                    effective_start = sym_first
                else:
                    # Symbol present from the very start of weight
                    # history → assume it has been in the index since
                    # bench_start_date.
                    effective_start = bench_start
            else:
                # Symbol referenced only via remove events (rare); fall
                # back to bench_start_date.
                effective_start = bench_start

            # Listing-date lower bound: a stock cannot be in the index
            # before it was listed. This catches stocks that joined the
            # index *before* the weight history starts (where first_appear
            # is pinned to weight_history_min and the algorithm would
            # otherwise default to bench_start_date).
            listing = listing_dates.get(sym)
            if listing is not None and listing > effective_start:
                effective_start = listing

            if effective_start > bench_start:
                leak_fixes.append(
                    {
                        "symbol": sym,
                        "fixed_start": effective_start,
                        "leak_days": (effective_start - bench_start).days,
                    }
                )

            # Pair add/remove events into [start, end] intervals.
            cur_start = effective_start
            for date, etype in changes_by_sym.get(sym, []):
                if etype == self.REMOVE:
                    if cur_start is not None:
                        records.append(
                            {
                                self.SYMBOL_FIELD_NAME: sym,
                                self.START_DATE_FIELD: cur_start,
                                self.END_DATE_FIELD: date,
                            }
                        )
                        cur_start = None
                else:  # ADD
                    cur_start = date
            # Open interval — symbol is currently in the index.
            if cur_start is not None and is_current:
                records.append(
                    {
                        self.SYMBOL_FIELD_NAME: sym,
                        self.START_DATE_FIELD: cur_start,
                        self.END_DATE_FIELD: end_date_align,
                    }
                )

        # 6. Sort output deterministically and write main + dated snapshot.
        inst_df = pd.DataFrame(records)
        if inst_df.empty:
            raise ValueError(
                f"no instrument records produced for {self.index_name}"
            )
        inst_df = inst_df.sort_values(
            [self.SYMBOL_FIELD_NAME, self.START_DATE_FIELD]
        ).reset_index(drop=True)
        inst_df = self.format_datetime(inst_df)

        cols = [
            self.SYMBOL_FIELD_NAME,
            self.START_DATE_FIELD,
            self.END_DATE_FIELD,
        ]
        main_path = self.instruments_dir.joinpath(
            f"{self.index_name.lower()}.txt"
        )
        snapshot_path = self.instruments_dir.joinpath(
            f"{self.index_name.lower()}_{end_date_align.strftime('%Y%m%d')}.txt"
        )
        inst_df[cols].to_csv(main_path, sep="\t", index=False, header=None)
        inst_df[cols].to_csv(snapshot_path, sep="\t", index=False, header=None)

        logger.info(
            f"parse {self.index_name.lower()} finished: "
            f"{len(inst_df)} records, "
            f"{inst_df[self.SYMBOL_FIELD_NAME].nunique()} symbols, "
            f"fixed leaks: {len(leak_fixes)}, "
            f"end_date_aligned: {end_date_align.date()}"
        )
        if leak_fixes:
            top = sorted(
                leak_fixes, key=lambda x: x["leak_days"], reverse=True
            )[:5]
            logger.info(
                "  top leak fixes: "
                + ", ".join(
                    f"{f['symbol']}(+{f['leak_days']}d)" for f in top
                )
            )
        logger.info(f"  main:     {main_path}")
        logger.info(f"  snapshot: {snapshot_path}")


class CSI300Index(CSIIndex):
    @property
    def index_code(self):
        return "000300"

    @property
    def bench_start_date(self) -> pd.Timestamp:
        return pd.Timestamp("2005-01-01")

    @property
    def html_table_index(self) -> int:
        return 0


class CSI100Index(CSIIndex):
    @property
    def index_code(self):
        return "000903"

    @property
    def bench_start_date(self) -> pd.Timestamp:
        return pd.Timestamp("2006-05-29")

    @property
    def html_table_index(self) -> int:
        return 1


class CSI500Index(CSIIndex):
    @property
    def index_code(self) -> str:
        return "000905"

    @property
    def bench_start_date(self) -> pd.Timestamp:
        return pd.Timestamp("2007-01-15")

    def get_changes(self) -> pd.DataFrame:
        """CSI500: try Tushare first, fall back to baostock."""
        try:
            return super().get_changes()
        except Exception as e:
            logger.warning(f"Tushare index_weight failed for CSI500: {e}, falling back to baostock")
            return self.get_changes_with_history_companies(self.get_history_companies())

    def get_history_companies(self) -> pd.DataFrame:
        bs.login()
        today = pd.Timestamp.now()
        date_range = pd.DataFrame(pd.date_range(start="2007-01-15", end=today, freq="7D"))[0].dt.date
        ret_list = []
        for date in tqdm(date_range, desc="Download CSI500"):
            result = self.get_data_from_baostock(date)
            ret_list.append(result[["date", "symbol"]])
        bs.logout()
        return pd.concat(ret_list, sort=False)

    @staticmethod
    def get_data_from_baostock(date) -> pd.DataFrame:
        col = ["date", "symbol", "code_name"]
        rs = bs.query_zz500_stocks(date=str(date))
        zz500_stocks = []
        while (rs.error_code == "0") & rs.next():
            zz500_stocks.append(rs.get_row_data())
        result = pd.DataFrame(zz500_stocks, columns=col)
        result["symbol"] = result["symbol"].apply(lambda x: x.replace(".", "").upper())
        return result

    def get_new_companies(self) -> pd.DataFrame:
        """CSI500: try Tushare first, fall back to baostock."""
        try:
            return super().get_new_companies()
        except Exception as e:
            logger.warning(f"Tushare failed for CSI500 new companies: {e}, falling back to baostock")
            logger.info("get new companies (baostock)......")
            today = pd.Timestamp.now().normalize()
            bs.login()
            result = self.get_data_from_baostock(today.strftime("%Y-%m-%d"))
            bs.logout()
            df = result[["date", "symbol"]]
            df.columns = [self.END_DATE_FIELD, self.SYMBOL_FIELD_NAME]
            df[self.END_DATE_FIELD] = today
            df[self.START_DATE_FIELD] = self.bench_start_date
            logger.info("end of get new companies.")
            return df


if __name__ == "__main__":
    fire.Fire(get_instruments)
