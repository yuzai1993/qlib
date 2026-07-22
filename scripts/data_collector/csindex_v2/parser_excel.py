"""解析器 2：Excel 附件（正式附件 + 正文内嵌链接下载的附件）。

已确认的两类格式：
  X1（2021 至今）: sheet 名"调入"/"调出"（少数临时调整用"换入"/"换出"），
       列 = [指数代码, 指数简称, 证券代码, 证券简称]
  X2（2015-2016）: 单 sheet，双层表头
       [指数代码, 指数简称, 调出(股票代码,股票名称), 调入(股票代码,股票名称)]
       注意证券代码可能丢失前导零（400 → 000400）

过滤规则：只保留目标指数（000300/000905/000852/932000）的行；
衍生指数样本名单文件（如"中证500红利低波动指数样本名单.xlsx"）自然被过滤为空。

生效日期：从文件名中的公告 id 回查公告详情正文提取。
"""

from __future__ import annotations

import html as html_mod
import json
import re
from pathlib import Path

import pandas as pd
from loguru import logger

from . import config as cfg
from .parser_content import extract_effective_date, normalize_symbol

# 目标指数代码 → 内部名
TARGET_INDEX_CODES = {meta["code"]: name for name, meta in cfg.INDEX_META.items()}

FILENAME_RE = re.compile(r"^(\d{8})_(\d+)_")


def _pad_code(v) -> str | None:
    """证券代码可能被 Excel 存成 int 丢前导零。"""
    if pd.isna(v):
        return None
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if not s.isdigit():
        return None
    if len(s) > 6:
        return None
    return s.zfill(6)


def _effective_date_of(announcement_id: str) -> tuple[str | None, str | None]:
    """回查公告详情，返回 (effective_date, announce_date)。"""
    p = cfg.DETAILS_DIR / f"{announcement_id}.json"
    if not p.exists():
        return None, None
    with p.open() as f:
        data = (json.load(f).get("data") or {})
    announce_date = data.get("publishDate")
    content = data.get("content") or ""
    text = re.sub(r"<[^>]+>", " ", content)
    text = html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text)
    eff = extract_effective_date(text, announce_date=announce_date, title=data.get("title") or "")
    return eff, announce_date


def parse_x1_sheet(df: pd.DataFrame, direction: str) -> list[dict]:
    """X1: 列 [指数代码, 指数简称, 证券代码, 证券简称]（首行为表头）。"""
    records = []
    for _, row in df.iterrows():
        vals = row.tolist()
        if len(vals) < 3:
            continue
        idx_code = _pad_code(vals[0])
        if idx_code is None and str(vals[0]).strip() == "932000":
            idx_code = "932000"
        # 932000 是 6 位，_pad_code 可处理；指数代码也可能是 H30439 之类，跳过
        if idx_code not in TARGET_INDEX_CODES:
            continue
        stock_code = _pad_code(vals[2])
        if stock_code is None:
            continue
        sym = normalize_symbol(stock_code)
        if sym:
            records.append({
                "index_name": TARGET_INDEX_CODES[idx_code],
                "symbol": sym,
                "type": direction,
            })
    return records


def parse_x2_sheet(df: pd.DataFrame) -> list[dict]:
    """X2: 双层表头 [指数代码, 指数简称, 调出(代码,名称), 调入(代码,名称)]。"""
    # 从前两行找"调出/调入"的列位置
    out_col = in_col = None
    header_rows = 0
    for ri in range(min(3, len(df))):
        for ci, v in enumerate(df.iloc[ri]):
            s = str(v).replace(" ", "")
            if "调出" in s:
                out_col = ci
                header_rows = max(header_rows, ri + 1)
            elif "调入" in s:
                in_col = ci
                header_rows = max(header_rows, ri + 1)
    if out_col is None or in_col is None:
        return []
    # 第二层表头（股票代码/名称）
    if header_rows < len(df):
        second = "".join(str(v) for v in df.iloc[header_rows])
        if "代码" in second or "名称" in second:
            header_rows += 1

    records = []
    for ri in range(header_rows, len(df)):
        row = df.iloc[ri].tolist()
        idx_code = _pad_code(row[0])
        if idx_code not in TARGET_INDEX_CODES:
            continue
        idx_name = TARGET_INDEX_CODES[idx_code]
        for col, direction in ((out_col, "remove"), (in_col, "add")):
            if col < len(row):
                code = _pad_code(row[col])
                if code:
                    sym = normalize_symbol(code)
                    if sym:
                        records.append({
                            "index_name": idx_name,
                            "symbol": sym,
                            "type": direction,
                        })
    return records


def parse_excel_file(path: Path) -> pd.DataFrame:
    """解析单个 Excel 附件 → 变更记录 DataFrame。"""
    m = FILENAME_RE.match(path.name)
    ann_id = m.group(2) if m else None

    try:
        xl = pd.ExcelFile(path)
    except Exception as e:
        logger.warning(f"{path.name}: 无法打开 ({e})")
        return pd.DataFrame()

    records: list[dict] = []
    sheet_names = xl.sheet_names

    # 调入/调出 与 换入/换出 同义（后者见于部分临时调整附件，如 id=14842）
    dir_map = {"调入": "add", "换入": "add", "调出": "remove", "换出": "remove"}
    has_direction_sheets = any(s.strip() in dir_map for s in sheet_names)
    if has_direction_sheets:
        for sh in sheet_names:
            name = sh.strip()
            if name in dir_map:
                records += parse_x1_sheet(xl.parse(sh, header=None), dir_map[name])
            # 备选名单 sheet 跳过
    else:
        for sh in sheet_names:
            df = xl.parse(sh, header=None)
            if df.empty:
                continue
            records += parse_x2_sheet(df)

    if not records:
        return pd.DataFrame()

    eff, ann_date = _effective_date_of(ann_id) if ann_id else (None, None)

    out = pd.DataFrame(records).drop_duplicates()
    out["effective_date"] = eff
    out["announce_date"] = ann_date
    out["source_id"] = ann_id
    out["source_file"] = path.name
    out["method"] = "excel"
    return out


def parse_all() -> pd.DataFrame:
    """解析全部 Excel 附件（跳过 embed 与正式附件重复的同名文件）。"""
    cfg.ensure_dirs()
    files = sorted(cfg.FILES_DIR.glob("*.xls")) + sorted(cfg.FILES_DIR.glob("*.xlsx"))

    frames = []
    seen_content: set = set()  # (ann_id, 记录指纹) 去重：embed 和正式附件常常是同一文件
    ok = 0
    for p in files:
        df = parse_excel_file(p)
        if df.empty:
            continue
        fingerprint = (
            df["source_id"].iloc[0],
            tuple(sorted(zip(df["index_name"], df["symbol"], df["type"]))),
        )
        if fingerprint in seen_content:
            continue
        seen_content.add(fingerprint)
        frames.append(df)
        ok += 1

    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    logger.info(f"Excel 解析: {ok} 个文件产出 {len(result)} 条记录")
    out = cfg.PARSED_DIR / "excel_changes.csv"
    result.to_csv(out, index=False)
    logger.info(f"保存 → {out}")
    return result


if __name__ == "__main__":
    parse_all()
