"""Builder：锚点全量名单 + 变更记录 → 每日成分区间 → qlib instruments 文件。

数据源策略（2026-07）：
  csi300   仅官方公告；2005-07-01 生效的全量名单按公告日 2005-06-22 正推，
             2005-04-08 初始名单反推
  csi500   官方公告为主，两条快速纳入由人工审核的 Tushare 月末快照补录；
             自无缺失定期调样的最早一期（2015-12-14 生效）正推
  csi1000  仅官方公告；同上（2015-12-14 生效）
  csi2000  Tushare 月末差分；初始名单按公告日 2023-08-10 正推

输出:
  changes/{index}_intervals.csv
  changes/{index}_instruments.txt
  changes/index_starts.json      各指数覆盖起始日
  changes/build_report.txt
"""

from __future__ import annotations

import bisect
from pathlib import Path

import pandas as pd
from loguru import logger

from . import config as cfg
from .index_starts import (
    CSI300_ANCHOR,
    CSI2000_ANCHOR,
    INDEX_LAUNCH,
    build_index_start_records,
    write_index_starts,
)

CALENDAR_PATH = Path("~/.qlib/qlib_data/cn_data/calendars/day.txt").expanduser()

# instruments 区间日期语义：announce=公告日（缺公告日回退生效日），effective=生效日
DATE_MODE = "announce"


def load_calendar() -> list[str]:
    with CALENDAR_PATH.open() as f:
        return [l.strip() for l in f if l.strip()]


def snap_to_trading_day(date: str, calendar: list[str]) -> str:
    i = bisect.bisect_left(calendar, date)
    if i >= len(calendar):
        return date
    return calendar[i]


def prev_trading_day(date: str, calendar: list[str]) -> str:
    i = bisect.bisect_left(calendar, date)
    return calendar[i - 1] if i > 0 else date


def resolve_event_date(
    announce_date: str | None,
    effective_date: str,
    calendar: list[str],
    date_mode: str = DATE_MODE,
) -> str:
    """按指定口径选择事件日期；公告日缺失时回退到生效日。"""
    if date_mode not in {"announce", "effective"}:
        raise ValueError(f"未知日期口径: {date_mode}")
    use_announce = (
        date_mode == "announce"
        and pd.notna(announce_date)
        and bool(str(announce_date).strip())
    )
    raw = announce_date if use_announce else effective_date
    if pd.isna(raw) or not str(raw).strip():
        raise ValueError("事件缺少可用的公告日和生效日")
    return snap_to_trading_day(str(raw)[:10], calendar)


# ── 锚点名单 ──────────────────────────────────────────────────────────────────

def _normalize_code(code) -> str | None:
    s = str(code).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if not s.isdigit() or len(s) > 6:
        return None
    s = s.zfill(6)
    if s.startswith(("60", "68")):
        return f"SH{s}"
    if s.startswith(("00", "30")):
        return f"SZ{s}"
    if s.startswith(("92", "43", "83", "87", "88", "82")):
        return f"BJ{s}"
    return None


def load_current_snapshot(index_name: str) -> tuple[str, set[str]]:
    """官网当前成分快照。返回 (快照日期, 符号集合)。"""
    code = cfg.INDEX_META[index_name]["code"]
    path = cfg.SNAPSHOTS_DIR / f"{code}cons.xls"
    df = pd.read_excel(path, dtype=str)
    date_raw = str(df.iloc[0, 0])
    date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
    syms = set()
    for c in df.iloc[:, 4]:
        s = _normalize_code(c)
        if s:
            syms.add(s)
    return date, syms


def load_csi300_anchor(calendar: list[str]) -> tuple[str, set[str]]:
    """首次全量名单；锚点日期服从当前公告日/生效日口径。"""
    full = pd.read_csv(cfg.PARSED_DIR / "full_lists.csv", dtype={"source_id": str})
    sub = full[(full["index_name"] == "csi300") & (full["source_id"] == "6773")]
    assert len(sub) == 300, f"csi300 锚点名单应为 300 只，实际 {len(sub)}"
    anchor_date = resolve_event_date(
        CSI300_ANCHOR["announce_date"],
        CSI300_ANCHOR["effective_date"],
        calendar,
    )
    return anchor_date, set(sub["symbol"])


def load_csi2000_anchor(calendar: list[str]) -> tuple[str, set[str]]:
    """发布时的初始 2000 只；锚点日期服从当前日期口径。"""
    path = cfg.FILES_DIR / "20230810_14883_中证2000指数.xlsx"
    df = pd.read_excel(path, dtype=str)
    syms = set()
    for c in df.iloc[:, 3]:
        s = _normalize_code(c)
        if s:
            syms.add(s)
    assert len(syms) == 2000, f"csi2000 初始名单应为 2000 只，实际 {len(syms)}"
    anchor_date = resolve_event_date(
        CSI2000_ANCHOR["announce_date"],
        CSI2000_ANCHOR["effective_date"],
        calendar,
    )
    return anchor_date, syms


