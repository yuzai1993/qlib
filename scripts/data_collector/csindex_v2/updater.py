"""csindex_v2 每日增量维护。

流程：
  1. 增量爬公告 + 下载四指数官网成分快照
  2. 解析 / 聚合 / 构建历史区间（公告日口径）
  3. 安装 csi300/500/1000/2000 到 qlib instruments
  4. csi300/500/1000 用官网当日快照只读校验；csi2000 用同一份快照补丁当前在册

日期口径（重要）：
  csi300/500/1000 的 instruments 区间统一使用**公告日**——公告发出当天即视为成分变更，
  以便在指数基金实际调仓（生效日）前提前跟随。官网快照反映的是
  **生效日**口径的「当前在册」，在公告→生效窗口内与本地在册存在
  预期滞后（调入未生效 / 调出未生效）。这三段快照绝不能写回 instruments，
  否则会抹掉公告日口径的提前量；快照只用于发现真实漂移（漏公告、解析错）。
  csi2000 没有可用的完整公告链，改为按官网快照日补丁当前在册。

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
import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd
from loguru import logger

from . import config as cfg
from . import crawler
from . import parser_content, parser_excel, parser_pdf
from .aggregator import aggregate
from .builder import build_all, load_current_snapshot
from .hybrid_history import build_hybrid_outputs

QLIB_INSTRUMENTS = Path("~/.qlib/qlib_data/cn_data/instruments").expanduser().resolve()
OFFICIAL_INDICES = ("csi300", "csi500", "csi1000", "csi2000")
HYBRID_INDICES = ("csi500_hybrid", "csi1000_hybrid")
# 向后兼容已有导入；官网快照与公告流水线只使用这四个官方指数。
INSTALL_INDICES = OFFICIAL_INDICES


def crawl_incremental() -> int:
    """增量爬取，返回本次新拉取的详情条数。"""
    before = (
        {p.stem for p in cfg.DETAILS_DIR.glob("*.json")}
        if cfg.DETAILS_DIR.exists()
        else set()
    )
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
    """预检并分组安装；任一替换失败时恢复整组旧文件。"""
    QLIB_INSTRUMENTS.mkdir(parents=True, exist_ok=True)
    sources = {name: cfg.CHANGES_DIR / f"{name}_instruments.txt" for name in indices}
    missing = [str(path) for path in sources.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"缺少构建产物: {missing}")

    destinations = {name: QLIB_INSTRUMENTS / f"{name}.txt" for name in indices}
    staged: dict[str, Path] = {}
    backups: dict[str, Path | None] = {}
    touched: list[str] = []
    clean_backups = True
    try:
        for name in indices:
            src = sources[name]
            dest = destinations[name]
            descriptor, temporary = tempfile.mkstemp(
                dir=QLIB_INSTRUMENTS,
                prefix=f".{dest.name}.",
                suffix=".tmp",
            )
            temp_path = Path(temporary)
            staged[name] = temp_path
            with src.open("rb") as source, os.fdopen(descriptor, "wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())

        for name in indices:
            dest = destinations[name]
            backup_path = None
            if dest.exists():
                descriptor, backup = tempfile.mkstemp(
                    dir=QLIB_INSTRUMENTS,
                    prefix=f".{dest.name}.",
                    suffix=".backup",
                )
                os.close(descriptor)
                backup_path = Path(backup)
                backup_path.unlink()
                backups[name] = backup_path
                os.replace(dest, backup_path)
            else:
                backups[name] = None
            touched.append(name)
            os.replace(staged[name], dest)
    except BaseException as error:
        rollback_errors = []
        for name in reversed(touched):
            dest = destinations[name]
            backup_path = backups[name]
            try:
                dest.unlink(missing_ok=True)
                if backup_path is not None and backup_path.exists():
                    os.replace(backup_path, dest)
            except Exception as rollback_error:
                rollback_errors.append(f"{name}: {rollback_error}")
        if rollback_errors:
            clean_backups = False
            raise RuntimeError(
                f"instruments 安装失败且回滚不完整: {rollback_errors}"
            ) from error
        raise
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
        if clean_backups:
            for path in backups.values():
                if path is not None:
                    path.unlink(missing_ok=True)

    installed = {}
    for name in indices:
        dest = destinations[name]
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
        path,
        sep="\t",
        header=None,
        names=["symbol", "start_date", "end_date"],
        dtype=str,
    )


# 公告日 → 生效日的最大预期窗口。定期调样公告一般提前约 2 周发布，
# 窗口内快照与公告日口径在册的差异属预期滞后，超窗仍不一致才算真实漂移。
SNAPSHOT_LAG_DAYS = 25


def diff_snapshot(
    df: pd.DataFrame,
    snap_date: str,
    snap_set: set[str],
    lag_days: int = SNAPSHOT_LAG_DAYS,
) -> dict:
    """官网快照 vs 公告日口径在册的纯比对（不做任何 IO）。

    差异分两类：
    - 预期滞后（pending_*）：公告已调入/调出但尚未生效——差异股票的
      区间边界落在 snap_date 前 lag_days 天内，快照仍是旧名单，正常。
    - 真实漂移（unexplained_*）：差异股票近期无任何区间边界，
      说明漏公告或解析错，需要告警。
    """
    lag_floor = (pd.Timestamp(snap_date) - pd.Timedelta(days=lag_days)).strftime(
        "%Y-%m-%d"
    )

    active_mask = (df["start_date"] <= snap_date) & (df["end_date"] > snap_date)
    active = set(df.loc[active_mask, "symbol"])

    only_local = sorted(active - snap_set)  # 在册有、快照无
    only_snap = sorted(snap_set - active)  # 快照有、在册无

    # 公告已调入未生效：活跃区间的 start 落在滞后窗口内
    recent_start = set(df.loc[active_mask & (df["start_date"] >= lag_floor), "symbol"])
    pending_add = [s for s in only_local if s in recent_start]
    unexplained_local = [s for s in only_local if s not in recent_start]

    # 公告已调出未生效：最近一段区间的 end 落在滞后窗口内
    closed = df.loc[df["end_date"] <= snap_date]
    recent_end = set(closed.loc[closed["end_date"] >= lag_floor, "symbol"])
    pending_drop = [s for s in only_snap if s in recent_end]
    unexplained_snap = [s for s in only_snap if s not in recent_end]

    return {
        "ok": not unexplained_local and not unexplained_snap,
        "snap_date": snap_date,
        "snap_count": len(snap_set),
        "error": None,
        "pending_add": pending_add,
        "pending_drop": pending_drop,
        "unexplained_local": unexplained_local,
        "unexplained_snap": unexplained_snap,
    }


def compare_snapshot(index_name: str, snap_date: str, snap_set: set[str]) -> dict:
    """读取已安装 instruments 与官网快照比对：只比对，绝不写回。"""
    path = QLIB_INSTRUMENTS / f"{index_name}.txt"
    df = _load_instruments_df(path)
    if df.empty:
        return {
            "ok": False,
            "snap_date": snap_date,
            "snap_count": len(snap_set),
            "error": f"instruments 缺失或为空: {path}",
            "pending_add": [],
            "pending_drop": [],
            "unexplained_local": [],
            "unexplained_snap": [],
        }

    result = diff_snapshot(df, snap_date, snap_set)
    pending_add = result["pending_add"]
    pending_drop = result["pending_drop"]
    if result["ok"] and not pending_add and not pending_drop:
        logger.info(f"{index_name}: 快照 {snap_date} 与在册一致（{len(snap_set)} 只）")
    elif result["ok"]:
        logger.info(
            f"{index_name}: 快照 {snap_date} 存在预期滞后（公告日口径提前量）："
            f"调入未生效 {len(pending_add)} 只 {pending_add[:5]}，"
            f"调出未生效 {len(pending_drop)} 只 {pending_drop[:5]}"
        )
    else:
        logger.error(
            f"{index_name}: 快照 {snap_date} 存在无法解释的漂移（疑漏公告/解析错）："
            f"仅在册 {result['unexplained_local'][:10]}，"
            f"仅快照 {result['unexplained_snap'][:10]}"
        )
    return result


def check_against_official_snapshots(
    indices: tuple[str, ...] = OFFICIAL_INDICES,
) -> dict[str, dict]:
    """下载（或使用已缓存）官网快照并逐指数比对。只读校验，不改 instruments。"""
    # 确保快照是最新的
    crawler.download_snapshots()
    results: dict[str, dict] = {}
    for name in indices:
        snap_date, snap_set = load_current_snapshot(name)
        expected = cfg.INDEX_META[name]["expected_size"]
        if abs(len(snap_set) - expected) > max(20, expected // 20):
            logger.warning(
                f"{name}: 快照成分数 {len(snap_set)} 与标称 {expected} 偏差较大"
            )
        results[name] = compare_snapshot(name, snap_date, snap_set)
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
      公告增量 → 重建并安装官方指数 → 拼接并安装 hybrid → 官网快照校验

    csi300/500/1000 以公告构建结果为准，快照差异只用于告警，不回写。
    csi2000 由 builder 按官网快照补丁当前在册。
    hybrid 失败不会阻止官方安装和校验，但会记录错误供入口返回非零。
    """
    cfg.ensure_dirs()
    n_new = crawl_incremental()
    need_rebuild = (
        force_rebuild
        or n_new > 0
        or not all(
            (cfg.CHANGES_DIR / f"{n}_instruments.txt").exists()
            for n in OFFICIAL_INDICES
        )
    )
    if need_rebuild:
        rebuild()
        rebuilt = True
    else:
        logger.info("跳过重建（无新公告且产物已存在）")
        rebuilt = False

    installed = install_instruments(OFFICIAL_INDICES)
    hybrid_error = None
    try:
        build_hybrid_outputs()
        installed.update(install_instruments(HYBRID_INDICES))
    except Exception as error:
        hybrid_error = str(error)
        logger.exception(f"hybrid 指数刷新失败，保留上一次成功安装的文件: {error}")

    archive_snapshots()
    logger.info("=== 官网快照校验（300/500/1000 只读；2000 已由 builder 写入）===")
    snap_check = check_against_official_snapshots(OFFICIAL_INDICES)

    members = {
        name: current_members(name) for name in OFFICIAL_INDICES + HYBRID_INDICES
    }
    for name, m in members.items():
        logger.info(f"{name} 当前在册 {len(m)} 只")

    return {
        "new_details": n_new,
        "rebuilt": rebuilt,
        "hybrid_error": hybrid_error,
        "installed": {k: str(v) for k, v in installed.items()},
        "snapshot_check": snap_check,
        "members": members,
    }


if __name__ == "__main__":
    import fire

    fire.Fire(update_daily)
