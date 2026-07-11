"""csindex_v2 每日增量维护。

流程：
  1. 增量爬公告 + 下载四指数官网成分快照
  2. 解析 / 聚合 / 构建历史区间（公告日口径）
  3. 安装 csi300/500/1000/2000 到 qlib instruments
  4. 用官网当日快照差分对齐「当前在册」（替代原聚宽日更）

快照 URL（与 crawler.SNAPSHOT_URL_TEMPLATE 一致）：
  csi300  https://.../cons/000300cons.xls
  csi500  https://.../cons/000905cons.xls
  csi1000 https://.../cons/000852cons.xls
  csi2000 https://.../cons/932000cons.xls

用法：
  python -m scripts.data_collector.csindex_v2.updater
"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pandas as pd
from loguru import logger

from . import config as cfg
from . import crawler
from . import parser_content, parser_excel, parser_pdf
from .aggregator import aggregate
from .builder import build_all, load_current_snapshot

QLIB_INSTRUMENTS = Path("~/.qlib/qlib_data/cn_data/instruments").expanduser().resolve()
INSTALL_INDICES = ("csi300", "csi500", "csi1000", "csi2000")
OPEN_END = "2099-12-31"


def crawl_incremental() -> int:
    """增量爬取，返回本次新拉取的详情条数。"""
    before = {p.stem for p in cfg.DETAILS_DIR.glob("*.json")} if cfg.DETAILS_DIR.exists() else set()
    logger.info("=== csindex_v2 增量爬取 ===")
    items = crawler.build_manifest(incremental=True)
    crawler.fetch_details(items)
    crawler.download_attachments()
    crawler.download_embedded_attachments()
    crawler.download_snapshots()
    after = {p.stem for p in cfg.DETAILS_DIR.glob("*.json")}
    new_ids = after - before
    logger.info(f"本次新增详情 {len(new_ids)} 条: {sorted(new_ids)[:10]}")
    return len(new_ids)


def rebuild() -> str:
    """全量重解析 + 聚合 + 构建。"""
    logger.info("=== 解析 Excel / PDF / 正文 ===")
    parser_excel.parse_all()
    parser_pdf.parse_all()
    parser_content.parse_all()
    logger.info("=== 聚合 ===")
    aggregate()
    logger.info("=== 构建 instruments ===")
    return build_all()


def install_instruments(indices: tuple[str, ...] = INSTALL_INDICES) -> dict[str, Path]:
    """把 changes/{index}_instruments.txt 安装到 qlib instruments/。"""
    QLIB_INSTRUMENTS.mkdir(parents=True, exist_ok=True)
    installed: dict[str, Path] = {}
    for name in indices:
        src = cfg.CHANGES_DIR / f"{name}_instruments.txt"
        if not src.exists():
            raise FileNotFoundError(f"缺少构建产物: {src}")
        dest = QLIB_INSTRUMENTS / f"{name}.txt"
        shutil.copy2(src, dest)
        installed[name] = dest
        logger.info(f"安装 {name} → {dest}")
    return installed


def current_members(index_name: str, asof: str | None = None) -> set[str]:
    """读取已安装 instruments 中 asof 日仍在册的成分（区间为 [start, end)）。"""
    asof = asof or dt.date.today().isoformat()
    path = QLIB_INSTRUMENTS / f"{index_name}.txt"
    if not path.exists():
        return set()
    members: set[str] = set()
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        sym, start, end = parts[0], parts[1], parts[2]
        if start <= asof < end:
            members.add(sym)
    return members


def _load_instruments_df(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "start_date", "end_date"])
    return pd.read_csv(
        path, sep="\t", header=None, names=["symbol", "start_date", "end_date"], dtype=str
    )


def _write_instruments_df(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{r.symbol}\t{r.start_date}\t{r.end_date}" for r in df.itertuples(index=False)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def apply_snapshot_diff(index_name: str, snap_date: str, snap_set: set[str]) -> dict:
    """
    用官网快照对齐当前在册：调出关闭区间，调入追加 [snap_date, 2099-12-31)。

    快照是「当日收盘后成分」的权威来源，替代原聚宽日更。
    """
    path = QLIB_INSTRUMENTS / f"{index_name}.txt"
    df = _load_instruments_df(path)
    if df.empty:
        rows = [
            {"symbol": s, "start_date": snap_date, "end_date": OPEN_END}
            for s in sorted(snap_set)
        ]
        out = pd.DataFrame(rows)
        _write_instruments_df(out, path)
        logger.warning(f"{index_name}: 无既有 instruments，已用快照 {len(snap_set)} 只初始化")
        return {
            "added": sorted(snap_set),
            "removed": [],
            "updated": True,
            "snap_date": snap_date,
            "count": len(snap_set),
        }

    active_mask = (df["start_date"] <= snap_date) & (df["end_date"] > snap_date)
    active = set(df.loc[active_mask, "symbol"])
    added = sorted(snap_set - active)
    removed = sorted(active - snap_set)

    if not added and not removed:
        logger.info(f"{index_name}: 快照 {snap_date} 与在册一致（{len(snap_set)} 只）")
        return {
            "added": [],
            "removed": [],
            "updated": False,
            "snap_date": snap_date,
            "count": len(snap_set),
        }

    if removed:
        df.loc[active_mask & df["symbol"].isin(removed), "end_date"] = snap_date
    if added:
        new_rows = pd.DataFrame(
            [{"symbol": s, "start_date": snap_date, "end_date": OPEN_END} for s in added]
        )
        df = pd.concat([df, new_rows], ignore_index=True)

    df = df.sort_values(["symbol", "start_date"]).reset_index(drop=True)
    _write_instruments_df(df, path)
    logger.info(
        f"{index_name}: 快照对齐 {snap_date} +{len(added)} -{len(removed)} → {path.name}"
    )
    return {
        "added": added,
        "removed": removed,
        "updated": True,
        "snap_date": snap_date,
        "count": len(snap_set),
    }


def sync_from_official_snapshots(
    indices: tuple[str, ...] = INSTALL_INDICES,
) -> dict[str, dict]:
    """下载（或使用已缓存）官网快照，差分对齐四指数当前成分。"""
    # 确保快照是最新的
    crawler.download_snapshots()
    results: dict[str, dict] = {}
    for name in indices:
        snap_date, snap_set = load_current_snapshot(name)
        expected = cfg.INDEX_META[name]["expected_size"]
        if abs(len(snap_set) - expected) > max(20, expected // 20):
            logger.warning(
                f"{name}: 快照成分数 {len(snap_set)} 与标称 {expected} 偏差较大，仍继续对齐"
            )
        results[name] = apply_snapshot_diff(name, snap_date, snap_set)
    return results


def archive_snapshots() -> None:
    """把当日快照另存一份，便于回溯。"""
    today = dt.date.today().strftime("%Y%m%d")
    dest_dir = cfg.SNAPSHOTS_DIR / "daily" / today
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name, meta in cfg.INDEX_META.items():
        src = cfg.SNAPSHOTS_DIR / f"{meta['code']}cons.xls"
        if src.exists():
            shutil.copy2(src, dest_dir / src.name)
    logger.info(f"快照归档 → {dest_dir}")


def update_daily(force_rebuild: bool = True) -> dict:
    """
    每日入口：
      公告增量 → 重建历史区间 → 安装四指数 → 官网快照对齐当前成分
    """
    cfg.ensure_dirs()
    n_new = crawl_incremental()
    need_rebuild = force_rebuild or n_new > 0 or not all(
        (cfg.CHANGES_DIR / f"{n}_instruments.txt").exists() for n in INSTALL_INDICES
    )
    if need_rebuild:
        rebuild()
        rebuilt = True
    else:
        logger.info("跳过重建（无新公告且产物已存在）")
        rebuilt = False

    installed = install_instruments(INSTALL_INDICES)
    archive_snapshots()
    logger.info("=== 官网快照对齐当前成分 ===")
    snap_sync = sync_from_official_snapshots(INSTALL_INDICES)

    members = {name: current_members(name) for name in INSTALL_INDICES}
    for name, m in members.items():
        logger.info(f"{name} 当前在册 {len(m)} 只")

    return {
        "new_details": n_new,
        "rebuilt": rebuilt,
        "installed": {k: str(v) for k, v in installed.items()},
        "snapshot_sync": snap_sync,
        "members": members,
    }


if __name__ == "__main__":
    import fire

    fire.Fire(update_daily)
