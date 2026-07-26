"""Training-only hybrid CSI500/CSI1000 history construction.

The pre-2015 history is approximate.  The official suffix starting on
2015-11-30 is supplied by the existing ``csindex_v2`` instruments builder.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config as cfg


CUTOVER = "2015-11-30"
PREFIX_END = "2015-11-27"
HYBRID_ROOT = cfg.CACHE_ROOT / "hybrid"
CALENDAR_PATH = Path("~/.qlib/qlib_data/cn_data/calendars/day.txt").expanduser()


def ts_code_to_symbol(ts_code: str) -> str | None:
    """Convert a Tushare stock code to Qlib's exchange-prefixed symbol."""
    code, dot, exchange = str(ts_code).upper().partition(".")
    if dot != "." or len(code) != 6 or not code.isdigit():
        return None
    prefix = {"SH": "SH", "SZ": "SZ", "BJ": "BJ"}.get(exchange)
    return f"{prefix}{code}" if prefix else None


def select_csi1000_proxy(
    total_mv: pd.DataFrame,
    excluded: set[str],
    limit: int = 1000,
) -> set[str]:
    """Select the largest eligible stocks after excluding CSI300/CSI500."""
    frame = total_mv[["ts_code", "total_mv"]].copy()
    frame["symbol"] = frame["ts_code"].map(ts_code_to_symbol)
    frame["total_mv"] = pd.to_numeric(frame["total_mv"], errors="coerce")
    frame = frame[
        frame["symbol"].notna()
        & frame["total_mv"].gt(0)
        & ~frame["symbol"].isin(excluded)
    ]
    frame = frame.sort_values(
        ["total_mv", "symbol"],
        ascending=[False, True],
        kind="mergesort",
    )
    return set(frame.head(limit)["symbol"])


def _previous_trading_day(date: str, calendar: list[str]) -> str:
    position = bisect.bisect_left(calendar, date)
    if position == 0:
        raise ValueError(f"{date} 前没有可用交易日")
    return calendar[position - 1]


def rosters_to_closed_intervals(
    rosters: list[tuple[str, set[str]]],
    calendar: list[str],
    final_end: str,
    source: str,
) -> pd.DataFrame:
    """Convert dated rosters to inclusive, non-overlapping Qlib intervals."""
    ordered = sorted((date, set(members)) for date, members in rosters)
    open_since: dict[str, str] = {}
    previous_members: set[str] = set()
    rows: list[dict[str, str]] = []

    for date, members in ordered:
        for symbol in sorted(previous_members - members):
            rows.append(
                {
                    "symbol": symbol,
                    "start": open_since.pop(symbol),
                    "end": _previous_trading_day(date, calendar),
                    "source": source,
                }
            )
        for symbol in sorted(members - previous_members):
            open_since[symbol] = date
        previous_members = members

    for symbol, start in sorted(open_since.items()):
        rows.append(
            {
                "symbol": symbol,
                "start": start,
                "end": final_end,
                "source": source,
            }
        )

    columns = ["symbol", "start", "end", "source"]
    return (
        pd.DataFrame(rows, columns=columns)
        .sort_values(["symbol", "start"])
        .reset_index(drop=True)
    )


def active_members(intervals: pd.DataFrame, date: str) -> set[str]:
    """Return members active on ``date`` using inclusive interval semantics."""
    active = intervals[(intervals["start"] <= date) & (intervals["end"] >= date)]
    return set(active["symbol"])


def _iso_date(value: str) -> str:
    text = str(value).strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"非法日期: {value}")
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _weight_rosters(frame: pd.DataFrame) -> list[tuple[str, set[str]]]:
    required = {"trade_date", "con_code"}
    if not required.issubset(frame.columns):
        raise ValueError(f"指数权重缺少字段 {sorted(required - set(frame.columns))}")
    work = frame[["trade_date", "con_code"]].copy()
    work["trade_date"] = work["trade_date"].map(_iso_date)
    rosters: list[tuple[str, set[str]]] = []
    for date, group in work.groupby("trade_date", sort=True):
        members = {
            symbol
            for symbol in group["con_code"].map(ts_code_to_symbol)
            if symbol is not None
        }
        if members:
            rosters.append((date, members))
    return rosters


