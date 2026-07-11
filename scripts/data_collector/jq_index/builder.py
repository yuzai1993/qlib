"""从每日快照推导成分股变动，构建 Qlib instruments.txt。

instruments.txt 格式（制表符分隔）：
    SH600000\t2005-04-08\t2026-06-02
    SH600004\t2018-12-28\t2021-07-01
    ...

每行含义：symbol 在 [start_date, end_date) 区间内持续在指数中。
end_date 为"退出后次日"，即最后一个在册日的次交易日。
当 end_date 为今日或之后，表示当前仍在指数中，通常写成今日 + 1。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from . import config as cfg
from .puller import load_snapshots


# ── 核心推导逻辑 ──────────────────────────────────────────────────────────────

def snapshots_to_changes(snapshots: pd.DataFrame) -> pd.DataFrame:
    """
    从每日快照推导 add/remove 事件。

    Parameters
    ----------
    snapshots : DataFrame
        columns: date(str), symbol(str)

    Returns
    -------
    DataFrame
        columns: symbol(str), type(str 'add'|'remove'), date(str)
        date 为该变动生效的第一天（add 当天即生效，remove 当天已不在册）。
    """
    if snapshots.empty:
        return pd.DataFrame(columns=["symbol", "type", "date"])

    # 按日期排序，枚举相邻两日的差集
    dates = sorted(snapshots["date"].unique())
    records: list[dict] = []

    prev_set: set[str] = set()
    for i, d in enumerate(dates):
        cur_set = set(snapshots.loc[snapshots["date"] == d, "symbol"])
        if i == 0:
            # 第一天：所有成分视为 add
            for sym in sorted(cur_set):
                records.append({"symbol": sym, "type": "add", "date": d})
        else:
            added = cur_set - prev_set
            removed = prev_set - cur_set
            for sym in sorted(added):
                records.append({"symbol": sym, "type": "add", "date": d})
            for sym in sorted(removed):
                records.append({"symbol": sym, "type": "remove", "date": d})
        prev_set = cur_set

    return pd.DataFrame(records)


def changes_to_instruments(
    changes: pd.DataFrame,
    last_snapshot_date: str,
    next_trading_day: Optional[str] = None,
) -> pd.DataFrame:
    """
    将 add/remove 事件流转换为 instruments 格式（每行是连续在册区间）。

    Parameters
    ----------
    changes : DataFrame
        columns: symbol, type, date
    last_snapshot_date : str
        快照最后一天（'YYYY-MM-DD'），仍在册的股票 end_date 设为次日。
    next_trading_day : str, optional
        last_snapshot_date 后的下一个交易日。若为 None，使用 last_snapshot_date + 1 calendar day。

    Returns
    -------
    DataFrame
        columns: symbol(str), start_date(str), end_date(str)
    """
    if changes.empty:
        return pd.DataFrame(columns=["symbol", "start_date", "end_date"])

    last_ts = pd.Timestamp(last_snapshot_date)
    if next_trading_day:
        end_sentinel = next_trading_day
    else:
        # 粗略用 +1 日，后续可由 builder 传入精确的下一交易日
        end_sentinel = str((last_ts + pd.Timedelta(days=1)).date())

    changes = changes.sort_values(["symbol", "date"]).reset_index(drop=True)
    records: list[dict] = []

    for sym, grp in changes.groupby("symbol"):
        grp = grp.sort_values("date")
        open_start: str | None = None

        for _, row in grp.iterrows():
            if row["type"] == "add":
                if open_start is not None:
                    # 理论上不应连续两次 add，记录异常但不中断
                    logger.warning(f"{sym}: 连续两次 add，忽略第二次 add (date={row['date']})")
                    continue
                open_start = row["date"]
            elif row["type"] == "remove":
                if open_start is None:
                    # 没有对应 add 的 remove（可能在快照第一天之前就在册）
                    logger.debug(f"{sym}: remove 前无 add 记录 (date={row['date']})")
                    continue
                records.append({
                    "symbol": sym,
                    "start_date": open_start,
                    "end_date": row["date"],
                })
                open_start = None

        # 仍在册（未见 remove）
        if open_start is not None:
            records.append({
                "symbol": sym,
                "start_date": open_start,
                "end_date": end_sentinel,
            })

    return pd.DataFrame(records)


# ── instruments.txt 写出 ──────────────────────────────────────────────────────

def write_instruments(instruments: pd.DataFrame, output_path: Path) -> None:
    """写出 instruments.txt，制表符分隔，无表头。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for _, row in instruments.iterrows():
        lines.append(f"{row['symbol']}\t{row['start_date']}\t{row['end_date']}")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"写出 {len(lines)} 行 → {output_path}")


