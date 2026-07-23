"""共享配置。

调研结论（2026-07 验证）：
- 搜索 API `csindex-home/search/search-content` 可直接 HTTP 访问，无需 Playwright
- 详情 API `csindex-home/announcement/queryAnnouncementById` 返回正文 HTML + 附件列表
- 公告历史完整覆盖 2005-04-05 至今
- 早期公告（2005-2012）股票名单直接嵌在正文 HTML 中（无扫描件问题）
- 近期公告（2020+）名单在 OSS PDF 附件中
- 临时调整公告是自由文本，日期可能是条件式（"自终止上市生效日起"）
"""

from __future__ import annotations

from pathlib import Path

CACHE_ROOT = Path("~/.cache/qlib/csindex_v2").expanduser().resolve()

MANIFEST_PATH = CACHE_ROOT / "manifest.json"          # 公告元信息（搜索结果聚合）
DETAILS_DIR = CACHE_ROOT / "details"                  # 每条公告的详情 JSON
FILES_DIR = CACHE_ROOT / "files"                      # 附件（PDF/Excel）
SNAPSHOTS_DIR = CACHE_ROOT / "snapshots"              # 当前成分股快照 XLS
PARSED_DIR = CACHE_ROOT / "parsed"                    # 解析后的变更 CSV
CHANGES_DIR = CACHE_ROOT / "changes"                  # 聚合后的全量变更


def ensure_dirs() -> None:
    for d in (CACHE_ROOT, DETAILS_DIR, FILES_DIR, SNAPSHOTS_DIR, PARSED_DIR, CHANGES_DIR):
        d.mkdir(parents=True, exist_ok=True)


# 搜索关键词 → 指数
SEARCH_KEYWORDS = ["沪深300", "中证500", "中证1000", "中证2000"]

INDEX_META: dict[str, dict] = {
    "csi300":  {"code": "000300", "display": "沪深300",  "expected_size": 300},
    "csi500":  {"code": "000905", "display": "中证500",  "expected_size": 500},
    "csi1000": {"code": "000852", "display": "中证1000", "expected_size": 1000},
    "csi2000": {"code": "932000", "display": "中证2000", "expected_size": 2000},
}

SEARCH_API = "https://www.csindex.com.cn/csindex-home/search/search-content"
DETAIL_API = "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById"
SNAPSHOT_URL_TEMPLATE = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "file/autofile/cons/{code}cons.xls"
)

REQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# 限速（用户要求慢慢爬）
SLEEP_BETWEEN_REQUESTS = 4.0    # 每个请求间隔秒数
SLEEP_BETWEEN_PAGES = 6.0       # 搜索翻页间隔
