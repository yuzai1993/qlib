"""Causal daily market aggregates from the existing Qlib file provider.

Price matrices are read with the same float32 calendar-offset representation
as qlib.data.storage.file_storage.FileFeatureStorage. No Qlib workers are started.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

# Official publication, not the historical base date. Conservative masking also
# discards the prelaunch base observation when computing first-day returns.
INDEX_LAUNCH = {
    "SH000001": "1991-07-15",
    "SH000016": "2004-01-02",
    "SH000300": "2005-04-08",
    "SH000905": "2007-01-15",
    "SH000852": "2014-10-17",
    "SH000688": "2020-07-23",
    "SZ399001": "1995-01-23",
    "SZ399006": "2010-06-01",
}
INDEX_PUBLICATION_SOURCES = [
    "https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20150912_3988461.shtml",
    "https://www.sse.com.cn/market/sseindex/diclosure/c/c_20150911_3984876.shtml",
    "https://www.sse.com.cn/market/sseindex/diclosure/c/c_20150911_3984893.shtml",
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000905factsheet.pdf",
    "https://statics.citics.com/website/bdp/中证1000ETF市场特征与投资价值分析.pdf",
    "https://www.sse.com.cn/aboutus/mediacenter/conference/c/c_20200619_5130972.shtml",
    "https://www.cnindex.com.cn/zh_information/research_reports/2021/202107/P020210713404319744157.pdf",
    "https://www.szse.cn/disclosure/notice/general/t20100531_500454.html",
]


def mask_index_prelaunch(series, symbol):
    return series.where(series.index >= INDEX_LAUNCH[symbol])


def fingerprint(path):
    path = Path(path).resolve()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "sha256": digest.hexdigest(), "size": path.stat().st_size}


def read_bin_column(path, n_dates):
    raw = np.fromfile(path, dtype="<f4")
    if not len(raw) or not np.isfinite(raw[0]) or raw[0] != int(raw[0]) or raw[0] < 0:
        raise ValueError(f"invalid Qlib calendar offset: {path}")
    result = np.full(n_dates, np.nan, dtype=np.float32)
    offset = int(raw[0])
    count = min(len(raw) - 1, max(0, n_dates - offset))
    result[offset : offset + count] = raw[1 : count + 1]
    return result


def read_market(provider, end="2026-08-31"):
    provider = Path(provider).expanduser().resolve()
    calendar_path = provider / "calendars/day.txt"
    instrument_path = provider / "instruments/all.txt"
    full_calendar = pd.DatetimeIndex(pd.to_datetime(calendar_path.read_text().splitlines()))
    if not full_calendar.is_unique or not full_calendar.is_monotonic_increasing:
        raise ValueError("provider calendar must be unique and increasing")
    calendar = full_calendar[full_calendar <= end]
    instruments = pd.read_csv(instrument_path, sep="\t", header=None, names=["symbol", "start", "end"])
    stocks = instruments[instruments.symbol.str.match(r"^(SH60|SH68|SZ00|SZ30)\d{4}$")].copy()
    stocks = stocks.sort_values("symbol").drop_duplicates("symbol")
    names = stocks.symbol.tolist()
    matrices = {
        key: np.full((len(calendar), len(names)), np.nan, dtype=np.float32)
        for key in ("close", "open", "high", "low", "amount")
    }
    refs = [fingerprint(calendar_path), fingerprint(instrument_path)]
    missing = []
    for j, stock in enumerate(stocks.itertuples(index=False)):
        folder = provider / "features" / stock.symbol.lower()
        valid_life = (calendar >= stock.start) & (calendar <= stock.end)
        values = {}
        for field in ("close", "open", "high", "low", "volume", "vwap"):
            path = folder / f"{field}.day.bin"
            if not path.is_file():
                missing.append(str(path))
                values[field] = np.full(len(calendar), np.nan, dtype=np.float32)
                continue
            values[field] = read_bin_column(path, len(calendar))
            values[field][~valid_life] = np.nan
            refs.append(fingerprint(path))
        active = (values["volume"] > 0) & (values["close"] > 0)
        for field in ("close", "open", "high", "low"):
            matrices[field][:, j] = np.where(active, values[field], np.nan)
        # Collector normalizes volume/factor and vwap*factor: factor cancels.
        matrices["amount"][:, j] = np.where(active, values["volume"] * values["vwap"] * 100.0, np.nan)
        if (j + 1) % 1000 == 0:
            print(f"market input {j + 1}/{len(names)} stocks", flush=True)
    panel = {key: pd.DataFrame(values, index=calendar, columns=names) for key, values in matrices.items()}
    index_prices = {}
    for symbol in ("SH000001", "SH000016", "SH000300", "SH000905", "SH000852", "SH000688", "SZ399001", "SZ399006"):
        path = provider / "features" / symbol.lower() / "close.day.bin"
        if path.is_file():
            index_prices[symbol] = mask_index_prelaunch(
                pd.Series(read_bin_column(path, len(calendar)), index=calendar), symbol
            )
            refs.append(fingerprint(path))
    audit = {
        "data_version": str(full_calendar[-1].date()),
        "index_publication_dates": INDEX_LAUNCH,
        "index_publication_sources": INDEX_PUBLICATION_SOURCES,
        "provider": str(provider),
        "n_stocks": len(names),
        "input_files": refs,
        "missing_fields": missing,
        "amount_formula": "Qlib adjusted volume * adjusted vwap * 100 CNY; collector factor cancels",
        "universe": "historical provider all.txt life intervals; SH60/SH68/SZ00/SZ30; no current constituent filter",
        "survivorship_limit": "Coverage follows existing provider; unavailable delisted histories are not reconstructed.",
    }
    return panel, pd.DataFrame(index_prices, index=calendar), audit


def _tail_spread(realized_return, factor, lower=True, tail=0.3):
    # The position was known at the previous close; payoff is realized today.
    ranks = factor.shift(1).rank(axis=1, pct=True, method="average")
    low = realized_return.where(ranks <= tail).mean(axis=1)
    high = realized_return.where(ranks >= 1 - tail).mean(axis=1)
    return low - high if lower else high - low


def daily_aggregates(panel, min_stocks=100):
    close, amount = panel["close"], panel["amount"]
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    active = close.notna() & amount.gt(0)
    ret = ret.where(active)
    count = ret.notna().sum(axis=1)
    size = np.log1p(amount.rolling(20, min_periods=20).mean())
    vol = ret.rolling(20, min_periods=20).std()
    mom = close / close.shift(20) - 1
    output = {
        "n_stocks": count,
        "mkt": ret.mean(axis=1),
        "dispersion": ret.std(axis=1),
        "small_big": _tail_spread(ret, size),
        "lowvol_highvol": _tail_spread(ret, vol),
        "momentum": _tail_spread(ret, mom, lower=False),
        "lowvol_tail": _tail_spread(ret, vol, tail=0.1),
        "amount": amount.sum(axis=1, min_count=1),
        "amount_median": amount.median(axis=1),
        "amount_top10_share": amount.apply(lambda row: row.nlargest(10).sum(), axis=1) / amount.sum(axis=1),
        "breadth_up": ret.gt(0).sum(axis=1) / count,
        "breadth_down": ret.lt(0).sum(axis=1) / count,
        "skewness": ret.skew(axis=1),
        "kurtosis": ret.kurt(axis=1),
        "zero_return_share": ret.eq(0).sum(axis=1) / count,
        "illiquid_share": (amount.lt(1e7) & active).sum(axis=1) / active.sum(axis=1),
        "amihud": (ret.abs() / amount * 1e8).median(axis=1),
    }
    for q in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95):
        output[f"return_q{int(q*100)}"] = ret.quantile(q, axis=1)
    # Official board limits approximated by 9.5/19.5%; ST-specific 5% is not inferred.
    limit = pd.DataFrame(0.095, index=close.index, columns=close.columns)
    for symbol in close.columns:
        if symbol.startswith("SH68"):
            limit[symbol] = 0.195
        elif symbol.startswith("SZ30"):
            limit.loc[limit.index >= "2020-08-24", symbol] = 0.195
    up, down = ret.ge(limit), ret.le(-limit)
    output["limitup_proxy_share"] = up.sum(axis=1) / count
    output["limitdown_proxy_share"] = down.sum(axis=1) / count
    output["consecutive_limitup_proxy_share"] = (up & up.shift(1, fill_value=False)).sum(axis=1) / count
    output["consecutive_limitdown_proxy_share"] = (down & down.shift(1, fill_value=False)).sum(axis=1) / count
    for window in (20, 60, 120, 250):
        mean = close.rolling(window, min_periods=window).mean()
        valid = mean.notna() & active
        output[f"above_ma{window}"] = (close.gt(mean) & valid).sum(axis=1) / valid.sum(axis=1)
        highest = close.rolling(window, min_periods=window).max()
        lowest = close.rolling(window, min_periods=window).min()
        output[f"newhigh{window}_share"] = (close.ge(highest) & valid).sum(axis=1) / valid.sum(axis=1)
        output[f"newlow{window}_share"] = (close.le(lowest) & valid).sum(axis=1) / valid.sum(axis=1)
    output["gap_mean"] = (panel["open"] / close.shift(1) - 1).mean(axis=1)
    output["intraday_mean"] = (close / panel["open"] - 1).mean(axis=1)
    output["range_mean"] = ((panel["high"] - panel["low"]) / close.shift(1)).mean(axis=1)
    result = pd.DataFrame(output).replace([np.inf, -np.inf], np.nan)
    result.loc[count < min_stocks, result.columns.difference(["n_stocks"])] = np.nan
    result.index.name = "date"
    return result


def expand_features(daily, index_prices=None):
    """All transformations use only rows at or before the feature timestamp."""
    output, descriptions = {}, {}
    returns = ["mkt", "small_big", "lowvol_highvol", "momentum", "lowvol_tail"]
    for name in returns:
        s = daily[name]
        for window in (1, 2, 5, 10, 20, 60, 120, 250):
            output[f"{name}_sum{window}"] = s.rolling(window, min_periods=window).sum()
        for window in (5, 20, 60, 120):
            output[f"{name}_vol{window}"] = s.rolling(window).std()
            output[f"{name}_downvol{window}"] = s.clip(upper=0).pow(2).rolling(window).mean().pow(0.5)
        output[f"{name}_mtd"] = s.groupby(s.index.to_period("M")).cumsum()
        output[f"{name}_change5"] = s.rolling(5).mean() - s.rolling(20).mean()
        output[f"{name}_autocorr20"] = s.rolling(20).corr(s.shift(1))
        if name != "mkt":
            output[f"{name}_mktcorr60"] = s.rolling(60).corr(daily.mkt)
    nav = (1 + daily.mkt.fillna(0)).cumprod()
    for window in (20, 60, 120, 250):
        output[f"mkt_drawdown{window}"] = nav / nav.rolling(window).max() - 1
        output[f"mkt_uplow{window}"] = nav / nav.rolling(window).min() - 1
        output[f"mkt_ma_dist{window}"] = nav / nav.rolling(window).mean() - 1
    for name in daily.columns.difference(returns + ["n_stocks", "amount", "amount_median"]):
        output[name] = daily[name]
        for window in (5, 20, 60):
            output[f"{name}_mean{window}"] = daily[name].rolling(window).mean()
        output[f"{name}_change5"] = daily[name] - daily[name].shift(5)
    for name in ("amount", "amount_median"):
        s = daily[name]
        output[f"{name}_log"] = np.log1p(s)
        for short, long in ((1, 20), (5, 20), (5, 60), (20, 120)):
            output[f"{name}_ratio{short}_{long}"] = s.rolling(short).mean() / s.rolling(long).mean()
        output[f"{name}_z250"] = (s - s.rolling(250, min_periods=120).mean().shift(1)) / s.rolling(
            250, min_periods=120
        ).std().shift(1)
    output["weekday"] = pd.Series(daily.index.dayofweek, index=daily.index)
    output["month_day"] = pd.Series(daily.index.day, index=daily.index)
    output["month_elapsed_sessions"] = daily.groupby(daily.index.to_period("M")).cumcount() + 1
    if index_prices is not None:
        for name in index_prices:
            price = index_prices[name].where(index_prices[name] > 0)
            ret = price.pct_change(fill_method=None)
            for window in (1, 2, 5, 10, 20, 60, 120, 250):
                output[f"{name}_ret{window}"] = price / price.shift(window) - 1
            for window in (20, 60, 250):
                output[f"{name}_vol{window}"] = ret.rolling(window).std()
                output[f"{name}_dd{window}"] = price / price.rolling(window).max() - 1
                output[f"{name}_up{window}"] = price / price.rolling(window).min() - 1
                output[f"{name}_ewspread{window}"] = daily.mkt.rolling(window).sum() - ret.rolling(window).sum()
    features = pd.DataFrame(output, index=daily.index).replace([np.inf, -np.inf], np.nan)
    features.index.name = "date"
    for name in features:
        descriptions[name] = {
            "group": name.split("_")[0],
            "source": "Qlib close-of-day market aggregates",
            "available_at": "signal_date 20:00 Asia/Shanghai",
            "uses_future": False,
            "formula": name,
            "units": "ratio/share except explicitly log/count/calendar",
        }
        if "limitup" in name or "limitdown" in name:
            descriptions[name]["limitation"] = (
                "9.5/19.5% close-return proxy with historical board switch; "
                "not exact price-limit count: ST5%, IPO no-limit days unmodeled"
            )
    core = [f"{name}_sum{w}" for name in returns[:4] for w in (1, 5, 20, 60)]
    core += [
        "mkt_vol20",
        "mkt_vol60",
        "mkt_drawdown250",
        "mkt_uplow60",
        "amount_ratio5_60",
        "breadth_up",
        "limitup_proxy_share",
        "limitdown_proxy_share",
        "dispersion",
        "month_elapsed_sessions",
    ]
    return features, {"core_features": core, "full_features": list(features), "features": descriptions}
