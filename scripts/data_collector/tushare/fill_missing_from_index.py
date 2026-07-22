"""补全指数成分中缺失/过期的个股日线，写入 qlib bin。

用法:
  python fill_missing_from_index.py
  python fill_missing_from_index.py --only-missing
  python fill_missing_from_index.py --symbols SH600000,BJ430418
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import fire
import numpy as np
import pandas as pd
from loguru import logger

CUR_DIR = Path(__file__).resolve().parent
sys.path.append(str(CUR_DIR.parent.parent))

# 复用 collector 的采集与代码转换实现；凭据只从环境读取
import collector as ts_collector  # noqa: E402
from dump_bin import DumpDataUpdate  # noqa: E402

QLIB_DIR = Path("~/.qlib/qlib_data/cn_data").expanduser().resolve()
SOURCE_DIR = CUR_DIR / "source"
NORMALIZE_DIR = CUR_DIR / "normalize_fill"
GAPS_CSV = QLIB_DIR / "instruments" / "_coverage_gaps.csv"


def qlib_to_ts_code(symbol: str, bj_map: dict[str, str] | None = None) -> str:
    s = symbol.strip().upper()
    if bj_map and s in bj_map:
        return bj_map[s]
    if s.startswith("SH") and len(s) == 8:
        return f"{s[2:]}.SH"
    if s.startswith("SZ") and len(s) == 8:
        return f"{s[2:]}.SZ"
    if s.startswith("BJ") and len(s) == 8:
        code6 = s[2:]
        # 北交所改革后 43/83/87/88 → 92 + 后四位
        if code6.startswith(("43", "83", "87", "88")):
            return f"92{code6[2:]}.BJ"
        return f"{code6}.BJ"
    raise ValueError(f"unsupported symbol: {symbol}")


def build_bj_map(pro) -> dict[str, str]:
    """用 stock_basic 建立旧 BJ 代码 → 现行 ts_code 映射。"""
    frames = []
    for st in ("L", "D", "P"):
        time.sleep(0.4)
        b = pro.stock_basic(
            exchange="", list_status=st, fields="ts_code,symbol,name"
        )
        if b is None or b.empty:
            continue
        frames.append(b[b["ts_code"].str.endswith(".BJ")])
    if not frames:
        return {}
    allb = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
    codeset = set(allb["ts_code"])
    mapping: dict[str, str] = {}
    # 对 gaps 里的 BJ 预解析
    if GAPS_CSV.exists():
        gaps = pd.read_csv(GAPS_CSV, dtype=str)
        bj_syms = [s for s in gaps["symbol"] if str(s).startswith("BJ")]
    else:
        bj_syms = []
    for sym in bj_syms:
        code6 = sym[2:]
        cands = []
        if f"{code6}.BJ" in codeset:
            cands.append(f"{code6}.BJ")
        if code6.startswith(("43", "83", "87", "88")):
            alt = f"92{code6[2:]}.BJ"
            if alt in codeset:
                cands.append(alt)
        if cands:
            mapping[sym] = cands[0]
    return mapping


def get_pro():
    import tushare as ts

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未设置")
    return ts.pro_api(token)


def qlib_to_fname(symbol: str) -> str:
    return symbol.strip().lower()


def fetch_daily(pro, ts_code: str, start: str, end: str) -> pd.DataFrame:
    daily = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    if daily is None or daily.empty:
        return pd.DataFrame()
    daily = daily.rename(columns={"trade_date": "date", "vol": "volume"})
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values("date")
    if "pct_chg" in daily.columns:
        daily["pct_chg"] = daily["pct_chg"].astype(float)
    else:
        daily["pct_chg"] = np.nan

    adj = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=end)
    if adj is None or adj.empty:
        daily["adj_factor"] = 1.0
    else:
        adj = adj.rename(columns={"trade_date": "date"})
        adj["date"] = pd.to_datetime(adj["date"])
        daily = daily.merge(adj[["date", "adj_factor"]], on="date", how="left")
        daily["adj_factor"] = daily["adj_factor"].ffill().bfill().fillna(1.0)

    cols = ["date", "open", "high", "low", "close", "volume", "adj_factor", "pct_chg"]
    out = daily[[c for c in cols if c in daily.columns]].copy()
    return out


def normalize_df(df: pd.DataFrame, symbol_fname: str) -> pd.DataFrame:
    """与 TushareNormalize1d 对齐的精简归一化。"""
    if df.empty:
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    # 停牌
    if "volume" in df.columns:
        mask = (df["volume"] <= 0) | df["volume"].isna()
        for c in ("open", "high", "low", "close", "volume"):
            if c in df.columns:
                df.loc[mask, c] = np.nan
    # 前复权
    if "adj_factor" in df.columns and len(df):
        last = float(df["adj_factor"].iloc[-1]) or 1.0
        factor = df["adj_factor"] / last
        for c in ("open", "high", "low", "close"):
            if c in df.columns:
                df[c] = df[c] * factor
        if "volume" in df.columns:
            df["volume"] = df["volume"] / factor
        df["factor"] = factor
    if "pct_chg" in df.columns:
        df["change"] = df["pct_chg"] / 100.0
    df["symbol"] = symbol_fname
    keep = ["date", "symbol", "open", "high", "low", "close", "volume", "factor", "change"]
    return df[[c for c in keep if c in df.columns]]


def load_targets(only_missing: bool, symbols: str | None) -> list[str]:
    if symbols:
        # fire 可能把 "A,B" 解析成 tuple
        if isinstance(symbols, (list, tuple)):
            parts = symbols
        else:
            parts = str(symbols).replace(";", ",").split(",")
        return [str(s).strip().upper() for s in parts if str(s).strip()]
    if not GAPS_CSV.exists():
        raise FileNotFoundError(f"先运行 check_index_coverage.py 生成 {GAPS_CSV}")
    gaps = pd.read_csv(GAPS_CSV, dtype=str)
    if only_missing:
        gaps = gaps[gaps["issue"] == "missing_dir"]
    return gaps["symbol"].tolist()


def run(
    only_missing: bool = False,
    symbols: str = None,
    start: str = "20040101",
    end: str = None,
    delay: float = 0.35,
    dump: bool = True,
):
    if end is None:
        end = pd.Timestamp.today().strftime("%Y%m%d")
    targets = load_targets(only_missing, symbols)
    logger.info(f"targets={len(targets)} start={start} end={end}")

    pro = get_pro()
    bj_map = build_bj_map(pro)
    logger.info(f"BJ map size={len(bj_map)}")

    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZE_DIR.mkdir(parents=True, exist_ok=True)

    ok = fail = empty = skip = 0
    skipped: list[str] = []
    for i, sym in enumerate(targets, 1):
        try:
            ts_code = qlib_to_ts_code(sym, bj_map)
            fname = qlib_to_fname(sym)
            time.sleep(delay)
            raw = fetch_daily(pro, ts_code, start, end)
            if raw.empty and sym.startswith("BJ") and sym not in bj_map:
                skip += 1
                skipped.append(sym)
                logger.warning(f"[{i}/{len(targets)}] skip unmapped BJ {sym}")
                continue
            if raw.empty:
                empty += 1
                logger.warning(f"[{i}/{len(targets)}] empty {sym} ({ts_code})")
                continue
            raw["symbol"] = fname
            src_path = SOURCE_DIR / f"{fname}.csv"
            if src_path.exists():
                old = pd.read_csv(src_path)
                old["date"] = pd.to_datetime(old["date"])
                raw = pd.concat([old, raw], ignore_index=True)
                raw = raw.drop_duplicates(subset=["date"], keep="last").sort_values("date")
            raw.to_csv(src_path, index=False)

            norm = normalize_df(raw, fname)
            norm.to_csv(NORMALIZE_DIR / f"{fname}.csv", index=False)
            ok += 1
            if i % 20 == 0 or i == len(targets):
                logger.info(
                    f"progress {i}/{len(targets)} ok={ok} empty={empty} skip={skip} fail={fail}"
                )
        except Exception as e:
            fail += 1
            logger.error(f"[{i}/{len(targets)}] {sym}: {e}")

    logger.info(
        f"download done: ok={ok} empty={empty} skip={skip} fail={fail}"
    )
    if skipped:
        logger.warning(f"unmapped BJ skipped ({len(skipped)}): {skipped}")
    if dump and ok:
        logger.info("dump_update into qlib_dir...")
        # dump_bin 在 scripts/ 下
        sys.path.insert(0, str(CUR_DIR.parent.parent))
        dumper = DumpDataUpdate(
            data_path=NORMALIZE_DIR,
            qlib_dir=str(QLIB_DIR),
            exclude_fields="symbol,date",
            max_workers=8,
        )
        dumper.dump()
        logger.info("dump done")


if __name__ == "__main__":
    fire.Fire(run)
