"""缺口补齐：用 Tushare index_weight 月末快照差分，仅用于中证2000。

其余指数（csi300/500/1000）仅使用官网公告；本模块不再为它们生成差分记录。

方法：
  相邻月末快照差分 → add/remove 集合；
  若差分区间包含已知定期调样生效日，生效日=该日（date_precision=exact）；
  否则生效日=后一个快照日（date_precision=month，表示"至迟该日已生效"）。

输出: parsed/tushare_gap_changes.csv
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
from loguru import logger

from . import config as cfg

# ── token：只从运行环境读取 ─────────────────────────────────────────────────


def _get_pro():
    import tushare as ts

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")
    return ts.pro_api(token)


# ── 拉取窗口（全历史：兼作公告缺漏的自动补充与交叉校验基准） ──────────────────

PULL_WINDOWS = {
    "csi2000": ("932000.CSI", "2023-08", None),  # None = 到当前月
}

# 定期调样"新名单首个适用交易日"（2005-2022，来自已解析公告）
PERIODIC_DATES_EARLY = [
    "2005-07-01", "2006-01-04", "2006-07-03", "2007-01-04", "2007-07-02",
    "2008-01-02", "2008-07-01", "2009-01-05", "2009-07-01", "2010-01-04",
    "2010-07-01", "2011-01-04", "2011-07-01", "2012-01-04", "2012-07-02",
    "2013-01-04", "2013-07-01", "2013-12-16", "2014-06-16", "2014-12-15",
    "2015-06-15", "2015-12-14", "2016-06-13", "2016-12-12", "2017-06-12",
    "2017-12-11", "2018-06-11", "2018-12-17", "2019-06-17", "2019-12-16",
    "2020-06-15", "2020-12-14", "2021-06-11", "2021-12-10", "2022-06-10",
    "2022-12-09",
]


def _periodic_dates_modern() -> list[str]:
    """2023+ 定期调样生效日：6/12 月第二个周五的下一交易日。"""
    cal_path = Path("~/.qlib/qlib_data/cn_data/calendars/day.txt").expanduser()
    calendar = [l.strip() for l in cal_path.open() if l.strip()]
    dates = []
    for year in range(2023, 2027):
        for month in (6, 12):
            # 第二个周五
            fridays = pd.date_range(f"{year}-{month:02d}-01", periods=21).to_series()
            fridays = fridays[fridays.dt.weekday == 4]
            second_friday = fridays.iloc[1].strftime("%Y-%m-%d")
            # 下一交易日
            import bisect

            i = bisect.bisect_right(calendar, second_friday)
            if i < len(calendar):
                dates.append(calendar[i])
    return dates


# ── 快照拉取（含缓存） ────────────────────────────────────────────────────────

TS_SNAPSHOT_DIR = cfg.CACHE_ROOT / "tushare_snapshots"


def pull_monthly_snapshots(index_name: str) -> dict[str, set[str]]:
    """拉取该指数缺口窗口内的月末快照。返回 {snapshot_date: {ts_code,...}}。"""
    TS_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts_code, start_m, end_m = PULL_WINDOWS[index_name]
    if end_m is None:
        end_m = pd.Timestamp.now().strftime("%Y-%m")

    cache_file = TS_SNAPSHOT_DIR / f"{index_name}.parquet"
    if cache_file.exists():
        cached = pd.read_parquet(cache_file)
    else:
        cached = pd.DataFrame(columns=["trade_date", "con_code"])
    cached_months = set(str(d)[:6] for d in cached["trade_date"].unique())

    pro = _get_pro()
    months = pd.period_range(start=start_m, end=end_m, freq="M")
    new_frames = []
    for m in months:
        ym = m.strftime("%Y%m")
        if ym in cached_months:
            continue
        s = m.start_time.strftime("%Y%m%d")
        e = m.end_time.strftime("%Y%m%d")
        try:
            df = pro.index_weight(index_code=ts_code, start_date=s, end_date=e)
        except Exception as ex:
            logger.warning(f"{index_name} {ym}: {ex}")
            time.sleep(2)
            continue
        if len(df):
            # 只留每月最后一个快照日
            last_date = df["trade_date"].max()
            df = df[df["trade_date"] == last_date][["trade_date", "con_code"]]
            new_frames.append(df)
        time.sleep(0.4)

    if new_frames:
        cached = pd.concat([cached] + new_frames, ignore_index=True).drop_duplicates()
        cached.to_parquet(cache_file, index=False)
        logger.info(f"{index_name}: 新增 {len(new_frames)} 个月快照，缓存共 {cached['trade_date'].nunique()} 个快照日")

    snapshots: dict[str, set[str]] = {}
    for date, grp in cached.groupby("trade_date"):
        # 每月最后快照日
        snapshots[str(date)] = set(grp["con_code"])
    # 只保留每个月的最后一个快照日
    by_month: dict[str, str] = {}
    for d in snapshots:
        ym = d[:6]
        if ym not in by_month or d > by_month[ym]:
            by_month[ym] = d
    return {d: snapshots[d] for d in sorted(by_month.values())}


# ── 差分 → 变更记录 ───────────────────────────────────────────────────────────

def _ts_code_to_symbol(ts_code: str) -> str | None:
    code, _, exch = ts_code.partition(".")
    if len(code) != 6 or not code.isdigit():
        return None
    if exch == "SH":
        return f"SH{code}"
    if exch in ("SZ",):
        return f"SZ{code}"
    if exch == "BJ":
        return f"BJ{code}"
    return None


def derive_changes(index_name: str, snapshots: dict[str, set[str]]) -> pd.DataFrame:
    periodic = sorted(PERIODIC_DATES_EARLY + _periodic_dates_modern())
    dates = sorted(snapshots)
    rows = []
    for d1, d2 in zip(dates, dates[1:]):
        prev_set, next_set = snapshots[d1], snapshots[d2]
        # 快照残缺保护：规模指数快照数量应接近满额
        expected = cfg.INDEX_META[index_name]["expected_size"]
        if len(prev_set) < expected * 0.9 or len(next_set) < expected * 0.9:
            logger.warning(f"{index_name} {d1}->{d2}: 快照不完整 ({len(prev_set)}, {len(next_set)})，跳过")
            continue
        added = next_set - prev_set
        removed = prev_set - next_set
        if not added and not removed:
            continue
        d1_iso = f"{d1[:4]}-{d1[4:6]}-{d1[6:]}"
        d2_iso = f"{d2[:4]}-{d2[4:6]}-{d2[6:]}"
        hit = [p for p in periodic if d1_iso < p <= d2_iso]
        if hit:
            eff, precision = hit[0], "exact"
        else:
            eff, precision = d2_iso, "month"
        for ts_code_set, direction in ((added, "add"), (removed, "remove")):
            for tsc in ts_code_set:
                sym = _ts_code_to_symbol(tsc)
                if sym:
                    rows.append({
                        "index_name": index_name,
                        "symbol": sym,
                        "type": direction,
                        "effective_date": eff,
                        "announce_date": None,
                        "source_id": None,
                        "method": "tushare_monthly",
                        "date_precision": precision,
                    })
    return pd.DataFrame(rows)


def run_all() -> pd.DataFrame:
    cfg.ensure_dirs()
    frames = []
    for index_name in PULL_WINDOWS:
        snapshots = pull_monthly_snapshots(index_name)
        logger.info(f"{index_name}: {len(snapshots)} 个月末快照")
        df = derive_changes(index_name, snapshots)
        logger.info(f"{index_name}: 差分得到 {len(df)} 条变更")
        frames.append(df)
    result = pd.concat(frames, ignore_index=True)
    out = cfg.PARSED_DIR / "tushare_gap_changes.csv"
    result.to_csv(out, index=False)
    logger.info(f"保存 → {out}")
    return result


if __name__ == "__main__":
    run_all()