def _roster_on_or_before(
    rosters: list[tuple[str, set[str]]],
    date: str,
) -> set[str]:
    eligible = [members for roster_date, members in rosters if roster_date <= date]
    return set(eligible[-1]) if eligible else set()


def build_prefix_frames(
    index_weights: dict[str, pd.DataFrame],
    total_mv: pd.DataFrame,
    csi300: pd.DataFrame,
    calendar: list[str],
    proxy_limit: int = 1000,
) -> dict[str, pd.DataFrame]:
    """Build frozen pre-cutover intervals from monthly source rosters."""
    csi500_rosters = [
        item for item in _weight_rosters(index_weights["csi500"]) if item[0] < CUTOVER
    ]
    if not csi500_rosters:
        raise ValueError("CSI500 Tushare 月末快照为空")
    csi500 = rosters_to_closed_intervals(
        csi500_rosters,
        calendar,
        final_end=PREFIX_END,
        source="tushare_index_weight",
    )

    direct_rosters = [
        item for item in _weight_rosters(index_weights["csi1000"]) if item[0] < CUTOVER
    ]
    direct_start = direct_rosters[0][0] if direct_rosters else CUTOVER

    required_total_mv = {"trade_date", "ts_code", "total_mv"}
    if not required_total_mv.issubset(total_mv.columns):
        raise ValueError(
            f"总市值缓存缺少字段 "
            f"{sorted(required_total_mv - set(total_mv.columns))}"
        )
    market_cap = total_mv.copy()
    market_cap["trade_date"] = market_cap["trade_date"].map(_iso_date)
    proxy_rosters: list[tuple[str, set[str]]] = []
    for date, group in market_cap.groupby("trade_date", sort=True):
        if date >= direct_start:
            continue
        excluded = active_members(csi300, date)
        excluded |= _roster_on_or_before(csi500_rosters, date)
        proxy_rosters.append(
            (
                date,
                select_csi1000_proxy(
                    group,
                    excluded=excluded,
                    limit=proxy_limit,
                ),
            )
        )
    if not proxy_rosters:
        raise ValueError("CSI1000 total_mv 代理快照为空")

    proxy_end = (
        _previous_trading_day(direct_start, calendar) if direct_rosters else PREFIX_END
    )
    proxy = rosters_to_closed_intervals(
        proxy_rosters,
        calendar,
        final_end=proxy_end,
        source="total_mv_proxy",
    )
    direct = rosters_to_closed_intervals(
        direct_rosters,
        calendar,
        final_end=PREFIX_END,
        source="tushare_index_weight",
    )
    csi1000 = (
        pd.concat([proxy, direct], ignore_index=True)
        .sort_values(["symbol", "start"])
        .reset_index(drop=True)
    )
    return {
        "csi500_hybrid": csi500,
        "csi1000_hybrid": csi1000,
    }


def read_instruments(path: Path) -> pd.DataFrame:
    """Read a three-column Qlib instruments file."""
    return pd.read_csv(
        path,
        sep="\t",
        header=None,
        names=["symbol", "start", "end"],
        dtype=str,
    )


