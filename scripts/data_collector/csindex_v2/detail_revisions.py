"""Detect mutable official notices without rewriting their historical publication date.

An approved correction keeps the originally parsed detail/attachments as the historical
baseline. The fetched revision is evidence, and dated manual deltas carry its new information.
"""

import csv
import hashlib
import html
import json
from pathlib import Path
import re

from loguru import logger

from . import config as cfg

REVIEWED_REVISIONS = Path(__file__).with_name("reviewed_revisions.json")
MANUAL_FIXES = Path(__file__).with_name("manual_fixes.csv")


def correction_fingerprint() -> str:
    """Invalidate derived rosters when approved correction inputs change."""
    digest = hashlib.sha256()
    for path in (MANUAL_FIXES, REVIEWED_REVISIONS):
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes() if path.exists() else b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def reviewed_correction_source(row: dict) -> str | None:
    """Return the audited notice ID for an exact dated manual delta."""
    if row.get("source") != "manual" or not REVIEWED_REVISIONS.exists():
        return None
    reviews = json.loads(REVIEWED_REVISIONS.read_text())
    keys = ("index_name", "symbol", "type", "announce_date", "effective_date")
    for aid, records in reviews.items():
        for record in records:
            for correction in record.get("corrections") or []:
                if correction.get("action") == "add" and all(row.get(k) == correction.get(k) for k in keys):
                    return aid
    return None


def historical_detail(fetched: dict) -> dict:
    """Bootstrap a known revised notice from its audited original, not today's version."""
    aid = str(fetched["data"]["id"])
    reviews = json.loads(REVIEWED_REVISIONS.read_text()) if REVIEWED_REVISIONS.exists() else {}
    records = reviews.get(aid, [])
    if not records:
        return fetched
    review = records[-1]
    path = REVIEWED_REVISIONS.parent / review["baseline_file"]
    original = json.loads(path.read_text())
    if detail_signature(original) != review["baseline_sha256"]:
        raise RuntimeError(f"公告 {aid} 已审核历史基线校验失败: {path}")
    check_detail_revision(original, fetched)
    return original


def detail_signature(payload: dict) -> str:
    """Ignore presentation-only HTML changes; retain text, identity and attachment URLs."""
    data = payload.get("data") or {}
    content = html.unescape(data.get("content") or "")
    urls = {e["fileUrl"] for e in data.get("enclosureList") or [] if e.get("fileUrl")}
    urls.update(re.findall(r'href=[\"\'](https?://[^\"\']+)[\"\']', content))
    material = {
        "id": str(data.get("id")),
        "publish_date": data.get("publishDate"),
        "title": re.sub(r"\s+", "", data.get("title") or ""),
        "text": re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", content)),
        "attachments": sorted(urls),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def check_detail_revision(original: dict, fetched: dict) -> None:
    baseline = detail_signature(original)
    current = detail_signature(fetched)
    aid = str(original["data"]["id"])
    reviews = json.loads(REVIEWED_REVISIONS.read_text()) if REVIEWED_REVISIONS.exists() else {}
    records = reviews.get(aid, [])
    if records and not any(record.get("baseline_sha256") == baseline for record in records):
        raise RuntimeError(f"公告 {aid} 缓存不匹配已审核历史基线；须核实并恢复原详情/附件后再重建")
    if baseline == current:
        if records:
            raise RuntimeError(f"公告 {aid} 官网内容已回退到原版；须复核现有修订修正，不能继续静默应用")
        return

    archive = cfg.CACHE_ROOT / "detail_revisions" / aid / f"{current}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_suffix(".tmp")
    temporary.write_text(json.dumps(fetched, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(archive)

    for review in records:
        if review.get("baseline_sha256") != baseline or review.get("reviewed_sha256") != current:
            continue
        with MANUAL_FIXES.open(encoding="utf-8", newline="") as source:
            fixes = list(csv.DictReader(source))
        corrections = review.get("corrections") or []
        if not corrections or any(
            not any(all(row.get(key) == value for key, value in correction.items()) for row in fixes)
            for correction in corrections
        ):
            raise RuntimeError(f"公告 {aid} 已审核修订缺少对应日期的 manual_fixes 修正；证据: {archive}")
        logger.info(f"公告 {aid} 修订已审核，保留原公告历史并应用有日期的修正；证据: {archive}")
        return

    raise RuntimeError(
        f"发现未经审核的公告修订 {aid}（正文或附件已变更）；证据: {archive}。"
        "原缓存保持不变，请核实修订日期和成分变更后登记修正，不能回填到原发布日期。"
    )
