"""校验新构建的事件流、成分区间、终局快照与 CSI300 旧缓存。

旧缓存格式：
  csi300_changes_YYYYMMDD.csv   列 [_, symbol, type, date]（date=生效前一交易日）
  csi300_changes_YYYYMMDD.xls*  中证官网原始附件（指数代码/简称/证券代码/简称，
                                含全部衍生指数，需过滤 000300）

对齐口径：
  旧 CSV 的 date 是"旧名单最后一天"，新记录的 effective_date 是"新名单首日"，
  校验时允许 ±5 个交易日的窗口匹配。
"""

from __future__ import annotations

from pathlib import Path
import re

import pandas as pd
from loguru import logger

from . import config as cfg
from .builder import load_calendar, load_current_snapshot

LEGACY_DIR = Path("~/.cache/qlib/index/CSI300").expanduser()
SYMBOL_RE = re.compile(r"^(SH|SZ|BJ)\d{6}$")


def load_legacy_changes() -> pd.DataFrame:
    """旧缓存的全部变更（CSV 直接读，XLS 过滤指数代码=000300）。"""
    frames = []
    for p in sorted(LEGACY_DIR.glob("csi300_changes_*.csv")):
        df = pd.read_csv(p)
        df = df[["symbol", "type", "date"]].copy()
        df["file"] = p.name
        frames.append(df)
    for p in sorted(LEGACY_DIR.glob("csi300_changes_*.xls*")):
        try:
            raw = pd.read_excel(p, header=None, dtype=str)
        except Exception as e:
            logger.warning(f"{p.name}: {e}")
            continue
        # 双 sheet（调入/调出）或单 sheet 双栏；旧附件与我们 excel 解析器同源，
        # 简化处理：读所有 sheet，按 X1/X2 结构过滤 000300
        xl = pd.ExcelFile(p)
        recs = []
        for sh in xl.sheet_names:
            if "备选" in str(sh):
                continue
            df = xl.parse(sh, header=None, dtype=str)
            if df.empty:
                continue
            direction = "add" if "调入" in str(sh) else ("remove" if "调出" in str(sh) else None)
            # 找表头结构
            for ri in range(len(df)):
                row = df.iloc[ri].tolist()
                idx_code = str(row[0]).strip() if row else ""
                if idx_code != "000300":
                    continue
                if direction is not None:
                    # X1: [指数代码, 指数简称, 证券代码, 证券简称]
                    code = str(row[2]).strip().zfill(6)
                    recs.append({"code": code, "type": direction})
                else:
                    # X2: [指数代码, 简称, 调出代码, 调出名, 调入代码, 调入名]
                    if len(row) >= 3 and str(row[2]).strip().replace(".0", "").isdigit():
                        recs.append({"code": str(row[2]).strip().replace(".0", "").zfill(6), "type": "remove"})
                    if len(row) >= 5 and str(row[4]).strip().replace(".0", "").isdigit():
                        recs.append({"code": str(row[4]).strip().replace(".0", "").zfill(6), "type": "add"})
        if not recs:
            continue
        df = pd.DataFrame(recs)
        df["symbol"] = df["code"].map(
            lambda c: f"SH{c}" if c.startswith(("60", "68")) else f"SZ{c}"
        )
        # 文件名日期 = 生效日
        date_str = p.stem.split("_")[-1]
        df["date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        df["file"] = p.name
        frames.append(df[["symbol", "type", "date", "file"]])
    out = pd.concat(frames, ignore_index=True)
    return out


def change_errors(changes: pd.DataFrame, calendar: list[str]) -> list[str]:
    """校验聚合事件流的关键字段、唯一性与交易日。"""
    required = {"index_name", "symbol", "type", "effective_date", "source"}
    missing = required - set(changes.columns)
    if missing:
        return [f"变更流缺少字段 {sorted(missing)}"]

    errors: list[str] = []
    critical_nulls = changes[list(required)].isna().sum()
    if critical_nulls.sum():
        errors.append(
            "关键字段为空 "
            + ", ".join(f"{name}={int(count)}" for name, count in critical_nulls.items() if count)
        )

    invalid_symbols = ~changes["symbol"].astype(str).map(
        lambda s: bool(SYMBOL_RE.fullmatch(s))
    )
    if invalid_symbols.any():
        errors.append(f"非法证券代码 {int(invalid_symbols.sum())} 条")

    invalid_indices = ~changes["index_name"].isin(
        {"csi300", "csi500", "csi1000", "csi2000"}
    )
    if invalid_indices.any():
        errors.append(f"非法指数名 {int(invalid_indices.sum())} 条")
    invalid_types = ~changes["type"].isin({"add", "remove"})
    if invalid_types.any():
        errors.append(f"非法变更类型 {int(invalid_types.sum())} 条")

    duplicate_keys = changes.duplicated(
        ["index_name", "symbol", "type", "effective_date"]
    )
    if duplicate_keys.any():
        errors.append(f"重复事件键 {int(duplicate_keys.sum())} 条")

    conflicts = changes.groupby(
        ["index_name", "symbol", "effective_date"]
    )["type"].nunique()
    if (conflicts > 1).any():
        errors.append(f"同日调入调出冲突 {int((conflicts > 1).sum())} 组")

    non_trading = ~changes["effective_date"].astype(str).isin(set(calendar))
    if non_trading.any():
        errors.append(f"生效日非交易日 {int(non_trading.sum())} 条")

    wrong_source = (changes["index_name"] != "csi2000") & (
        changes["source"] == "tushare"
    )
    if wrong_source.any():
        errors.append(f"非 csi2000 使用 Tushare {int(wrong_source.sum())} 条")
    return errors


def interval_errors(
    intervals: pd.DataFrame,
    calendar: list[str],
    snapshot_date: str,
    snapshot_set: set[str],
) -> list[str]:
    """校验单个指数的区间结构与终局快照。"""
    required = {"symbol", "start", "end"}
    missing = required - set(intervals.columns)
    if missing:
        return [f"缺少字段 {sorted(missing)}"]

    errors: list[str] = []
    df = intervals[list(required)].astype(str).copy()
    invalid_symbols = ~df["symbol"].map(lambda s: bool(SYMBOL_RE.fullmatch(s)))
    if invalid_symbols.any():
        errors.append(f"非法证券代码 {int(invalid_symbols.sum())} 条")
    if df.duplicated().any():
        errors.append(f"完全重复区间 {int(df.duplicated().sum())} 条")

    bad_order = pd.to_datetime(df["start"]) > pd.to_datetime(df["end"])
    if bad_order.any():
        errors.append(f"起止日期倒置 {int(bad_order.sum())} 条")

    calendar_set = set(calendar)
    bad_start = ~df["start"].isin(calendar_set)
    bad_end = ~df["end"].isin(calendar_set | {"2099-12-31"})
    if bad_start.any():
        errors.append(f"起始日非交易日 {int(bad_start.sum())} 条")
    if bad_end.any():
        errors.append(f"结束日非交易日 {int(bad_end.sum())} 条")

    overlaps = 0
    for _, group in df.sort_values(["symbol", "start"]).groupby("symbol"):
        starts = group["start"].tolist()
        ends = group["end"].tolist()
        overlaps += sum(start <= prev_end for prev_end, start in zip(ends, starts[1:]))
    if overlaps:
        errors.append(f"区间重叠 {overlaps} 处")

    active = set(
        df[(df["start"] <= snapshot_date) & (df["end"] >= snapshot_date)]["symbol"]
    )
    if active != snapshot_set:
        errors.append(
            f"终局快照不一致：多 {len(active - snapshot_set)} / "
            f"缺 {len(snapshot_set - active)}"
        )
    return errors


def validate_built_outputs() -> tuple[list[str], list[str]]:
    """校验四个 instruments 产物，并返回报告行与错误列表。"""
    calendar = load_calendar()
    lines = ["构建产物校验"]
    errors: list[str] = []
    frames: dict[str, pd.DataFrame] = {}

    changes = pd.read_csv(cfg.PARSED_DIR / "all_changes.csv", dtype={"source_id": str})
    event_issues = change_errors(changes, calendar)
    if event_issues:
        errors.extend(f"all_changes: {issue}" for issue in event_issues)
        lines.append(f"  ✗ all_changes: {'；'.join(event_issues)}")
    else:
        lines.append("  ✓ all_changes: 关键字段、事件键、交易日和来源策略有效")

    for index_name in ("csi300", "csi500", "csi1000", "csi2000"):
        intervals = pd.read_csv(cfg.CHANGES_DIR / f"{index_name}_intervals.csv", dtype=str)
        frames[index_name] = intervals
        snapshot_date, snapshot_set = load_current_snapshot(index_name)
        issues = interval_errors(intervals, calendar, snapshot_date, snapshot_set)
        if issues:
            errors.extend(f"{index_name}: {issue}" for issue in issues)
            lines.append(f"  ✗ {index_name}: {'；'.join(issues)}")
        else:
            lines.append(f"  ✓ {index_name}: 区间结构与终局快照一致")

    csi300 = frames["csi300"]
    csi300_bad_counts = []
    for date in calendar:
        if date < csi300["start"].min() or date > "2026-07-06":
            continue
        count = int(((csi300["start"] <= date) & (csi300["end"] >= date)).sum())
        if count != 300:
            csi300_bad_counts.append((date, count))
    if csi300_bad_counts:
        first, last = csi300_bad_counts[0], csi300_bad_counts[-1]
        message = f"csi300: 成员数异常 {len(csi300_bad_counts)} 日（{first} ~ {last}）"
        errors.append(message)
        lines.append(f"  ✗ {message}")
    else:
        lines.append("  ✓ csi300: 全覆盖期每个交易日均为 300 只")

    csi1000_600223 = frames["csi1000"]
    csi1000_600223 = csi1000_600223[csi1000_600223["symbol"] == "SH600223"]
    expected_interval = (
        (csi1000_600223["start"] == "2020-07-03")
        & (csi1000_600223["end"] == "2026-05-28")
    )
    if not expected_interval.any():
        message = "csi1000: SH600223 在 2026 调出前缺少连续成员区间"
        errors.append(message)
        lines.append(f"  ✗ {message}")
    else:
        lines.append("  ✓ csi1000: SH600223 调入/调出链闭合")

    csi2000_start = frames["csi2000"]["start"].min()
    if csi2000_start != "2023-08-10":
        message = f"csi2000: 首个区间为 {csi2000_start}，期望 2023-08-10"
        errors.append(message)
        lines.append(f"  ✗ {message}")
    else:
        lines.append("  ✓ csi2000: 首个区间为公告日 2023-08-10")

    lines.insert(1, "状态: PASS" if not errors else "状态: FAIL")
    return lines, errors


def validate() -> str:
    new = pd.read_csv(cfg.PARSED_DIR / "all_changes.csv")
    new = new[(new["index_name"] == "csi300") & (new["type"].isin(["add", "remove"]))]
    legacy = load_legacy_changes()

    lines = [f"旧缓存记录: {len(legacy)} 条（{legacy['date'].min()} ~ {legacy['date'].max()}）",
             f"新记录(csi300): {len(new)} 条"]

    new_keys = {}
    for _, r in new.iterrows():
        new_keys.setdefault((r["symbol"], r["type"]), []).append(pd.Timestamp(r["effective_date"]))

    matched = 0
    missing = []
    for _, r in legacy.iterrows():
        d = pd.Timestamp(r["date"])
        cands = new_keys.get((r["symbol"], r["type"]), [])
        if any(abs((d - c).days) <= 10 for c in cands):
            matched += 1
        else:
            missing.append((r["date"], r["symbol"], r["type"], r["file"]))

    lines.append(f"旧记录在新数据中匹配: {matched}/{len(legacy)} ({matched / len(legacy):.1%})")
    if missing:
        lines.append(f"未匹配 {len(missing)} 条:")
        for m in missing[:40]:
            lines.append(f"  {m[0]}  {m[1]}  {m[2]:<7} ({m[3]})")

    # 反向：定期调样窗口内新记录是否被旧记录覆盖（旧缓存只有定期调样）
    legacy_dates = sorted(set(legacy["date"]))
    lines.append(f"\n旧缓存覆盖的调样期数: {len(legacy_dates)}")

    built_lines, _ = validate_built_outputs()
    lines.extend(["", *built_lines])
    report = "\n".join(lines)
    (cfg.PARSED_DIR / "legacy_validation.txt").write_text(report)
    return report


if __name__ == "__main__":
    report = validate()
    print(report)
    if "状态: FAIL" in report:
        raise SystemExit(1)
