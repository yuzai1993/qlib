"""中证官网公告爬虫（基于搜索 API，无需浏览器）。

流程：
  1. 用 4 个关键词分别搜索，翻页收集所有 announcement 类型条目 → manifest.json
  2. 对每条公告调用详情 API，保存正文 + 附件列表 → details/{id}.json
  3. 下载所有附件 → files/
  4. 下载 4 个指数的当前成分快照 → snapshots/

特性：
  - 断点续传：manifest 已有的条目跳过搜索，details 已存在的跳过详情拉取
  - 慢速爬取：请求间隔 4-6 秒，总耗时约 40-60 分钟
  - 全程无浏览器依赖，纯 requests
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
from loguru import logger

from . import config as cfg


# ── HTTP 基础 ─────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 30, retries: int = 3) -> requests.Response | None:
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=cfg.REQ_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp
            logger.warning(f"GET {url[:80]} status={resp.status_code}")
        except Exception as e:
            logger.warning(f"GET {url[:80]} attempt={attempt} error: {e}")
        time.sleep(3.0 * (attempt + 1))
    return None


# ── Step 1: 搜索公告 ──────────────────────────────────────────────────────────

def search_announcements(keyword: str, page_size: int = 100) -> list[dict]:
    """搜索某关键词的全部 announcement 条目。"""
    results: list[dict] = []
    page = 1
    while True:
        url = (
            f"{cfg.SEARCH_API}?searchInput={quote(keyword)}"
            f"&pageNum={page}&pageSize={page_size}&lang=cn"
        )
        resp = _get(url)
        if resp is None:
            logger.error(f"search '{keyword}' page={page} failed, aborting this keyword")
            break
        data = resp.json()
        items = data.get("data") or []
        if not items:
            break
        anns = [x for x in items if x.get("itemType") == "announcement"]
        results.extend(anns)
        total = data.get("total", 0)
        logger.info(f"search '{keyword}' page={page}: {len(items)} items ({len(anns)} announcements), total={total}")
        if page * page_size >= total:
            break
        page += 1
        time.sleep(cfg.SLEEP_BETWEEN_PAGES)
    return results


def _merge_announcements(
    existing: dict[int, dict], anns: list[dict], keyword: str
) -> int:
    """把搜索结果合并进 existing，返回新增条数。"""
    new_count = 0
    for ann in anns:
        aid = ann["id"]
        if aid not in existing:
            title = re.sub(r"</?b>", "", ann.get("headline") or "")
            existing[aid] = {
                "id": aid,
                "date": ann.get("itemDate"),
                "title": title,
                "keywords": [keyword],
            }
            new_count += 1
        elif keyword not in existing[aid]["keywords"]:
            existing[aid]["keywords"].append(keyword)
    return new_count


def search_announcements_incremental(
    keyword: str,
    known_ids: set[int],
    page_size: int = 100,
    max_pages: int = 5,
) -> list[dict]:
    """增量搜索：遇到整页皆为已知 id 时提前停止（适合每日维护）。"""
    results: list[dict] = []
    page = 1
    while page <= max_pages:
        url = (
            f"{cfg.SEARCH_API}?searchInput={quote(keyword)}"
            f"&pageNum={page}&pageSize={page_size}&lang=cn"
        )
        resp = _get(url)
        if resp is None:
            logger.error(f"incremental search '{keyword}' page={page} failed")
            break
        data = resp.json()
        items = data.get("data") or []
        if not items:
            break
        anns = [x for x in items if x.get("itemType") == "announcement"]
        results.extend(anns)
        new_on_page = sum(1 for a in anns if a["id"] not in known_ids)
        logger.info(
            f"incremental '{keyword}' page={page}: "
            f"{len(anns)} announcements, new={new_on_page}"
        )
        if anns and new_on_page == 0:
            break
        total = data.get("total", 0)
        if page * page_size >= total:
            break
        page += 1
        time.sleep(cfg.SLEEP_BETWEEN_PAGES)
    return results


def build_manifest(refresh: bool = False, incremental: bool = False) -> list[dict]:
    """搜索关键词，合并去重（按 id），保存 manifest.json。

    incremental=True 时只翻最近几页，整页无新增即停（每日调度用）。
    """
    cfg.ensure_dirs()
    existing: dict[int, dict] = {}
    if cfg.MANIFEST_PATH.exists() and not refresh:
        with cfg.MANIFEST_PATH.open() as f:
            for item in json.load(f):
                existing[item["id"]] = item
        logger.info(f"manifest 已有 {len(existing)} 条")

    known_ids = set(existing.keys())
    for kw in cfg.SEARCH_KEYWORDS:
        if incremental:
            anns = search_announcements_incremental(kw, known_ids)
        else:
            anns = search_announcements(kw)
        new_count = _merge_announcements(existing, anns, kw)
        known_ids = set(existing.keys())
        logger.info(f"关键词 '{kw}': {len(anns)} 条，新增 {new_count}")
        time.sleep(cfg.SLEEP_BETWEEN_PAGES)

    items = sorted(existing.values(), key=lambda x: (x.get("date") or "", x["id"]))
    with cfg.MANIFEST_PATH.open("w") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    logger.info(f"manifest 保存: {len(items)} 条公告")
    return items


# ── Step 2: 拉取公告详情 ──────────────────────────────────────────────────────

def fetch_details(items: list[dict] | None = None) -> None:
    """对 manifest 中每条公告拉取详情 JSON（断点续传）。"""
    cfg.ensure_dirs()
    if items is None:
        with cfg.MANIFEST_PATH.open() as f:
            items = json.load(f)

    todo = [it for it in items if not (cfg.DETAILS_DIR / f"{it['id']}.json").exists()]
    logger.info(f"details: 共 {len(items)} 条，待拉取 {len(todo)} 条")

    for i, it in enumerate(todo):
        aid = it["id"]
        url = f"{cfg.DETAIL_API}?id={aid}&lang=cn"
        resp = _get(url)
        if resp is None:
            logger.error(f"detail id={aid} 拉取失败，跳过")
            continue
        try:
            payload = resp.json()
        except Exception as e:
            logger.error(f"detail id={aid} JSON 解析失败: {e}")
            continue
        dest = cfg.DETAILS_DIR / f"{aid}.json"
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        if (i + 1) % 20 == 0:
            logger.info(f"details: {i + 1}/{len(todo)} 完成")
        time.sleep(cfg.SLEEP_BETWEEN_REQUESTS)

    logger.info(f"details: 全部完成（目录共 {len(list(cfg.DETAILS_DIR.glob('*.json')))} 个文件）")


# ── Step 3: 下载附件 ──────────────────────────────────────────────────────────

def _safe_filename(s: str, max_len: int = 90) -> str:
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]", "_", s)
    return s[:max_len].strip()


def download_attachments() -> None:
    """扫描全部 details，下载 enclosureList 中的附件（断点续传）。"""
    cfg.ensure_dirs()
    detail_files = sorted(cfg.DETAILS_DIR.glob("*.json"))
    logger.info(f"attachments: 扫描 {len(detail_files)} 个详情文件")

    downloaded = 0
    skipped = 0
    for df in detail_files:
        with df.open() as f:
            payload = json.load(f)
        data = payload.get("data") or {}
        enclosures = data.get("enclosureList") or []
        pub_date = (data.get("publishDate") or "").replace("-", "")
        for enc in enclosures:
            file_url = enc.get("fileUrl")
            if not file_url:
                continue
            fname = f"{pub_date}_{df.stem}_{_safe_filename(enc.get('fileName') or 'attachment')}"
            dest = cfg.FILES_DIR / fname
            if dest.exists():
                skipped += 1
                continue
            resp = _get(file_url, timeout=60)
            if resp is None:
                logger.error(f"附件下载失败: {file_url[:80]}")
                continue
            dest.write_bytes(resp.content)
            downloaded += 1
            logger.info(f"附件: {fname[:70]} ({len(resp.content)} bytes)")
            time.sleep(cfg.SLEEP_BETWEEN_REQUESTS)

    logger.info(f"attachments: 下载 {downloaded}，已存在跳过 {skipped}")


# ── Step 3b: 下载正文内嵌附件 ─────────────────────────────────────────────────

EMBEDDED_HREF_RE = re.compile(
    r'href="(https?://oss-ch\.csindex\.com\.cn/[^"]+\.(?:xlsx?|pdf))"'
)


def download_embedded_attachments() -> None:
    """部分公告的名单链接内嵌在正文 HTML 的 <a href> 中（enclosureList 为空），
    扫描 details 并下载这些内嵌 OSS 文件（断点续传）。"""
    cfg.ensure_dirs()
    detail_files = sorted(cfg.DETAILS_DIR.glob("*.json"))
    downloaded = 0
    skipped = 0
    for df in detail_files:
        with df.open() as f:
            payload = json.load(f)
        data = payload.get("data") or {}
        content = data.get("content") or ""
        pub_date = (data.get("publishDate") or "").replace("-", "")
        urls = EMBEDDED_HREF_RE.findall(content)
        for url in urls:
            ext = url.rsplit(".", 1)[-1]
            base = url.rsplit("/", 1)[-1]
            fname = f"{pub_date}_{df.stem}_embed_{_safe_filename(base)}"
            dest = cfg.FILES_DIR / fname
            if dest.exists():
                skipped += 1
                continue
            resp = _get(url, timeout=60)
            if resp is None:
                logger.error(f"内嵌附件下载失败: {url[:80]}")
                continue
            dest.write_bytes(resp.content)
            downloaded += 1
            logger.info(f"内嵌附件: {fname[:70]} ({len(resp.content)} bytes)")
            time.sleep(cfg.SLEEP_BETWEEN_REQUESTS)
    logger.info(f"embedded attachments: 下载 {downloaded}，已存在跳过 {skipped}")


# ── Step 4: 下载当前成分快照 ──────────────────────────────────────────────────

def download_snapshots() -> None:
    cfg.ensure_dirs()
    for name, meta in cfg.INDEX_META.items():
        url = cfg.SNAPSHOT_URL_TEMPLATE.format(code=meta["code"])
        dest = cfg.SNAPSHOTS_DIR / f"{meta['code']}cons.xls"
        resp = _get(url, timeout=60)
        if resp is None:
            logger.error(f"snapshot {name} 下载失败")
            continue
        dest.write_bytes(resp.content)
        logger.info(f"snapshot {name} ({meta['code']}): {len(resp.content)} bytes")
        time.sleep(cfg.SLEEP_BETWEEN_REQUESTS)


# ── 主入口 ────────────────────────────────────────────────────────────────────

def run_all(refresh_manifest: bool = False) -> None:
    """完整流程：搜索 → 详情 → 附件 → 快照。"""
    logger.info("=== Step 1: 搜索公告，构建 manifest ===")
    items = build_manifest(refresh=refresh_manifest)

    logger.info("=== Step 2: 拉取公告详情 ===")
    fetch_details(items)

    logger.info("=== Step 3: 下载附件 ===")
    download_attachments()

    logger.info("=== Step 3b: 下载正文内嵌附件 ===")
    download_embedded_attachments()

    logger.info("=== Step 4: 下载当前成分快照 ===")
    download_snapshots()

    logger.info("=== 爬取完成 ===")


if __name__ == "__main__":
    import sys
    refresh = "--refresh" in sys.argv
    run_all(refresh_manifest=refresh)
