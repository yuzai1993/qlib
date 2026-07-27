"""Finite, resumable Tushare backfill for hybrid index history inputs."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

import pandas as pd

from . import config as cfg


INDEX_WEIGHT_SPECS = {
    "csi500": ("000905.SH", "2010-01", "2015-11", 500),
    "csi1000": ("000852.SH", "2015-05", "2015-11", 1000),
}
HYBRID_ROOT = cfg.CACHE_ROOT / "hybrid"
LEGACY_SNAPSHOT_DIR = cfg.CACHE_ROOT / "tushare_snapshots"
CALENDAR_PATH = Path("~/.qlib/qlib_data/cn_data/calendars/day.txt").expanduser()


def get_tushare_pro() -> Any:
    """Create a Tushare Pro client from the process environment."""
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")
    import tushare as ts

    return ts.pro_api(token)


class _LazyTusharePro:
    """Delay credential access until a missing cache requires the network."""

    def __init__(self):
        self._client: Any | None = None

    def __getattr__(self, name: str) -> Any:
        if self._client is None:
            self._client = get_tushare_pro()
        return getattr(self._client, name)


def required_month_end_dates(
    calendar: list[str],
    start_month: str,
    end_month: str,
) -> list[str]:
    """Return the last local trading day in every requested calendar month."""
    start = pd.Period(start_month, freq="M")
    end = pd.Period(end_month, freq="M")
    by_month: dict[str, str] = {}
    for date in calendar:
        month = str(date)[:7]
        period = pd.Period(month, freq="M")
        if start <= period <= end:
            by_month[month] = str(date)[:10].replace("-", "")
    return [by_month[str(month)] for month in pd.period_range(start, end, freq="M")]


def merge_cache(
    existing: pd.DataFrame,
    incoming: pd.DataFrame,
    keys: list[str],
) -> pd.DataFrame:
    """Merge resumable cache frames without duplicate logical records."""
    if existing.empty:
        merged = incoming.copy()
    elif incoming.empty:
        merged = existing.copy()
    else:
        merged = pd.concat([existing, incoming], ignore_index=True, sort=False)
    if merged.empty:
        return merged
    return (
        merged.drop_duplicates(subset=keys, keep="last")
        .sort_values(keys, kind="mergesort")
        .reset_index(drop=True)
    )


def keep_latest_snapshot_per_month(
    frame: pd.DataFrame,
    date_column: str = "trade_date",
) -> pd.DataFrame:
    """Normalize a cache to one (latest) snapshot date per month."""
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    work[date_column] = work[date_column].astype(str)
    work["_month"] = work[date_column].str[:6]
    latest = work.groupby("_month")[date_column].transform("max")
    return (
        work.loc[work[date_column] == latest]
        .drop(columns="_month")
        .reset_index(drop=True)
    )


def _write_parquet_atomic(frame: pd.DataFrame, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=dest.parent,
        prefix=f".{dest.name}.",
        suffix=".tmp",
    )
    os.close(handle)
    temp_path = Path(temporary)
    try:
        frame.to_parquet(temp_path, index=False)
        os.replace(temp_path, dest)
    finally:
        temp_path.unlink(missing_ok=True)


def _read_parquet_or_empty(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    frame = pd.read_parquet(path)
    for column in columns:
        if column not in frame:
            frame[column] = pd.Series(dtype="object")
    return frame


def backfill_index_weights(
    pro: Any,
    snapshot_dir: Path,
    legacy_dir: Path,
    sleep_seconds: float = 0.4,
    specs: dict[str, tuple[str, str, str, int]] | None = None,
) -> dict[str, Path]:
    """Migrate and fill finite month-end index-weight snapshot caches."""
    specs = specs or INDEX_WEIGHT_SPECS
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    for name, (index_code, start_month, end_month, expected) in specs.items():
        dest = snapshot_dir / f"{name}_index_weight.parquet"
        legacy = legacy_dir / f"{name}.parquet"
        legacy_cached = _read_parquet_or_empty(
            legacy,
            ["trade_date", "con_code"],
        )
        destination_cached = _read_parquet_or_empty(
            dest,
            ["trade_date", "con_code"],
        )
        cached = merge_cache(
            legacy_cached,
            destination_cached,
            ["trade_date", "con_code"],
        )
        cached = keep_latest_snapshot_per_month(cached)
        cached["trade_date"] = cached["trade_date"].astype(str)
        cached["con_code"] = cached["con_code"].astype(str)

        for month in pd.period_range(start_month, end_month, freq="M"):
            ym = month.strftime("%Y%m")
            in_month = cached[cached["trade_date"].str[:6] == ym]
            if not in_month.empty:
                latest = in_month["trade_date"].max()
                count = in_month.loc[
                    in_month["trade_date"] == latest, "con_code"
                ].nunique()
                allowed_extra = 2 if name == "csi1000" else 0
                if expected <= count <= expected + allowed_extra:
                    continue
                cached = cached[cached["trade_date"].str[:6] != ym]

            frame = pro.index_weight(
                index_code=index_code,
                start_date=month.start_time.strftime("%Y%m%d"),
                end_date=month.end_time.strftime("%Y%m%d"),
            )
            if frame is None or frame.empty:
                raise RuntimeError(f"{name} index_weight {ym} 返回空数据")
            required = {"trade_date", "con_code"}
            if not required.issubset(frame.columns):
                raise RuntimeError(
                    f"{name} index_weight {ym} 缺少字段 "
                    f"{sorted(required - set(frame.columns))}"
                )
            frame = frame.copy()
            frame["trade_date"] = frame["trade_date"].astype(str)
            latest = frame["trade_date"].max()
            frame = frame[frame["trade_date"] == latest]
            count = frame["con_code"].nunique()
            allowed_extra = 2 if name == "csi1000" else 0
            if not expected <= count <= expected + allowed_extra:
                raise RuntimeError(
                    f"{name} index_weight {latest} 成分数 {count}，"
                    f"期望 {expected}~{expected + allowed_extra}"
                )
            cached = merge_cache(
                cached,
                frame,
                ["trade_date", "con_code"],
            )
            cached = keep_latest_snapshot_per_month(cached)
            _write_parquet_atomic(cached, dest)
            if sleep_seconds:
                time.sleep(sleep_seconds)

        _write_parquet_atomic(cached, dest)
        paths[name] = dest
    return paths


def backfill_total_mv(
    pro: Any,
    dest: Path,
    calendar: list[str],
    sleep_seconds: float = 0.4,
    start_month: str = "2010-01",
    end_month: str = "2015-04",
    min_rows: int = 800,
) -> Path:
    """Fetch missing month-end total-market-cap cross sections."""
    if dest.exists():
        cached = pd.read_parquet(dest)
    else:
        cached = pd.DataFrame(columns=["ts_code", "trade_date", "total_mv"])
    cached["trade_date"] = cached["trade_date"].astype(str)
    cached_counts = cached.groupby("trade_date")["ts_code"].nunique().to_dict()

    for trade_date in required_month_end_dates(calendar, start_month, end_month):
        if cached_counts.get(trade_date, 0) >= min_rows:
            continue
        cached = cached[cached["trade_date"] != trade_date]
        frame = pro.daily_basic(
            trade_date=trade_date,
            fields="ts_code,trade_date,total_mv",
        )
        if frame is None or frame.empty:
            raise RuntimeError(f"daily_basic {trade_date} 返回空数据")
        columns = ["ts_code", "trade_date", "total_mv"]
        required = set(columns)
        if not required.issubset(frame.columns):
            raise RuntimeError(
                f"daily_basic {trade_date} 缺少字段 "
                f"{sorted(required - set(frame.columns))}"
            )
        if len(frame) < min_rows:
            raise RuntimeError(
                f"daily_basic {trade_date} 截面仅 {len(frame)} 行，"
                f"至少需要 {min_rows} 行"
            )
        frame = frame[columns].copy()
        frame["trade_date"] = frame["trade_date"].astype(str)
        cached = merge_cache(
            cached,
            frame,
            ["trade_date", "ts_code"],
        )
        _write_parquet_atomic(cached, dest)
        cached_counts[trade_date] = frame["ts_code"].nunique()
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return dest


def backfill_all(
    hybrid_root: Path = HYBRID_ROOT,
    legacy_dir: Path = LEGACY_SNAPSHOT_DIR,
    calendar_path: Path = CALENDAR_PATH,
    pro: Any | None = None,
    sleep_seconds: float = 0.4,
    index_specs: dict[str, tuple[str, str, str, int]] | None = None,
    total_mv_start: str = "2010-01",
    total_mv_end: str = "2015-04",
    total_mv_min_rows: int = 800,
) -> dict[str, Path]:
    """Prepare every finite source cache required by the hybrid prefixes."""
    client = pro if pro is not None else _LazyTusharePro()
    calendar = [
        line.strip() for line in calendar_path.read_text().splitlines() if line.strip()
    ]
    snapshot_dir = hybrid_root / "snapshots"
    paths = backfill_index_weights(
        client,
        snapshot_dir,
        legacy_dir,
        sleep_seconds=sleep_seconds,
        specs=index_specs,
    )
    total_mv_path = snapshot_dir / "total_mv_monthly.parquet"
    backfill_total_mv(
        client,
        total_mv_path,
        calendar,
        sleep_seconds=sleep_seconds,
        start_month=total_mv_start,
        end_month=total_mv_end,
        min_rows=total_mv_min_rows,
    )
    paths["total_mv_monthly"] = total_mv_path
    return paths
