"""解析器 1：公告正文 HTML 表格。

适用：2005-2015 年的定期调样和临时调整公告，名单直接嵌在正文 <table> 中。

表格形态（已实测确认）：
  形态 A（并排双栏）:
      调出样本 | 调入样本          ← 或者反过来"调入|调出"，需从表头判断
      股票代码 股票名称 | 证券代码 证券简称
      000029  深深房A  | 000059  辽通化工
  形态 B（单栏，临时调整）:
      正文叙述"将 XXX(600002) 调出，调入 YYY(000793)"

输出统一格式：
  DataFrame(index_name, symbol, type, effective_date, announce_date, source_id)

注意：
  - 生效日期从正文提取："将于2005年7月1日调整" / "自2018年7月13日起"
  - 一份公告可能涉及多个指数（"沪深300指数和中证100指数"），
    但正文表格通常只给出目标指数的名单，按标题匹配指数
"""

from __future__ import annotations

import html as html_mod
import json
import re
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup
from loguru import logger

from . import config as cfg


# ── 日期提取 ──────────────────────────────────────────────────────────────────

CN_DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

# 生效日的语境模式（按优先级排列）
EFFECTIVE_PATTERNS = [
    r"将?于(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)(?:收市后)?(?:起)?(?:正式)?(?:一并)?(?:调整|生效|实施)",
    r"自(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)(?:收市后)?起",
    r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)(?:收市后)?(?:起)?生效",
]

# 无年份的日期模式（"决定于7月3日调整" / "自8月15日起"）
EFFECTIVE_PATTERNS_NO_YEAR = [
    r"将?于(\d{1,2})\s*月\s*(\d{1,2})\s*日(?:收市后)?(?:起)?(?:正式)?调整",
    r"自(\d{1,2})\s*月\s*(\d{1,2})\s*日(?:收市后)?起",
]

# "X年X月第一个交易日"模式（如"于2009年7月第一个交易日调整"、"生效日期2010年1月第一个交易日"）
FIRST_TRADING_DAY_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?:份)?\s*第一个交易日")


