"""JoinQuant 每日快照拉取器。

策略：
- 对每个指数，逐交易日调用 get_index_stocks(code, date)，记录当日成分股集合。
- 快照存储为 parquet 文件（date, symbol 两列），支持增量追加。
- progress.json 记录已完成的最新日期和本会话调用次数，支持跨天续拉。
- 内置每日调用量上限保护（默认 9500 次），达到上限后自动停止并提示次日继续。

调用量估算（工作日近似）：
  CSI300  (2005-04-08 → today):  ~5,520 次
  CSI500  (2007-01-15 → today):  ~5,059 次
  CSI1000 (2014-10-17 → today):  ~3,035 次
  CSI2000 (2023-02-17 → today):    ~860 次
  总计:                          ~14,474 次（单日可跑完，付费版日限 100 万次）
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from . import config as cfg


# ── JoinQuant 懒加载 ──────────────────────────────────────────────────────────

def _get_jq():
    """懒加载 jqdatasdk，调用前必须已经 auth()。"""
    try:
        import jqdatasdk as jq
        return jq
    except ImportError as e:
        raise RuntimeError(
            "需要安装 jqdatasdk: pip install jqdatasdk\n"
            "然后调用 jqdatasdk.auth(username, password) 完成登录。"
        ) from e


def auth(username, password) -> None:
    """登录聚宽账号（每次进程启动前调用一次）。

    username/password 接受 str 或 int（fire 会把纯数字参数解析成 int，
    而 jqdatasdk 的 thrift 协议要求 string，因此此处强制转换）。
    """
    jq = _get_jq()
    jq.auth(str(username), str(password))
    logger.info("JoinQuant auth OK")


def account_info() -> dict:
    """查询当前账号的数据权限范围，返回 {start_date, end_date} 等信息。

    聚宽不同套餐可查询的历史数据范围不同：
      - 免费版/学生版：通常仅近 1 年
      - 付费版：可查 2005 年至今
    本函数用于在 pull 前确认账号能访问的时间窗口，避免无效调用。
    """
    jq = _get_jq()
    try:
        info = jq.get_account_info()
        logger.info(f"账号信息: {info}")
        return info if isinstance(info, dict) else {"raw": str(info)}
    except Exception as e:
        logger.warning(f"get_account_info 失败: {e}")
        return {}


# ── 进度管理 ──────────────────────────────────────────────────────────────────

def _load_progress(index_name: str) -> dict:
    p = cfg.progress_path(index_name)
    if p.exists():
        return json.loads(p.read_text())
    return {"last_date": None, "total_calls": 0}


def _save_progress(index_name: str, last_date: str, total_calls: int) -> None:
    p = cfg.progress_path(index_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "last_date": last_date,
        "total_calls": total_calls,
        "updated_at": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2))


# ── 快照读写 ──────────────────────────────────────────────────────────────────

def load_snapshots(index_name: str) -> pd.DataFrame:
    """读取已缓存的快照，返回 DataFrame(date: str, symbol: str)。"""
    p = cfg.snapshots_path(index_name)
    if not p.exists():
        return pd.DataFrame(columns=["date", "symbol"])
    return pd.read_parquet(p)


def _append_snapshots(index_name: str, new_rows: list[dict]) -> None:
    """追加新快照行到 parquet（原地追加）。"""
    if not new_rows:
        return
    new_df = pd.DataFrame(new_rows)
    existing = load_snapshots(index_name)
    combined = pd.concat([existing, new_df], ignore_index=True)
    p = cfg.snapshots_path(index_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(p, index=False)


# ── 核心拉取逻辑 ──────────────────────────────────────────────────────────────

def pull(
    index_name: str,
    end_date: Optional[str] = None,
    start_date: Optional[str] = None,
    max_calls: int = cfg.DAILY_CALL_LIMIT,
    sleep_sec: float = 0.3,
    batch_size: int = 200,
) -> dict:
    """
    拉取指定指数从 start_date 到 end_date 的每日快照。

    Parameters
    ----------
    index_name : str
        指数名称，如 'csi300'。
    end_date : str, optional
        拉取截止日期（含），格式 'YYYY-MM-DD'。默认为今天。
    start_date : str, optional
        拉取起始日期（含）。默认为指数成立日（或断点续拉的下一天）。
        若账号权限有限（如仅能查近一年），传入权限起始日避免无效调用。
    max_calls : int
        本次会话最多调用 get_index_stocks 的次数。
    sleep_sec : float
        每次 API 调用间的休眠秒数。
    batch_size : int
        每拉取多少天保存一次快照和进度。

    Returns
    -------
    dict
        {"pulled": N, "skipped": M, "stopped_early": bool, "last_date": "YYYY-MM-DD"}
    """
    meta = cfg.INDEX_META[index_name]
    jq = _get_jq()

    end_ts = pd.Timestamp(end_date) if end_date else pd.Timestamp.today().normalize()
    progress = _load_progress(index_name)
    last_done = pd.Timestamp(progress["last_date"]) if progress["last_date"] else None

    # 确定起始日：优先用 start_date 参数，其次断点续拉，最后指数成立日
    if start_date:
        start_ts = pd.Timestamp(start_date)
    elif last_done is not None:
        start_ts = last_done + pd.Timedelta(days=1)
    else:
        start_ts = meta["start_date"]

    # 若有断点且断点比 start_date 更靠后，从断点继续（避免重复拉取）
    if last_done is not None and last_done + pd.Timedelta(days=1) > start_ts:
        start_ts = last_done + pd.Timedelta(days=1)

    if start_ts > end_ts:
        logger.info(f"{index_name}: 已是最新（last_done={last_done.date()}），无需拉取。")
        return {"pulled": 0, "skipped": 0, "stopped_early": False, "last_date": str(last_done.date()) if last_done else None}

    # 获取交易日列表（单次调用）
    logger.info(f"{index_name}: 获取交易日 {start_ts.date()} → {end_ts.date()}")
    trade_days = jq.get_trade_days(
        start_date=start_ts.strftime("%Y-%m-%d"),
        end_date=end_ts.strftime("%Y-%m-%d"),
    )
    if len(trade_days) == 0:
        logger.info(f"{index_name}: 区间内无交易日。")
        return {"pulled": 0, "skipped": 0, "stopped_early": False, "last_date": progress["last_date"]}

    logger.info(f"{index_name}: 共 {len(trade_days)} 个交易日需拉取，max_calls={max_calls}")

    calls_this_session = 0
    pulled = 0
    batch_rows: list[dict] = []
    last_date_str = progress["last_date"]

    for td in trade_days:
        if calls_this_session >= max_calls:
            logger.warning(
                f"{index_name}: 本次会话已达 {max_calls} 次调用上限，停止。"
                f" 明天继续从 {td} 开始。"
            )
            _flush_batch(index_name, batch_rows, last_date_str, progress["total_calls"] + calls_this_session)
            return {
                "pulled": pulled,
                "skipped": 0,
                "stopped_early": True,
                "last_date": last_date_str,
            }

        date_str = str(td)
        try:
            stocks_jq = jq.get_index_stocks(meta["jq_code"], date=date_str)
        except Exception as e:
            logger.warning(f"{index_name} {date_str}: get_index_stocks 失败: {e}，跳过。")
            calls_this_session += 1
            time.sleep(sleep_sec)
            continue

        calls_this_session += 1
        pulled += 1
        last_date_str = date_str

        # 转换成 Qlib 格式并记录
        for jq_sym in stocks_jq:
            try:
                qlib_sym = cfg.jq_to_qlib(jq_sym)
            except Exception:
                qlib_sym = jq_sym  # 保留原始格式，后续可修正
            batch_rows.append({"date": date_str, "symbol": qlib_sym})

        # 每 batch_size 天落盘一次
        if pulled % batch_size == 0:
            _flush_batch(index_name, batch_rows, last_date_str, progress["total_calls"] + calls_this_session)
            batch_rows = []
            logger.info(f"{index_name}: {pulled}/{len(trade_days)} 天已完成（{date_str}）")

        time.sleep(sleep_sec)

    # 最后一批
    _flush_batch(index_name, batch_rows, last_date_str, progress["total_calls"] + calls_this_session)
    logger.info(f"{index_name}: 拉取完成，共 {pulled} 天，本次调用 {calls_this_session} 次。")
    return {
        "pulled": pulled,
        "skipped": 0,
        "stopped_early": False,
        "last_date": last_date_str,
    }


def _flush_batch(index_name: str, batch_rows: list[dict], last_date_str: str | None, total_calls: int) -> None:
    """将当前批次追加到 parquet 并更新进度。"""
    if batch_rows:
        _append_snapshots(index_name, batch_rows)
    if last_date_str:
        _save_progress(index_name, last_date_str, total_calls)


# ── 状态查询 ──────────────────────────────────────────────────────────────────

def status(index_name: str) -> dict:
    """返回当前拉取进度摘要。"""
    meta = cfg.INDEX_META[index_name]
    progress = _load_progress(index_name)
    snap = load_snapshots(index_name)
    covered_days = snap["date"].nunique() if not snap.empty else 0
    today = pd.Timestamp.today().normalize()
    total_needed = len(pd.bdate_range(meta["start_date"], today))  # 工作日近似
    return {
        "index": index_name,
        "start_date": str(meta["start_date"].date()),
        "last_pulled_date": progress["last_date"],
        "covered_trading_days": covered_days,
        "estimated_total_trading_days": total_needed,
        "total_api_calls": progress["total_calls"],
        "is_complete": progress["last_date"] is not None
            and pd.Timestamp(progress["last_date"]) >= today - pd.Timedelta(days=3),
    }


def pull_all(
    index_names: list[str] = cfg.ALL_INDEX_NAMES,
    end_date: Optional[str] = None,
    start_date: Optional[str] = None,
    max_calls: int = cfg.DAILY_CALL_LIMIT,
    sleep_sec: float = 0.3,
) -> dict[str, dict]:
    """
    依次拉取多个指数，共用同一个 max_calls 预算。

    Parameters
    ----------
    start_date : str, optional
        全局起始日期。若账号权限有限（如仅近一年），传入权限起始日。
        各指数的实际起始日 = max(start_date, index_start_date)。
    """
    remaining = max_calls
    results = {}
    ordered = sorted(index_names, key=lambda n: cfg.INDEX_META[n]["expected_size"])
    for name in ordered:
        if remaining <= 0:
            logger.warning(f"调用配额耗尽，{name} 及后续指数留待明日继续。")
            results[name] = {"pulled": 0, "stopped_early": True, "last_date": None}
            continue
        # 各指数起始日 = max(start_date, 指数成立日)
        idx_start = cfg.INDEX_META[name]["start_date"].strftime("%Y-%m-%d")
        effective_start = None
        if start_date:
            effective_start = max(start_date, idx_start)
        else:
            effective_start = idx_start
        r = pull(name, end_date=end_date, start_date=effective_start,
                 max_calls=remaining, sleep_sec=sleep_sec)
        results[name] = r
        consumed = r.get("pulled", 0) + 1
        remaining -= consumed
    return results
