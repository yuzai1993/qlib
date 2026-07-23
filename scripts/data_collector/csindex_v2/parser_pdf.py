"""解析器 3：PDF 附件（2020 至今的"部分指数样本调整名单"合并公告）。

格式（已实测确认）：
  附件：部分指数样本调整名单
  沪深300指数样本调整名单：          ← 分节标题（可能带空格"沪深300 指数"）
  调出名单 | 调入名单
  证券代码 证券名称 | 证券代码 证券名称
  002008 大族激光 | 000617 中油资本
  ...
  中证500指数样本调整名单：          ← 下一节
  ...（表格跨页，节标题也可能在页中间）

解析策略：
  逐页取 find_tables()（带 bbox）和节标题的 y 坐标，
  每张表归属其上方最近的节标题；页首无标题则沿用上一页的节。
  表格列结构固定：[调出代码, 调出名称, 调入代码, 调入名称]，
  单元格为 None 表示该侧名单已结束（两侧数量不等时）。
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

SECTION_RE = re.compile(r"([\u4e00-\u9fa5A-Z0-9\s]{2,30}?)\s*指数?样本[股]?调整名单")

# 目标指数显示名（去空格后精确匹配）
DISPLAY_TO_NAME = {meta["display"]: name for name, meta in cfg.INDEX_META.items()}

FILENAME_RE = re.compile(r"^(\d{8})_(\d+)_")

CODE_RE = re.compile(r"^\d{6}$")


def _section_index(section_text: str) -> str | None:
    """节标题 → 目标指数内部名（非目标指数返回 None）。"""
    s = section_text.replace(" ", "").replace("\u3000", "")
    m = SECTION_RE.search(s + "指数样本调整名单")  # 保底
    # 直接在压缩文本里找显示名，且要求精确（"沪深300"后面紧跟"指数"）
    for display, name in DISPLAY_TO_NAME.items():
        if s == display or s == display + "指数" or s.endswith(display) or s.endswith(display + "指数"):
            # 排除衍生名（"沪深300红利" endswith 不会匹配 "沪深300"...实际上
            # endswith("沪深300") 对 "关于沪深300" 成立，但节标题只含指数名，安全）
            return name
    return None


def _effective_date_of(announcement_id: str) -> tuple[str | None, str | None]:
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


def parse_pdf_file(path: Path) -> pd.DataFrame:
    import pdfplumber

    m = FILENAME_RE.match(path.name)
    ann_id = m.group(2) if m else None

    records: list[dict] = []
    current_index: str | None = None  # 跨页沿用

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # 节标题及其 y 坐标。
            # 注意："中证A50" 等前缀与后续文字的渲染基线可能略有偏差，
            # 因此用容差 3pt 分组拼行，而非精确 top 值。
            section_marks: list[tuple[float, str | None]] = []
            words = sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"]))
            lines: list[tuple[float, list]] = []
            for w in words:
                if lines and abs(w["top"] - lines[-1][0]) < 3.0:
                    lines[-1][1].append(w)
                else:
                    lines.append((w["top"], [w]))
            for top, ws in lines:
                line_text = "".join(w["text"] for w in sorted(ws, key=lambda x: x["x0"]))
                if "名单" not in line_text or "证券代码" in line_text:
                    continue
                mm = SECTION_RE.search(line_text)
                if mm:
                    idx = _section_index(mm.group(1))
                    section_marks.append((top, idx))
                elif "备选名单" in line_text:
                    # 备选名单节 → 其下表格跳过
                    section_marks.append((top, None))
            section_marks.sort()

            for table in page.find_tables():
                table_top = table.bbox[1]
                # 该表上方最近的节标题
                idx = current_index
                for top, sec_idx in section_marks:
                    if top < table_top:
                        idx = sec_idx
                    else:
                        break
                # 更新 current_index 供下一页/下一表沿用
                if section_marks:
                    last_before = [s for t, s in section_marks if t < table_top]
                    if last_before:
                        current_index = last_before[-1]
                        idx = current_index
                if idx is None:
                    continue

                for row in table.extract():
                    if not row:
                        continue
                    cells = [c.strip() if isinstance(c, str) else "" for c in row]
                    # 固定 4 列: [出代码, 出名称, 入代码, 入名称]；也兼容 2 列
                    pairs = []
                    if len(cells) >= 4:
                        pairs = [(cells[0], "remove"), (cells[2], "add")]
                    elif len(cells) >= 2:
                        pairs = [(cells[0], "unknown")]
                    for token, direction in pairs:
                        token = token.replace(" ", "")
                        if CODE_RE.match(token):
                            sym = normalize_symbol(token)
                            if sym:
                                records.append({
                                    "index_name": idx,
                                    "symbol": sym,
                                    "type": direction,
                                })

            # 页面末尾更新 current_index（若本页有节标题，取最后一个）
            if section_marks:
                current_index = section_marks[-1][1]

    if not records:
        return pd.DataFrame()

    eff, ann_date = _effective_date_of(ann_id) if ann_id else (None, None)
    out = pd.DataFrame(records).drop_duplicates()
    out["effective_date"] = eff
    out["announce_date"] = ann_date
    out["source_id"] = ann_id
    out["source_file"] = path.name
    out["method"] = "pdf"
    return out


# 只解析"样本调整名单"类 PDF；hbook（编制方案）等直接跳过
PDF_NAME_FILTER = re.compile(r"调整名单|调入调出")


def parse_all() -> pd.DataFrame:
    cfg.ensure_dirs()
    from urllib.parse import unquote

    files = sorted(cfg.FILES_DIR.glob("*.pdf"))
    frames = []
    seen_content: set = set()
    ok = 0
    for p in files:
        if not PDF_NAME_FILTER.search(unquote(p.name)):
            continue
        try:
            df = parse_pdf_file(p)
        except Exception as e:
            logger.warning(f"{p.name}: 解析失败 {e}")
            continue
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
    logger.info(f"PDF 解析: {ok} 个文件产出 {len(result)} 条记录")
    out = cfg.PARSED_DIR / "pdf_changes.csv"
    result.to_csv(out, index=False)
    logger.info(f"保存 → {out}")
    return result


if __name__ == "__main__":
    parse_all()
