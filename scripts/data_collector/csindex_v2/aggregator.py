"""聚合器：合并三个解析器的输出，规范生效日期，盘点覆盖度。

输出:
  parsed/all_changes.csv   去重后的全量变更（含来源）
  parsed/coverage.txt      每个指数的期次覆盖盘点（定期调样应每年 2 次）

日期语义:
  effective_date = 新名单开始适用的首个交易日。
  非交易日（如推断出的 2009-01-01）向后贴到下一交易日。
"""

from __future__ import annotations

import bisect
from pathlib import Path

import pandas as pd
from loguru import logger

from . import config as cfg

CALENDAR_PATH = Path("~/.qlib/qlib_data/cn_data/calendars/day.txt").expanduser()

# 各指数上线日（roster 重构的时间下界）
INDEX_LAUNCH = {
    "csi300": "2005-04-08",
    "csi500": "2007-01-15",
    "csi1000": "2014-10-17",
    "csi2000": "2023-08-11",
}


def load_calendar() -> list[str]:
    with CALENDAR_PATH.open() as f:
        return [line.strip() for line in f if line.strip()]


def snap_to_trading_day(date: str, calendar: list[str]) -> str:
    """非交易日 → 下一交易日。"""
    i = bisect.bisect_left(calendar, date)
    if i >= len(calendar):
        return date
    return calendar[i]