# ── 一站式构建入口 ────────────────────────────────────────────────────────────

def build(
    index_name: str,
    output_suffix: str = "_jq",
    next_trading_day: Optional[str] = None,
) -> pd.DataFrame:
    """
    从已缓存的快照构建 instruments.txt。

    Parameters
    ----------
    index_name : str
        指数名称，如 'csi300'。
    output_suffix : str
        输出文件名后缀，默认 '_jq'（即 csi300_jq.txt）。
        设为 '' 则直接覆盖 csi300.txt（谨慎使用）。
    next_trading_day : str, optional
        快照最后一天的下一个交易日，用于设置"仍在册"股票的 end_date。

    Returns
    -------
    DataFrame
        instruments 内容（symbol, start_date, end_date）。
    """
    snapshots = load_snapshots(index_name)
    if snapshots.empty:
        logger.error(f"{index_name}: 快照为空，请先运行 pull。")
        return pd.DataFrame(columns=["symbol", "start_date", "end_date"])

    last_date = snapshots["date"].max()
    logger.info(f"{index_name}: 快照覆盖到 {last_date}，共 {snapshots['date'].nunique()} 个交易日")

    changes = snapshots_to_changes(snapshots)
    logger.info(f"{index_name}: 检测到 {len(changes)} 条变动事件")

    instruments = changes_to_instruments(changes, last_date, next_trading_day)
    logger.info(f"{index_name}: 生成 {len(instruments)} 条在册区间")

    # 校验
    _validate_instruments(index_name, instruments, snapshots, last_date)

    output_path = cfg.instruments_output_path(index_name, output_suffix)
    write_instruments(instruments, output_path)
    return instruments


def build_all(
    index_names: list[str] = cfg.ALL_INDEX_NAMES,
    output_suffix: str = "_jq",
) -> dict[str, pd.DataFrame]:
    results = {}
    for name in index_names:
        results[name] = build(name, output_suffix=output_suffix)
    return results


# ── 简单校验 ──────────────────────────────────────────────────────────────────

def _validate_instruments(
    index_name: str,
    instruments: pd.DataFrame,
    snapshots: pd.DataFrame,
    last_date: str,
) -> None:
    """四项基础校验，不通过只记录警告，不抛出异常。"""
    meta = cfg.INDEX_META[index_name]
    expected = meta["expected_size"]

    # C1：最后一日的在册数 ≈ 标称数
    last_day_snap = set(snapshots.loc[snapshots["date"] == last_date, "symbol"])
    active = instruments[
        (instruments["start_date"] <= last_date) & (instruments["end_date"] > last_date)
    ]
    active_syms = set(active["symbol"])
    if active_syms != last_day_snap:
        missing = last_day_snap - active_syms
        extra = active_syms - last_day_snap
        logger.warning(
            f"[C1] {index_name} 最终在册与快照不一致："
            f" missing={len(missing)}, extra={len(extra)}"
        )
        if missing:
            logger.warning(f"  missing: {sorted(missing)[:10]}")
        if extra:
            logger.warning(f"  extra:   {sorted(extra)[:10]}")
    else:
        logger.info(f"[C1] {index_name} 最终在册 {len(active_syms)} 只，✓")

    # C2：最终在册数 ≈ 标称数（±5）
    diff = abs(len(active_syms) - expected)
    if diff > 5:
        logger.warning(f"[C2] {index_name} 在册数 {len(active_syms)} 与标称 {expected} 差异 {diff} > 5")
    else:
        logger.info(f"[C2] {index_name} 在册数 {len(active_syms)} ≈ 标称 {expected}，✓")

    # C3：同一股票不应有连续同类事件
    changes = snapshots_to_changes(snapshots)
    bad_c3 = []
    for sym, grp in changes.groupby("symbol"):
        grp = grp.sort_values("date")
        types = grp["type"].tolist()
        for j in range(1, len(types)):
            if types[j] == types[j - 1]:
                bad_c3.append((sym, types[j], grp.iloc[j]["date"]))
    if bad_c3:
        logger.warning(f"[C3] {index_name} 有 {len(bad_c3)} 条连续同类事件（前5条）: {bad_c3[:5]}")
    else:
        logger.info(f"[C3] {index_name} 无连续同类事件，✓")