def sha256_file(path: Path) -> str:
    """Hash an input artifact for manifest-level reproducibility."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_text_atomic(text: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=dest.parent,
        prefix=f".{dest.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, dest)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_csv_atomic(frame: pd.DataFrame, dest: Path) -> None:
    _write_text_atomic(frame.to_csv(index=False), dest)


def freeze_prefixes(
    hybrid_root: Path = HYBRID_ROOT,
    changes_dir: Path = cfg.CHANGES_DIR,
    calendar_path: Path = CALENDAR_PATH,
) -> dict:
    """Freeze pre-cutover prefix CSVs and their reproducibility manifest."""
    snapshot_dir = hybrid_root / "snapshots"
    input_paths = {
        "csi500_index_weight": snapshot_dir / "csi500_index_weight.parquet",
        "csi1000_index_weight": snapshot_dir / "csi1000_index_weight.parquet",
        "total_mv_monthly": snapshot_dir / "total_mv_monthly.parquet",
        "csi300_official": changes_dir / "csi300_instruments.txt",
    }
    missing = [str(path) for path in input_paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"冻结 hybrid prefix 缺少输入: {missing}")

    calendar = [
        line.strip() for line in calendar_path.read_text().splitlines() if line.strip()
    ]
    csi300 = read_instruments(input_paths["csi300_official"])
    weights = {
        "csi500": pd.read_parquet(input_paths["csi500_index_weight"]),
        "csi1000": pd.read_parquet(input_paths["csi1000_index_weight"]),
    }
    total_mv = pd.read_parquet(input_paths["total_mv_monthly"])
    frames = build_prefix_frames(weights, total_mv, csi300, calendar)

    prefix_dir = hybrid_root / "prefixes"
    output_meta: dict[str, dict] = {}
    for name, frame in frames.items():
        if frame.empty or frame["end"].max() > PREFIX_END:
            raise ValueError(f"{name} prefix 日期越过 {PREFIX_END}")
        dest = prefix_dir / f"{name}_prefix.csv"
        _write_csv_atomic(frame, dest)
        output_meta[name] = {
            "path": str(dest),
            "rows": len(frame),
            "first": frame["start"].min(),
            "last": frame["end"].max(),
            "sha256": sha256_file(dest),
        }

    monthly_counts = {
        "csi500_hybrid": {
            date: len(members)
            for date, members in _weight_rosters(weights["csi500"])
            if date < CUTOVER
        },
        "csi1000_hybrid": {
            date: len(active_members(frames["csi1000_hybrid"], date))
            for date in sorted(
                {_iso_date(value) for value in total_mv["trade_date"]}
                | {date for date, _ in _weight_rosters(weights["csi1000"])}
            )
            if date < CUTOVER
        },
    }
    manifest = {
        "algorithm_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cutover": CUTOVER,
        "prefix_end": PREFIX_END,
        "proxy_rule": {
            "metric": "total_mv",
            "limit": 1000,
            "exclude": ["csi300", "csi500"],
        },
        "inputs": {
            name: {
                "path": str(path),
                "sha256": sha256_file(path),
            }
            for name, path in input_paths.items()
        },
        "outputs": output_meta,
        "monthly_member_counts": monthly_counts,
    }
    _write_text_atomic(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        hybrid_root / "manifest.json",
    )
    return manifest


def validate_interval_structure(intervals: pd.DataFrame) -> None:
    """Reject malformed or overlapping inclusive membership intervals."""
    required = {"symbol", "start", "end"}
    if not required.issubset(intervals.columns):
        raise ValueError(f"区间缺少字段 {sorted(required - set(intervals.columns))}")
    frame = intervals[["symbol", "start", "end"]].astype(str)
    if frame.empty:
        raise ValueError("区间为空")
    if (frame["start"] > frame["end"]).any():
        raise ValueError("区间起止日期倒置")
    if frame.duplicated().any():
        raise ValueError("存在完全重复区间")
    overlaps: list[str] = []
    for symbol, group in frame.sort_values(["symbol", "start"]).groupby("symbol"):
        starts = group["start"].tolist()
        ends = group["end"].tolist()
        if any(start <= previous_end for previous_end, start in zip(ends, starts[1:])):
            overlaps.append(symbol)
    if overlaps:
        raise ValueError(f"成员区间重叠: {overlaps[:10]}")


def splice_official_suffix(
    prefix: pd.DataFrame,
    official: pd.DataFrame,
) -> pd.DataFrame:
    """Append the official suffix without rewriting any official row."""
    prefix_columns = prefix[["symbol", "start", "end"]].copy()
    official_columns = official[["symbol", "start", "end"]].copy()
    if not prefix_columns.empty and (prefix_columns["end"] >= CUTOVER).any():
        raise ValueError(f"hybrid prefix 越过公告切换点 {CUTOVER}")
    if official_columns.empty or (official_columns["start"] < CUTOVER).any():
        raise ValueError(f"官方 instruments 含 {CUTOVER} 以前的区间")
    combined = pd.concat(
        [prefix_columns, official_columns],
        ignore_index=True,
    )
    validate_interval_structure(combined)
    return combined


def validate_official_suffix(
    hybrid: pd.DataFrame,
    official: pd.DataFrame,
    calendar: list[str],
) -> None:
    """Prove exact-row and daily-membership equality after the cutover."""
    expected = official[["symbol", "start", "end"]].reset_index(drop=True)
    actual = hybrid.loc[
        hybrid["start"] >= CUTOVER,
        ["symbol", "start", "end"],
    ].reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(actual, expected)
    except AssertionError as error:
        raise ValueError(
            f"hybrid 官方后缀与官方 instruments 不一致: {error}"
        ) from error

    for date in calendar:
        if date < CUTOVER:
            continue
        if active_members(hybrid, date) != active_members(official, date):
            raise ValueError(f"hybrid 官方后缀在 {date} 的逐日成分不一致")


def _instruments_text(frame: pd.DataFrame) -> str:
    return frame[["symbol", "start", "end"]].to_csv(
        sep="\t",
        header=False,
        index=False,
    )


def build_hybrid_outputs(
    hybrid_root: Path = HYBRID_ROOT,
    changes_dir: Path = cfg.CHANGES_DIR,
    calendar_path: Path = CALENDAR_PATH,
) -> dict[str, Path]:
    """Build and validate both hybrid outputs before replacing either file."""
    calendar = [
        line.strip() for line in calendar_path.read_text().splitlines() if line.strip()
    ]
    pairs = {
        "csi500_hybrid": "csi500",
        "csi1000_hybrid": "csi1000",
    }
    candidates: dict[str, pd.DataFrame] = {}
    destinations: dict[str, Path] = {}

    for hybrid_name, official_name in pairs.items():
        prefix_path = hybrid_root / "prefixes" / f"{hybrid_name}_prefix.csv"
        official_path = changes_dir / f"{official_name}_instruments.txt"
        if not prefix_path.exists() or not official_path.exists():
            raise FileNotFoundError(
                f"{hybrid_name} 构建输入缺失: "
                f"prefix={prefix_path.exists()} official={official_path.exists()}"
            )
        prefix = pd.read_csv(prefix_path, dtype=str)
        official = read_instruments(official_path)
        candidate = splice_official_suffix(prefix, official)
        validate_official_suffix(candidate, official, calendar)
        candidates[hybrid_name] = candidate
        destinations[hybrid_name] = changes_dir / f"{hybrid_name}_instruments.txt"

    for name, candidate in candidates.items():
        _write_text_atomic(
            _instruments_text(candidate),
            destinations[name],
        )
    return destinations


def main(argv: list[str] | None = None) -> int:
    """Run one hybrid-history lifecycle stage."""
    parser = argparse.ArgumentParser(
        description="构建训练专用 CSI500/CSI1000 hybrid 历史"
    )
    parser.add_argument(
        "command",
        choices=("backfill", "freeze-prefix", "build", "prepare"),
    )
    args = parser.parse_args(argv)

    if args.command in {"backfill", "prepare"}:
        from .hybrid_backfill import backfill_all

        backfill_all()
    if args.command in {"freeze-prefix", "prepare"}:
        freeze_prefixes()
    if args.command in {"build", "prepare"}:
        build_hybrid_outputs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