def _parse_cn_date(s: str) -> str | None:
    m = CN_DATE_RE.search(s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{y:04d}-{mo:02d}-{d:02d}"


def extract_effective_date(text: str, announce_date: str | None = None, title: str = "") -> str | None:
    """从正文/标题提取生效日期。"收市后生效"意味着次交易日起新名单生效，
    此处保留公告原文日期，调用方可自行 shift。

    注意：部分公告排版会把数字打散（"202 5 年 12 月 12 日"），
    因此先在去除全部空白的紧凑文本上匹配。"""
    compact = re.sub(r"[\s\u3000]+", "", text)
    for candidate in (compact, text):
        for pat in EFFECTIVE_PATTERNS:
            m = re.search(pat, candidate)
            if m:
                return _parse_cn_date(m.group(1))

    # "X年X月第一个交易日"：记为当月1日（后续可用交易日历修正）
    m = FIRST_TRADING_DAY_RE.search(compact)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return f"{y:04d}-{mo:02d}-01"

    # 无年份日期：用公告年份推断（若生效月份 < 公告月份，则跨年 +1）
    if announce_date:
        ann_year = int(announce_date[:4])
        ann_month = int(announce_date[5:7])
        for pat in EFFECTIVE_PATTERNS_NO_YEAR:
            m = re.search(pat, compact)
            if m:
                mo, d = int(m.group(1)), int(m.group(2))
                year = ann_year + 1 if mo < ann_month - 6 else ann_year
                return f"{year:04d}-{mo:02d}-{d:02d}"

    # 标题里的日期（如"样本股名单（2005年7月1日起生效）"）
    if title:
        d = _parse_cn_date(title)
        if d:
            return d

    # 兜底：找正文里第一个非落款的日期（落款通常在结尾）
    dates = CN_DATE_RE.findall(text[: int(len(text) * 0.7)])
    if dates:
        y, mo, d = dates[0]
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


# ── 指数匹配 ──────────────────────────────────────────────────────────────────

# 衍生/无关指数名：出现这些时，其中的"沪深300/中证500"等子串不算目标指数
NOISE_NAMES = [
    "沪深300精明", "沪深300非周期", "沪深300周期", "沪深300波动", "沪深300碳中和",
    "沪深300ESG", "沪深300红利", "沪深300质量", "沪深300价值", "沪深300成长",
    "沪深300相对", "沪深300债券", "沪深300行业", "沪深300风格", "沪深300等权",
    "沪深300基本面", "沪深300低波", "沪深300高贝塔", "沪深300地域",
    "中证500质量", "中证500ESG", "中证500红利", "中证500相对", "中证500成长",
    "中证500价值", "中证500行业", "中证500等权", "中证500基本面", "中证500低波",
    "中证500沪市", "中证500深市",
    "中证1000增强", "中证1000行业", "中证1000等权", "中证1000价值", "中证1000成长",
    "中证2000 ESG", "中证2000ESG", "中证2000增强", "中证2000行业",
    "沪深300动态", "沪深300稳定",
]


def _strip_noise(s: str) -> str:
    for noise in NOISE_NAMES:
        s = s.replace(noise, "")
    return s


def _normalize_ws(s: str) -> str:
    """去除全部空白（公告正文常见"沪深 300"、"中证 1 000"这类排版空格）。"""
    return re.sub(r"[\s\u3000]+", "", s)


def match_indices(title: str) -> list[str]:
    """判断文本涉及哪些目标指数（去空白、剔除衍生指数名后匹配）。"""
    core = _strip_noise(_normalize_ws(title))
    hits = []
    for name, meta in cfg.INDEX_META.items():
        if meta["display"] in core:
            hits.append(name)
    return hits


# ── 符号规范化 ────────────────────────────────────────────────────────────────

def normalize_symbol(raw: str) -> str | None:
    digits = "".join(c for c in str(raw) if c.isdigit())
    if len(digits) != 6:
        return None
    if digits.startswith(("60", "68", "51", "58", "11")):
        return f"SH{digits}"
    if digits.startswith(("00", "30", "12", "15", "39")):
        return f"SZ{digits}"
    if digits.startswith(("92", "43", "83", "87", "88")):
        return f"BJ{digits}"
    return None


CODE_RE = re.compile(r"^\d{6}$")


# ── 表格解析 ──────────────────────────────────────────────────────────────────

def _table_to_grid(table) -> list[list[str]]:
    """BeautifulSoup table → 二维字符串数组。"""
    grid = []
    for tr in table.find_all("tr"):
        row = []
        for td in tr.find_all(["td", "th"]):
            txt = td.get_text(separator=" ", strip=True)
            txt = re.sub(r"\s+", " ", txt)
            row.append(txt)
        if row:
            grid.append(row)
    return grid


def _find_direction_columns(grid: list[list[str]]) -> dict[int, str] | None:
    """
    在表格前几行找"调入/调出"表头，返回 {列起始索引: 'add'|'remove'}。

    并排双栏的表头行形如 ["调出样本", "调入样本"] 或 ["调入样本", "", "调出样本", ""]。
    """
    for row in grid[:4]:
        mapping: dict[int, str] = {}
        for ci, cell in enumerate(row):
            c = cell.replace(" ", "")
            if "调入" in c or "纳入" in c or "调进" in c or "新进" in c or "新增" in c:
                mapping[ci] = "add"
            elif "调出" in c or "剔除" in c or "删除" in c or "移出" in c:
                mapping[ci] = "remove"
        if mapping:
            return mapping
    return None


# 指数代码 → 内部名（用于形态 C2 的行首指数代码识别）
INDEX_CODE_TO_NAME = {meta["code"]: name for name, meta in cfg.INDEX_META.items()}

# 指数显示名（精确匹配用，形态 C1 行首。"沪深300能源"等衍生名不能前缀匹配成 csi300）
EXACT_DISPLAY_TO_NAME = {}
for _name, _meta in cfg.INDEX_META.items():
    EXACT_DISPLAY_TO_NAME[_meta["display"]] = _name
    EXACT_DISPLAY_TO_NAME[_meta["display"] + "指数"] = _name


def _is_per_index_row_table(grid: list[list[str]]) -> bool:
    """形态 C：每行一个指数的调整表。

    C1: 表头 [指数名称, 调入, 调出]，行首是指数中文名
    C2: 表头 [指数代码, 指数简称, 调出, 调入]，行首是指数代码
    方向词也可能写作"纳入/删除"（如 id=1607 农业银行 IPO 快速纳入公告）。
    """
    for row in grid[:3]:
        joined = "".join(row).replace(" ", "")
        if ("指数名称" in joined or "指数代码" in joined or "指数简称" in joined) \
                and ("调入" in joined or "调出" in joined
                     or "纳入" in joined or "删除" in joined):
            return True
    return False


def parse_per_index_row_table(grid: list[list[str]]) -> list[dict]:
    """
    解析形态 C 表格。返回 [{index_display, symbol_raw, type}, ...]。

    C1 数据行: 沪深300指数 | 大秦铁路 601006 | 000780 草原兴发
        （行首是指数名，其余代码前一半调入/后一半调出，顺序看表头）
    C2 数据行: 000300 沪深300 | 000562 宏源证券 | 000166 申万宏源
        （行首是指数代码，需从代码识别指数；其余代码同上）
    """
    header_row_i = None
    add_first = True
    for i, row in enumerate(grid[:4]):
        joined = "".join(row).replace(" ", "")
        if "指数名称" in joined or "指数代码" in joined or "指数简称" in joined:
            header_row_i = i
            pos_in = joined.find("调入")
            if pos_in < 0:
                pos_in = joined.find("纳入")
            pos_out = joined.find("调出")
            if pos_out < 0:
                pos_out = joined.find("删除")
            if pos_in >= 0 and pos_out >= 0:
                add_first = pos_in < pos_out
            break
    if header_row_i is None:
        return []

    records = []
    last_c1_index: str | None = None  # 仅 C1（中文指数名）表格的续行继承
    for row in grid[header_row_i + 1:]:
        cells = [c.replace(" ", "") for c in row if c.replace(" ", "")]
        if not cells:
            continue
        # 跳过第二表头行（"股票代码 股票名称…"）
        if any("代码" in c or "名称" in c or "简称" in c for c in cells):
            continue

        index_display = None
        stock_codes: list[str] = []

        if CODE_RE.match(cells[0]):
            mapped = INDEX_CODE_TO_NAME.get(cells[0])
            if mapped is not None:
                # C2：行首是已知指数代码
                index_display = mapped
                rest = cells[1:]
                last_c1_index = None
            elif last_c1_index is not None:
                # C1 续行：同一指数的调整被 HTML 拆成两行，首列是股票代码
                index_display = last_c1_index
                rest = cells
            else:
                # C2 未知指数代码（000903 等衍生指数），跳过
                continue
        else:
            # C1：行首是指数中文名
            index_display = cells[0]
            rest = cells[1:]
            last_c1_index = cells[0]

        for c in rest:
            if CODE_RE.match(c):
                stock_codes.append(c)

        if index_display is None or not stock_codes:
            continue

        half = (len(stock_codes) + 1) // 2
        first_type = "add" if add_first else "remove"
        second_type = "remove" if add_first else "add"
        for c in stock_codes[:half]:
            records.append({"index_display": index_display, "symbol_raw": c, "type": first_type})
        for c in stock_codes[half:]:
            records.append({"index_display": index_display, "symbol_raw": c, "type": second_type})
    return records


def parse_table(table) -> list[dict]:
    """解析单个 <table>，返回 [{symbol, type}, ...]。方向由表头判断。"""
    grid = _table_to_grid(table)
    if not grid:
        return []

    direction_by_header_col = _find_direction_columns(grid)
    records: list[dict] = []

    if direction_by_header_col:
        # 并排双栏：确定每个"代码列"归属哪个方向。
        # 表头列索引可能与数据行列索引不完全对齐（rowspan/colspan），
        # 策略：数据行中所有含 6 位代码的列，将其映射到最近的（<=）方向表头列。
        header_cols = sorted(direction_by_header_col.keys())

        def col_direction(ci: int) -> str:
            best = None
            for hc in header_cols:
                if hc <= ci:
                    best = hc
                else:
                    break
            if best is None:
                best = header_cols[0]
            return direction_by_header_col[best]

        # 表头行数不定，跳过所有不含代码的前置行。
        # 部分公告 HTML 破损，备选名单（带 1-2 位"排序"号）会混进调整表，
        # 遇到排序号单元格后，该行剩余部分视为备选名单内容丢弃。
        RANK_RE = re.compile(r"^\D?\d{1,2}$")
        for row in grid:
            for ci, cell in enumerate(row):
                token = cell.replace(" ", "")
                if RANK_RE.match(token):
                    break  # 行内出现排序号 → 后面是备选名单
                if CODE_RE.match(token):
                    sym = normalize_symbol(token)
                    if sym:
                        records.append({"symbol": sym, "type": col_direction(ci)})
    else:
        # 无方向表头：整表作为单方向名单（方向由调用方从上下文赋值）
        for row in grid:
            for cell in row:
                token = cell.replace(" ", "")
                if CODE_RE.match(token):
                    sym = normalize_symbol(token)
                    if sym:
                        records.append({"symbol": sym, "type": "unknown"})

    return records


# ── 临时调整的叙述式解析 ──────────────────────────────────────────────────────

def parse_narrative(text: str) -> list[dict]:
    """
    解析叙述式临时调整："将其从指数中调出，同时…依次调入G燃气（000793）、航天信息（600271）"

    策略：将正文按"调出/调入"分割，各段中提取"名称（代码）"。
    """
    records: list[dict] = []

    # 找到所有 名称（代码）模式，以及其在文中的位置
    pattern = re.compile(r"[（(](\d{6})[)）]")
    matches = list(pattern.finditer(text))
    if not matches:
        return records

    # 对每个代码，向前找最近的"调出/调入/剔除/纳入"动词
    verbs = [(m.start(), "remove") for m in re.finditer(r"调出|剔除|移出", text)]
    verbs += [(m.start(), "add") for m in re.finditer(r"调入|纳入|调进", text)]
    verbs.sort()

    for m in matches:
        pos = m.start()
        direction = None
        # 向前找最近的动词
        for vpos, vdir in verbs:
            if vpos < pos:
                direction = vdir
            else:
                break
        # 特例："因 XXX(code) 终止上市…将其调出"：动词在代码之后。
        # 若向前找不到动词，向后找第一个动词
        if direction is None:
            for vpos, vdir in verbs:
                if vpos > pos:
                    direction = vdir
                    break
        if direction:
            sym = normalize_symbol(m.group(1))
            if sym:
                records.append({"symbol": sym, "type": direction})

    return records


# ── 表格上下文识别 ────────────────────────────────────────────────────────────

# 已知的全部指数名（含非目标指数），用于顺序映射
KNOWN_INDEX_ORDER_NAMES = [
    "沪深300", "中证100", "中证500", "中证1000", "中证2000", "中证800",
    "中证香港100", "中证海外", "上证50", "上证180", "上证380", "科创50",
    "小康指数", "中证红利", "中证流通", "中证700", "中证200",
]


IN_TABLE_TITLE_RE = re.compile(r"(指数)?样本[股]?(调整)?名单")


def _in_table_label(grid: list[list[str]]) -> str | None:
    """部分公告把"XX指数样本股调整名单"作为表格第一行。返回该标题或 None。"""
    for row in grid[:2]:
        cells = [c for c in row if c.strip()]
        if len(cells) == 1 and IN_TABLE_TITLE_RE.search(cells[0].replace(" ", "")):
            return cells[0][:100]
    return None


def _table_label(table) -> str:
    """取表格前最近的非空文本节点（跳过纯空白），作为标签候选。"""
    for s in table.find_all_previous(string=True):
        t = str(s).strip()
        if t:
            return t[:100]
    return ""


def _label_index(label: str) -> str | None | str:
    """
    根据标签文本判断该表归属：
      - "csi300" 等: 明确目标指数
      - None: 应跳过（备选名单/衍生指数名单）
      - "": 标签无信息量
    """
    if not label:
        return ""
    if "备选" in label:
        return None
    # 标签形如"XX指数样本股调整名单："才可信；太长的段落文本不算标签
    if len(label) > 60:
        return ""
    ctx_indices = match_indices(label)
    if ctx_indices:
        return ctx_indices[0]
    # 是"××名单"但不含目标指数 → 衍生/无关指数的名单，跳过
    if "名单" in label:
        return None
    return ""


def _extract_index_order(text: str) -> list[str]:
    """从叙述段落提取指数出现顺序（含非目标指数，保序去重）。"""
    hits: list[tuple[int, str]] = []
    core = _strip_noise(text)
    for disp in KNOWN_INDEX_ORDER_NAMES:
        pos = core.find(disp)
        if pos >= 0:
            hits.append((pos, disp))
    hits.sort()
    seen = set()
    ordered = []
    for _, disp in hits:
        if disp not in seen:
            seen.add(disp)
            ordered.append(disp)
    return ordered


def _display_to_name(display: str) -> str | None:
    """指数中文名 → 内部名（非目标指数返回 None）。"""
    for name, meta in cfg.INDEX_META.items():
        if meta["display"] == display:
            return name
    return None


# ── 主入口：解析单条公告 ──────────────────────────────────────────────────────

FULL_LIST_TITLE_RE = re.compile(r"样本股?名单")


def parse_announcement(detail_path: Path) -> pd.DataFrame:
    """
    解析一条公告详情 JSON 的正文，输出变更记录。

    Returns
    -------
    DataFrame
        columns: index_name, symbol, type, effective_date, announce_date, source_id, method
        type: 'add' | 'remove' | 'full'（全量名单快照）
        若不适用（无正文名单），返回空 DataFrame。
    """
    with detail_path.open() as f:
        payload = json.load(f)
    data = payload.get("data") or {}
    title = data.get("title") or ""
    announce_date = data.get("publishDate") or ""
    content = data.get("content") or ""
    aid = data.get("id")

    title_indices = match_indices(title)
    if not title_indices:
        return pd.DataFrame()

    # 标题级噪声过滤：
    # - 备选名单公告（正式调整公告里已含备选表，单独的备选公告无增量信息）
    # - 新指数发布/编制方案公告（表格是指数代码清单，非样本股名单）
    if "备选" in title:
        return pd.DataFrame()
    if re.search(r"发布.*指数", title) and "调整" not in title:
        return pd.DataFrame()

    text = re.sub(r"<[^>]+>", " ", content)
    text = html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text)

    effective_date = extract_effective_date(text, announce_date=announce_date, title=title)
    date_inferred = False

    # 定期调样公告缺日期时按惯例推断：
    # 5-6 月公告 → 当年 7 月第一个交易日；11-12 月公告 → 次年 1 月第一个交易日
    # （2013 年后定期调样改为 6/12 月第二个周五后的次交易日，但那些公告都有明确日期）
    if effective_date is None and announce_date:
        month = int(announce_date[5:7])
        year = int(announce_date[:4])
        if month in (5, 6):
            effective_date = f"{year:04d}-07-01"
            date_inferred = True
        elif month in (11, 12):
            effective_date = f"{year + 1:04d}-01-01"
            date_inferred = True

    # 全量名单公告（如"沪深300指数样本股名单（2005年7月1日起生效）"）
    is_full_list = bool(FULL_LIST_TITLE_RE.search(title)) and "调整" not in title and "调样" not in title

    soup = BeautifulSoup(content, "lxml")
    # 只处理叶子表格（外层包裹表格会重复包含内层内容）
    tables = [t for t in soup.find_all("table") if t.find("table") is None]

    rows: list[dict] = []
    seen: set = set()

    def emit(idx_name: str, symbol: str, rtype: str, method: str):
        key = (idx_name, symbol, rtype)
        if key in seen:
            return
        seen.add(key)
        rows.append({
            "index_name": idx_name,
            "symbol": symbol,
            "type": rtype,
            "effective_date": effective_date,
            "announce_date": announce_date,
            "source_id": aid,
            "method": method + ("_date_inferred" if date_inferred else ""),
        })

    for table in tables:
        grid = _table_to_grid(table)
        if not grid:
            continue

        # 形态 C：每行一个指数（IPO 快速纳入等临时调整）
        if _is_per_index_row_table(grid):
            for r in parse_per_index_row_table(grid):
                disp = r["index_display"]
                if disp in cfg.INDEX_META:
                    idx_name = disp  # C2 已映射为内部名
                else:
                    # 必须精确匹配："沪深300能源"等衍生指数行不能算 csi300
                    idx_name = EXACT_DISPLAY_TO_NAME.get(disp)
                if idx_name is None:
                    continue
                sym = normalize_symbol(r["symbol_raw"])
                if sym:
                    emit(idx_name, sym, r["type"], "table_per_index")
            continue

        # 形态 A/B：优先用表内首行标题，否则用前置文本标签
        label = _in_table_label(grid) or _table_label(table)
        idx_attr = _label_index(label)
        if idx_attr is None:
            continue  # 备选名单/衍生指数名单
        if idx_attr == "":
            # 无标签信息：单指数公告归标题指数；多指数公告无法归属，跳过并留待人工
            if len(title_indices) == 1:
                idx_attr = title_indices[0]
            else:
                idx_attr = "__ambiguous__"

        recs = parse_table(table)
        for r in recs:
            rtype = r["type"]
            if rtype == "unknown":
                rtype = "full" if is_full_list else "unknown"
            emit(idx_attr, r["symbol"], rtype, "table")

    # 表格没解析出来 → 尝试叙述式。
    # 但若正文说"名单见附件"，名单本体在附件里，交给附件解析器处理，
    # 此处不做叙述式解析（正文提到的股票代码只是风险警示的当事股票）。
    refers_to_attachment = bool(re.search(r"附\s*件", text)) and "名单" in text
    if not rows and not refers_to_attachment:
        narrative_recs = parse_narrative(text)
        for r in narrative_recs:
            for idx_name in title_indices:
                emit(idx_name, r["symbol"], r["type"], "narrative")

    return pd.DataFrame(rows)


def parse_all() -> pd.DataFrame:
    """解析全部公告正文，汇总输出 + 报告失败清单。"""
    cfg.ensure_dirs()
    detail_files = sorted(cfg.DETAILS_DIR.glob("*.json"))
    frames = []
    parsed_ok = []
    for f in detail_files:
        try:
            df = parse_announcement(f)
        except Exception as e:
            logger.error(f"parse {f.name} error: {e}")
            continue
        if not df.empty:
            frames.append(df)
            parsed_ok.append(f.stem)

    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    logger.info(f"正文解析: {len(parsed_ok)} 条公告产出 {len(result)} 条记录")
    out = cfg.PARSED_DIR / "content_changes.csv"
    result.to_csv(out, index=False)
    logger.info(f"保存 → {out}")
    return result


if __name__ == "__main__":
    parse_all()
