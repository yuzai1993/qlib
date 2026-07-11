"""与旧缓存（~/.cache/qlib/index/CSI*/）的交叉校验。

旧缓存格式（每个调样事件一个文件）：
  ~/.cache/qlib/index/CSI300/csi300_changes_YYYYMMDD.{csv,xls,xlsx}

CSV 格式：(index, symbol, type, date)
XLS/XLSX 格式：中证官网原始下载件，需解析"调入"/"调出"两类样本

校验逻辑：
  对每个旧缓存文件，找到同一日期的 JQ 变动记录，比较 add/remove 集合。
  输出三类差异：
    - only_in_legacy : 旧缓存有、JQ 无
    - only_in_jq     : JQ 有、旧缓存无
    - agreement_rate : 一致率
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from . import config as cfg
from .builder import snapshots_to_changes
from .puller import load_snapshots


# ── 旧缓存加载 ────────────────────────────────────────────────────────────────

def _normalize_symbol_from_legacy(raw: str) -> str:
    """
    旧缓存中的证券代码格式可能是：
      SZ000001, SH600000 (已规范)  →  直接返回
      000001.SZ, 600000.SH        →  转换
      000001                      →  按首位数字推断
    """
    raw = str(raw).strip()
    if raw.startswith(("SH", "SZ")):
        return raw
    if "." in raw:
        code, ex = raw.upper().split(".", 1)
        return ("SH" if ex == "SH" else "SZ") + code
    # 纯数字：按首位推断
    code = raw.zfill(6)
    if code.startswith(("60", "68", "51", "11", "58")):
        return "SH" + code
    return "SZ" + code


def _parse_csv_changes(filepath: Path) -> pd.DataFrame:
    """解析 CSV 格式的旧缓存文件。"""
    df = pd.read_csv(filepath, index_col=0)
    # 列名可能是 symbol/type/date 或带 index 前缀
    df.columns = [c.lower().strip() for c in df.columns]
    if "symbol" not in df.columns:
        logger.warning(f"无 symbol 列: {filepath}")
        return pd.DataFrame()
    df["symbol"] = df["symbol"].apply(_normalize_symbol_from_legacy)
    # type 列：可能是 "add"/"remove" 或 "调入"/"调出"
    if "type" in df.columns:
        df["type"] = df["type"].str.lower().replace({"调入": "add", "调出": "remove", "add_new": "add"})
    return df[["symbol", "type", "date"]].dropna()


def _parse_excel_changes(filepath: Path) -> pd.DataFrame:
    """
    解析中证官网原始 Excel 格式（.xls / .xlsx）。

    中证官网调样 Excel 通常有两张 sheet 或在一张 sheet 中分两个表：
      - 调入样本（新增）
      - 调出样本（剔除）
    列名可能是：证券代码、证券简称、交易所等。
    日期从文件名提取。
    """
    date_match = re.search(r"(\d{8})", filepath.stem)
    if not date_match:
        logger.warning(f"无法从文件名提取日期: {filepath.name}")
        return pd.DataFrame()
    file_date = pd.Timestamp(date_match.group(1)).strftime("%Y-%m-%d")

    records: list[dict] = []
    try:
        xl = pd.ExcelFile(filepath, engine="xlrd" if filepath.suffix == ".xls" else "openpyxl")
    except Exception as e:
        logger.warning(f"打开 Excel 失败 {filepath.name}: {e}")
        return pd.DataFrame()

    for sheet_name in xl.sheet_names:
        try:
            df = xl.parse(sheet_name, header=None)
        except Exception:
            continue

        # 找到含有证券代码列的行（第一列是纯 6 位数字字符串）
        # 策略：扫描每行，找到看起来像证券代码的内容
        action = _guess_action_from_sheet_name(sheet_name)
        for _, row in df.iterrows():
            val = str(row.iloc[0]).strip() if len(row) > 0 else ""
            # 跳过表头行
            if not re.match(r"^\d{6}$", val):
                continue
            sym = _normalize_symbol_from_legacy(val)
            records.append({"symbol": sym, "type": action, "date": file_date})

    if not records:
        logger.debug(f"Excel {filepath.name} 未解析到任何记录")
        return pd.DataFrame()

    return pd.DataFrame(records)


def _guess_action_from_sheet_name(sheet_name: str) -> str:
    """根据 sheet 名推断 add/remove。"""
    s = sheet_name.lower()
    if any(k in s for k in ["调入", "新增", "纳入", "add", "in"]):
        return "add"
    if any(k in s for k in ["调出", "剔除", "移出", "remove", "out"]):
        return "remove"
    # 默认：add（通常第一个 sheet 是调入）
    return "add"


def load_legacy_changes(index_name: str) -> pd.DataFrame:
    """
    加载指定指数的全部旧缓存变动记录，合并为统一 DataFrame。

    Returns
    -------
    DataFrame
        columns: symbol(str), type(str 'add'|'remove'), date(str 'YYYY-MM-DD')
    """
    meta = cfg.INDEX_META[index_name]
    if meta["legacy_dir"] is None:
        logger.info(f"{index_name}: 无旧缓存目录。")
        return pd.DataFrame(columns=["symbol", "type", "date"])

    legacy_dir = cfg.LEGACY_CACHE_ROOT / meta["legacy_dir"]
    if not legacy_dir.exists():
        logger.info(f"{index_name}: 旧缓存目录不存在: {legacy_dir}")
        return pd.DataFrame(columns=["symbol", "type", "date"])

    all_dfs: list[pd.DataFrame] = []
    pattern = f"{index_name}_changes_*"
    files = sorted(legacy_dir.glob(pattern + ".csv") ) + \
            sorted(legacy_dir.glob(pattern + ".xls") ) + \
            sorted(legacy_dir.glob(pattern + ".xlsx"))

    for f in files:
        if f.suffix == ".csv":
            df = _parse_csv_changes(f)
        else:
            df = _parse_excel_changes(f)
        if not df.empty:
            all_dfs.append(df)

    if not all_dfs:
        logger.info(f"{index_name}: 旧缓存目录中无有效文件。")
        return pd.DataFrame(columns=["symbol", "type", "date"])

    combined = pd.concat(all_dfs, ignore_index=True).drop_duplicates()
    logger.info(f"{index_name}: 旧缓存加载 {len(files)} 个文件，共 {len(combined)} 条记录。")
    return combined


# ── 交叉校验 ──────────────────────────────────────────────────────────────────

def validate(
    index_name: str,
    output_report: bool = True,
) -> dict:
    """
    将 JQ 变动与旧缓存变动对比，输出校验报告。

    Returns
    -------
    dict
        {
          "matched_events": int,      # 日期维度完全一致的调样事件数
          "total_events": int,        # 旧缓存事件总数
          "discrepancies": list       # 有差异的调样日期列表（详细）
        }
    """
    legacy = load_legacy_changes(index_name)
    if legacy.empty:
        logger.info(f"{index_name}: 无旧缓存，跳过校验。")
        return {"matched_events": 0, "total_events": 0, "discrepancies": []}

    snapshots = load_snapshots(index_name)
    if snapshots.empty:
        logger.warning(f"{index_name}: JQ 快照为空，无法校验，请先 pull。")
        return {"matched_events": 0, "total_events": 0, "discrepancies": []}

    jq_changes = snapshots_to_changes(snapshots)

    # 按调样日期分组比较
    legacy_dates = sorted(legacy["date"].unique())
    discrepancies = []
    matched = 0

    for event_date in legacy_dates:
        legacy_add = set(legacy.loc[(legacy["date"] == event_date) & (legacy["type"] == "add"), "symbol"])
        legacy_rem = set(legacy.loc[(legacy["date"] == event_date) & (legacy["type"] == "remove"), "symbol"])

        jq_add = set(jq_changes.loc[(jq_changes["date"] == event_date) & (jq_changes["type"] == "add"), "symbol"])
        jq_rem = set(jq_changes.loc[(jq_changes["date"] == event_date) & (jq_changes["type"] == "remove"), "symbol"])

        add_only_legacy = legacy_add - jq_add
        add_only_jq     = jq_add - legacy_add
        rem_only_legacy = legacy_rem - jq_rem
        rem_only_jq     = jq_rem - legacy_rem

        has_diff = bool(add_only_legacy or add_only_jq or rem_only_legacy or rem_only_jq)
        if has_diff:
            discrepancies.append({
                "date": event_date,
                "add_only_in_legacy": sorted(add_only_legacy),
                "add_only_in_jq":     sorted(add_only_jq),
                "rem_only_in_legacy": sorted(rem_only_legacy),
                "rem_only_in_jq":     sorted(rem_only_jq),
            })
        else:
            matched += 1

    result = {
        "matched_events": matched,
        "total_events": len(legacy_dates),
        "agreement_rate": matched / len(legacy_dates) if legacy_dates else 1.0,
        "discrepancies": discrepancies,
    }

    if output_report:
        _print_report(index_name, result)

    return result


def _print_report(index_name: str, result: dict) -> None:
    total = result["total_events"]
    matched = result["matched_events"]
    rate = result["agreement_rate"]
    discs = result["discrepancies"]

    logger.info(f"=== {index_name.upper()} 校验报告 ===")
    logger.info(f"  旧缓存调样事件: {total} 个日期")
    logger.info(f"  完全一致:       {matched} 个 ({rate:.1%})")
    logger.info(f"  有差异:         {len(discs)} 个")

    for d in discs[:10]:  # 最多打印前 10 条
        logger.warning(f"  [{d['date']}]")
        if d["add_only_in_legacy"]:
            logger.warning(f"    旧缓存独有 add: {d['add_only_in_legacy'][:5]}")
        if d["add_only_in_jq"]:
            logger.warning(f"    JQ 独有 add:    {d['add_only_in_jq'][:5]}")
        if d["rem_only_in_legacy"]:
            logger.warning(f"    旧缓存独有 rem: {d['rem_only_in_legacy'][:5]}")
        if d["rem_only_in_jq"]:
            logger.warning(f"    JQ 独有 rem:    {d['rem_only_in_jq'][:5]}")

    if len(discs) > 10:
        logger.warning(f"  ... 共 {len(discs)} 条差异，仅显示前 10 条")

    if rate >= 0.95:
        logger.info(f"  ✓ 整体一致率 {rate:.1%}，数据质量良好")
    else:
        logger.warning(f"  ⚠ 整体一致率 {rate:.1%}，建议人工复核差异较大的日期")