def load_all() -> pd.DataFrame:
    frames = []
    for name in (
        "content_changes.csv",
        "excel_changes.csv",
        "pdf_changes.csv",
        "tushare_gap_changes.csv",
    ):
        p = cfg.PARSED_DIR / name
        if not p.exists():
            continue
        df = pd.read_csv(p, dtype={"source_id": str})
        df["source"] = name.split("_")[0]
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def apply_manual_fixes(
    changes: pd.DataFrame, fixes: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, int]]:
    """应用人工修正，同时保留公告日与生效日两个日期字段。"""
    changes = changes.copy()
    counts = {"add": 0, "drop": 0, "patch": 0}

    for _, fx in fixes.iterrows():
        key = (
            (changes["index_name"] == fx["index_name"])
            & (changes["symbol"] == fx["symbol"])
            & (changes["type"] == fx["type"])
        )
        if fx["action"] == "drop" and pd.notna(fx.get("effective_date")):
            key = key & (changes["effective_date"] == fx["effective_date"])

        if fx["action"] == "drop":
            counts["drop"] += int(key.sum())
            changes = changes[~key]
        elif fx["action"] == "patch_date":
            changes.loc[key, "effective_date"] = fx["effective_date"]
            if pd.notna(fx.get("announce_date")):
                changes.loc[key, "announce_date"] = fx["announce_date"]
            counts["patch"] += int(key.sum())
        elif fx["action"] == "add":
            announce_date = fx.get("announce_date")
            changes = pd.concat(
                [
                    changes,
                    pd.DataFrame(
                        [
                            {
                                "index_name": fx["index_name"],
                                "symbol": fx["symbol"],
                                "type": fx["type"],
                                "effective_date": fx["effective_date"],
                                "announce_date": (
                                    announce_date if pd.notna(announce_date) else None
                                ),
                                "source_id": None,
                                "method": "manual_fix",
                                "source": "manual",
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
            counts["add"] += 1

    changes = changes.sort_values(
        ["index_name", "effective_date", "type", "symbol"]
    ).reset_index(drop=True)
    return changes, counts


def aggregate() -> pd.DataFrame:
    calendar = load_calendar()
    df = load_all()

    # full 快照单独存放（校验锚点用）
    full = df[df["type"] == "full"].copy()
    changes = df[df["type"].isin(["add", "remove"])].copy()

    # 缺失生效日的记录（多为"自终止上市生效日起"的条件式临时调整）：
    # 用公告日的下一交易日近似，误差通常在数日内；丢弃会导致退市股永久残留
    missing = changes["effective_date"].isna()
    if missing.any():
        logger.info(f"{missing.sum()} 条缺失生效日，用公告日下一交易日近似")
        changes.loc[missing, "effective_date"] = changes.loc[missing, "announce_date"]
        changes.loc[missing, "method"] = changes.loc[missing, "method"].astype(str) + "_approx_date"
    changes = changes.dropna(subset=["effective_date"])
    changes["effective_date"] = changes["effective_date"].map(
        lambda d: snap_to_trading_day(str(d)[:10], calendar)
    )

    # 去重：同一 (指数, 股票, 方向, 生效日) 多来源重复只留一条
    # 来源优先级：excel(官方附件) > pdf > content > tushare(月度差分)
    prio = {"excel": 0, "pdf": 1, "content": 2, "tushare": 3}
    changes["prio"] = changes["source"].map(prio)
    changes = (
        changes.sort_values("prio")
        .drop_duplicates(subset=["index_name", "symbol", "type", "effective_date"])
        .sort_values(["index_name", "effective_date", "type", "symbol"])
        .reset_index(drop=True)
    )

    # 除 csi2000 外仅保留官方来源（content / excel / pdf / manual）
    before = len(changes)
    changes = changes[
        (changes["index_name"] == "csi2000") | (changes["source"] != "tushare")
    ].reset_index(drop=True)
    dropped = before - len(changes)
    if dropped:
        logger.info(f"非 csi2000 指数丢弃 {dropped} 条 tushare 记录（仅 csi2000 使用 tushare）")

    # 软去重：tushare 差分记录若与公告记录同 (指数,股票,方向) 且生效日相差 ≤45 天，
    # 视为同一事件的月度粗化版本，丢弃 tushare 版
    official = changes[changes["source"] != "tushare"]
    official_keys: dict[tuple, list[pd.Timestamp]] = {}
    for _, r in official.iterrows():
        official_keys.setdefault(
            (r["index_name"], r["symbol"], r["type"]), []
        ).append(pd.Timestamp(r["effective_date"]))

    def is_soft_dup(row) -> bool:
        if row["source"] != "tushare":
            return False
        dates = official_keys.get((row["index_name"], row["symbol"], row["type"]))
        if not dates:
            return False
        d = pd.Timestamp(row["effective_date"])
        return any(abs((d - od).days) <= 45 for od in dates)

    mask = changes.apply(is_soft_dup, axis=1)
    if mask.any():
        logger.info(f"软去重: 丢弃 {mask.sum()} 条与公告重叠的 tushare 差分记录")
    changes = changes[~mask].drop(columns=["prio"]).reset_index(drop=True)

    # 冲突检查：同一 (指数, 股票, 生效日) 同时 add 和 remove
    dup = changes.groupby(["index_name", "symbol", "effective_date"])["type"].nunique()
    conflicts = dup[dup > 1]
    if len(conflicts):
        logger.warning(f"同日既调入又调出的冲突记录 {len(conflicts)} 组：\n{conflicts.head(20)}")

    # 人工修正（吸收合并换码、条件式公告缺失等极少数无法自动解析的事件）
    fixes_path = Path(__file__).parent / "manual_fixes.csv"
    if fixes_path.exists():
        fixes = pd.read_csv(fixes_path)
        changes, counts = apply_manual_fixes(changes, fixes)
        logger.info(
            f"人工修正: +{counts['add']} / 改期 {counts['patch']} / "
            f"删除 {counts['drop']}"
        )

    out = cfg.PARSED_DIR / "all_changes.csv"
    changes.to_csv(out, index=False)
    full.to_csv(cfg.PARSED_DIR / "full_lists.csv", index=False)
    logger.info(f"聚合: {len(changes)} 条变更, {len(full)} 条全量名单记录 → {out}")
    return changes


def coverage_report(changes: pd.DataFrame) -> str:
    """盘点每个指数的期次覆盖。定期调样：2005-2013 为 1/7 月第一个交易日，
    2013-06 起为 6/12 月第二个周五的下一交易日。"""
    lines = []
    for idx_name in ("csi300", "csi500", "csi1000", "csi2000"):
        sub = changes[changes["index_name"] == idx_name]
        launch = INDEX_LAUNCH[idx_name]
        lines.append(f"\n===== {idx_name}（{launch} 上线）=====")
        if sub.empty:
            lines.append("  无任何记录！")
            continue

        by_date = sub.groupby("effective_date").agg(
            n_add=("type", lambda s: (s == "add").sum()),
            n_remove=("type", lambda s: (s == "remove").sum()),
            sources=("source", lambda s: ",".join(sorted(set(s)))),
        )
        for date, row in by_date.iterrows():
            flag = ""
            if abs(row.n_add - row.n_remove) > 2:
                flag = "  ← 不平衡!"
            lines.append(
                f"  {date}  +{row.n_add:<4} -{row.n_remove:<4} [{row.sources}]{flag}"
            )

        # 检查定期调样漏期：上线后每年应有 6/7 月和 12/1 月各一次调整
        years = range(int(launch[:4]), 2027)
        eff_dates = list(by_date.index)
        missing = []
        for y in years:
            # 年中调整窗口
            mid_window = [d for d in eff_dates if f"{y}-06" <= d <= f"{y}-07-31"]
            # 年末/次年初调整窗口
            end_window = [d for d in eff_dates if f"{y}-12" <= d <= f"{y + 1}-01-31"]
            mid_start = f"{y}-06-01"
            if mid_start > launch and f"{y}-07-31" < "2026-08" and not mid_window:
                missing.append(f"{y}年中")
            if f"{y}-12-01" > launch and y < 2026 and not end_window:
                missing.append(f"{y}年末")
        if missing:
            lines.append(f"  ⚠ 疑似缺失定期调样: {', '.join(missing)}")
        else:
            lines.append("  定期调样期次完整 ✓")

    report = "\n".join(lines)
    (cfg.PARSED_DIR / "coverage.txt").write_text(report)
    return report


if __name__ == "__main__":
    changes = aggregate()
    print(coverage_report(changes))