# ── 变更记录 ──────────────────────────────────────────────────────────────────

def load_changes(index_name: str, calendar: list[str]) -> pd.DataFrame:
    df = pd.read_csv(cfg.PARSED_DIR / "all_changes.csv", dtype={"source_id": str})
    df = df[df["index_name"] == index_name].copy()
    df["event_date"] = [
        resolve_event_date(ann, eff, calendar)
        for ann, eff in zip(df["announce_date"], df["effective_date"])
    ]
    return df.sort_values(["event_date", "type"]).reset_index(drop=True)



# ── 正推 ─────────────────────────────────────────────────────────────────────

def replay_forward(
    anchor_date: str,
    anchor_set: set[str],
    changes: pd.DataFrame,
    expected: int,
    report: list[str],
) -> list[tuple[str, set[str]]]:
    """从锚点正推。返回 [(事件日, 该日起的 roster), ...]（含锚点）。"""
    rosters = [(anchor_date, set(anchor_set))]
    current = set(anchor_set)
    for eff, grp in changes[changes["event_date"] > anchor_date].groupby("event_date"):
        added = set(grp[grp["type"] == "add"]["symbol"])
        removed = set(grp[grp["type"] == "remove"]["symbol"])
        ghost_remove = removed - current
        dup_add = added & current
        if ghost_remove:
            report.append(f"  {eff}: 调出但不在名单中 {sorted(ghost_remove)}")
        if dup_add:
            report.append(f"  {eff}: 调入但已在名单中 {sorted(dup_add)}")
        current = (current - removed) | added
        if expected and abs(len(current) - expected) > max(2, expected * 0.01):
            report.append(f"  {eff}: roster 数量异常 {len(current)} (期望 {expected})")
        rosters.append((eff, set(current)))
    return rosters


def replay_backward(
    anchor_date: str,
    anchor_set: set[str],
    changes: pd.DataFrame,
    trust_start: str,
    expected: int,
    report: list[str],
) -> list[tuple[str, set[str]]]:
    """从当前快照反推到 trust_start，再正序重建 roster 序列。"""
    current = set(anchor_set)
    subset = changes[
        (changes["event_date"] <= anchor_date)
        & (changes["event_date"] > trust_start)
    ]
    for eff, grp in sorted(subset.groupby("event_date"), key=lambda x: x[0], reverse=True):
        added = set(grp[grp["type"] == "add"]["symbol"])
        removed = set(grp[grp["type"] == "remove"]["symbol"])
        ghost_add = added - current
        overlap_removed = removed & current
        if ghost_add:
            report.append(f"  {eff}: 调入记录但当前名单中无 {sorted(ghost_add)}")
        if overlap_removed:
            report.append(f"  {eff}: 调出记录但仍在后续名单中 {sorted(overlap_removed)}")
        current = (current - added) | removed
        if expected and abs(len(current) - expected) > max(2, expected * 0.01):
            report.append(f"  {eff}(反推前一期): roster 数量异常 {len(current)} (期望 {expected})")
    hist_dates = sorted(subset["event_date"].unique())
    rosters = [(trust_start, set(current))]
    cur = set(current)
    for eff in hist_dates:
        grp = subset[subset["event_date"] == eff]
        added = set(grp[grp["type"] == "add"]["symbol"])
        removed = set(grp[grp["type"] == "remove"]["symbol"])
        cur = (cur - removed) | added
        rosters.append((eff, set(cur)))
    return rosters


# ── roster 序列 → 成员区间 → instruments ─────────────────────────────────────

def rosters_to_intervals(
    rosters: list[tuple[str, set[str]]],
    calendar: list[str],
    end_open_date: str,
) -> pd.DataFrame:
    """相邻 roster 差分出每只股票的 [start, end] 区间。"""
    intervals: list[dict] = []
    open_since: dict[str, str] = {}

    prev_set: set[str] = set()
    for eff, roster in rosters:
        added = roster - prev_set
        removed = prev_set - roster
        for s in removed:
            intervals.append({
                "symbol": s,
                "start": open_since.pop(s),
                "end": prev_trading_day(eff, calendar),
            })
        for s in added:
            open_since[s] = eff
        prev_set = roster

    for s, start in open_since.items():
        intervals.append({"symbol": s, "start": start, "end": end_open_date})

    return pd.DataFrame(intervals).sort_values(["symbol", "start"]).reset_index(drop=True)


def write_instruments(index_name: str, intervals: pd.DataFrame) -> Path:
    dest = cfg.CHANGES_DIR / f"{index_name}_instruments.txt"
    with dest.open("w") as f:
        for _, r in intervals.iterrows():
            f.write(f"{r['symbol']}\t{r['start']}\t{r['end']}\n")
    return dest


# ── 主流程 ────────────────────────────────────────────────────────────────────

def build_index(
    index_name: str,
    calendar: list[str],
    report: list[str],
    start_meta: dict,
) -> None:
    expected = cfg.INDEX_META[index_name]["expected_size"]
    snap_date, snap_set = load_current_snapshot(index_name)
    changes = load_changes(index_name, calendar)
    date_label = "公告日" if DATE_MODE == "announce" else "生效日"
    coverage_start = start_meta["coverage_start"]
    report.append(f"\n===== {index_name}（额定 {expected} 只，当前快照 {snap_date}）=====")
    report.append(f"  数据源: {start_meta['data_source']}")
    report.append(f"  覆盖起始 ({date_label}): {coverage_start}")
    if start_meta.get("gap_before_start"):
        report.append(f"  起始前缺口: {start_meta['gap_before_start']}")
    report.append(
        f"  变更记录: {len(changes)} 条, "
        f"{changes['event_date'].min()} ~ {changes['event_date'].max()}（{date_label}）"
    )

    if index_name == "csi300":
        anchor_date, anchor_set = load_csi300_anchor(calendar)
        rosters = replay_forward(anchor_date, anchor_set, changes, expected, report)
        g = changes[changes["effective_date"] == "2005-07-01"]
        initial = (anchor_set - set(g[g["type"] == "add"]["symbol"])) | set(
            g[g["type"] == "remove"]["symbol"]
        )
        report.append(f"  2005-04-08 初始名单反推: {len(initial)} 只")
        launch = INDEX_LAUNCH[index_name]
        if launch < anchor_date:
            rosters = [(launch, initial)] + rosters
    elif index_name == "csi2000":
        anchor_date, anchor_set = load_csi2000_anchor(calendar)
        rosters = replay_forward(anchor_date, anchor_set, changes, expected, report)
    else:
        # csi500 / csi1000：从官方连续覆盖起点反推，再正序重建
        start_event = snap_to_trading_day(coverage_start, calendar)
        rosters = replay_backward(
            snap_date, snap_set, changes, start_event, expected, report
        )
        report.append(f"  起始 roster（{start_event} 生效后）: {len(rosters[0][1])} 只")

    final_set = rosters[-1][1]
    only_built = final_set - snap_set
    only_snap = snap_set - final_set
    if only_built or only_snap:
        report.append(
            f"  ✗ 终局与官网快照不一致: 多出 {sorted(only_built)[:10]}"
            f"{'...' if len(only_built) > 10 else ''} / 缺少 {sorted(only_snap)[:10]}"
            f"{'...' if len(only_snap) > 10 else ''}"
            f" (多{len(only_built)}/缺{len(only_snap)})"
        )
    else:
        report.append(f"  ✓ 终局 roster 与官网快照完全一致（{len(final_set)} 只）")

    intervals = rosters_to_intervals(rosters, calendar, end_open_date="2099-12-31")
    dest = write_instruments(index_name, intervals)
    intervals.to_csv(cfg.CHANGES_DIR / f"{index_name}_intervals.csv", index=False)
    report.append(f"  区间 {len(intervals)} 条 → {dest.name}")


def build_all() -> str:
    cfg.ensure_dirs()
    calendar = load_calendar()
    all_changes = pd.read_csv(
        cfg.PARSED_DIR / "all_changes.csv", dtype={"source_id": str}
    )
    starts = build_index_start_records(all_changes, DATE_MODE)
    write_index_starts(all_changes, DATE_MODE)

    report: list[str] = [
        "构建报告",
        f"区间日期语义: {'公告日（缺公告日回退生效日）' if DATE_MODE == 'announce' else '生效日'}",
        "数据源: csi300/1000=仅官方公告, "
        "csi500=官方公告+2条人工审核Tushare月末快照, csi2000=Tushare",
        f"起始日配置 → {cfg.CHANGES_DIR / 'index_starts.json'}",
    ]
    for index_name in ("csi300", "csi500", "csi1000", "csi2000"):
        try:
            build_index(index_name, calendar, report, starts[index_name])
        except Exception as e:
            logger.exception(f"{index_name} 构建失败")
            report.append(f"\n===== {index_name} =====\n  ✗ 构建失败: {e}")

    text = "\n".join(report)
    (cfg.CHANGES_DIR / "build_report.txt").write_text(text)
    logger.info(f"报告 → {cfg.CHANGES_DIR / 'build_report.txt'}")
    return text


if __name__ == "__main__":
    print(build_all())
