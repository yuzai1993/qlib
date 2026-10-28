"""Versioned analysis documents in a dedicated archive; source reports are read-only."""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class ConflictError(ValueError):
    """The saved version changed after the editor loaded it."""


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _storage_overlaps(left: Path, right: Path) -> bool:
    # Be conservative on macOS case-insensitive volumes, including paths whose
    # final archive directory does not exist yet and cannot be checked by inode.
    left = Path(str(left).casefold())
    right = Path(str(right).casefold())
    return _inside(left, right) or _inside(right, left)


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


class AnalysisStore:
    MAX_CODE_BYTES = 256 * 1024
    MAX_RECORD_BYTES = 2 * 1024 * 1024

    def __init__(self, data_root: Path, research_root: Path):
        self.root = Path(data_root).resolve()
        self.research_root = Path(research_root).resolve()
        if _storage_overlaps(self.root, self.research_root):
            raise ValueError("分析存储必须与研究源目录分离")
        if "live_trading" in [part.casefold() for part in self.root.parts]:
            raise ValueError("分析存储不能位于实盘目录")
        protected = (Path.home() / "Project/qlib", Path("/Volumes/qmt_bridge"))
        for location in protected:
            location = location.resolve()
            if _storage_overlaps(self.root, location):
                raise ValueError("分析存储不得位于生产根或交易共享目录，也不能包含这些目录")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, record_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", record_id):
            raise ValueError("无效的分析标识")
        path = self.root / f"{record_id}.json"
        if path.is_symlink() or not _inside(path.resolve(), self.root):
            raise ValueError("分析文件路径不可用")
        return path

    @contextmanager
    def _lock(self):
        lock_path = self.root / ".write.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _validate(payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("分析内容必须是对象")
        record = {}
        for field in ("title", "background", "method", "conclusion", "usage"):
            value = payload.get(field)
            limit = 200 if field == "title" else 100_000
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{field} 必须填写，且不能超过 {limit} 字符")
            record[field] = value.strip()
        for field in ("tags", "related_experiments"):
            values = payload.get(field, [])
            if not isinstance(values, list) or len(values) > 50:
                raise ValueError(f"{field} 最多 50 项")
            if any(not isinstance(v, str) or len(v) > 300 for v in values):
                raise ValueError(f"{field} 内容格式无效")
            record[field] = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        status = payload.get("status", "active")
        if status not in {"active", "archived"}:
            raise ValueError("状态必须是 active 或 archived")
        record["status"] = status
        codes = payload.get("codes")
        if not isinstance(codes, list) or not 1 <= len(codes) <= 20:
            raise ValueError("请归档 1–20 份代码文件")
        record["codes"] = []
        names = set()
        for code in codes:
            if not isinstance(code, dict):
                raise ValueError("代码文件格式无效")
            name, content = code.get("name"), code.get("content")
            if (
                not isinstance(name, str)
                or not 1 <= len(name) <= 100
                or name.startswith(".")
                or name != name.strip()
                or re.search(r"[\\/:\x00-\x1f\x7f]", name)
            ):
                raise ValueError("代码文件名必须是安全的单一文件名")
            if name.casefold() in names:
                raise ValueError("代码文件名不能重复")
            names.add(name.casefold())
            if (
                not isinstance(content, str)
                or not content.strip()
                or len(content.encode("utf-8")) > AnalysisStore.MAX_CODE_BYTES
                or "\x00" in content
            ):
                raise ValueError("每份代码须为非空 UTF-8 文本，最多 256 KiB")
            record["codes"].append({"name": name, "content": content})
        if len(json.dumps(record, ensure_ascii=False).encode()) > AnalysisStore.MAX_RECORD_BYTES:
            raise ValueError("分析记录超过 2 MiB")
        return record

    @staticmethod
    def _report(row: dict) -> str:
        sections = [("背景", "background"), ("方法", "method"), ("结论", "conclusion"), ("使用说明", "usage")]
        return f"# {row['title']}\n\n" + "\n\n".join(f"## {title}\n\n{row[field]}" for title, field in sections) + "\n"

    def _write(self, row: dict):
        serialized = json.dumps(row, ensure_ascii=False, indent=2, allow_nan=False)
        if len(serialized.encode("utf-8")) > self.MAX_RECORD_BYTES:
            raise ValueError("完整报告与代码超过 2 MiB，请缩短内容或拆分分析")
        destination = self._path(row["id"])
        fd, temporary = tempfile.mkstemp(prefix=".analysis-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def create(self, payload: dict) -> dict:
        row = self._validate(payload)
        now = _now()
        row.update(
            id=uuid.uuid4().hex, version=1, created_at=now, updated_at=now, historical=False, attachments_missing=[]
        )
        row["report"] = self._report(row)
        with self._lock():
            self._write(row)
        return row

    def update(self, record_id: str, payload: dict) -> dict:
        if record_id.startswith("history-"):
            raise ValueError("历史源报告为只读，请新建补充分析")
        row = self._validate(payload)
        with self._lock():
            previous = self.get(record_id)
            version = payload.get("version")
            if type(version) is not int or version != previous["version"]:
                raise ConflictError("记录已被更新，请重新载入后再保存")
            row.update(
                id=record_id,
                version=version + 1,
                created_at=previous["created_at"],
                updated_at=_now(),
                historical=False,
                attachments_missing=[],
            )
            row["report"] = self._report(row)
            self._write(row)
        return row

    def get(self, record_id: str) -> dict:
        if record_id.startswith("history-"):
            for row in self._history():
                if row["id"] == record_id:
                    return row
            raise FileNotFoundError("分析不存在")
        path = self._path(record_id)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as source:
            raw = source.read(self.MAX_RECORD_BYTES + 1)
        if len(raw.encode()) > self.MAX_RECORD_BYTES:
            raise ValueError("分析记录过大")
        row = json.loads(raw)
        if not isinstance(row, dict) or row.get("id") != record_id:
            raise ValueError("分析记录格式损坏")
        return row

    def _history(self) -> list[dict]:
        directory = self.research_root / "backtest/diagnostics"
        records = []
        for path in sorted(directory.glob("*/REPORT.md")):
            resolved = path.resolve()
            if path.is_symlink() or not _inside(resolved, self.research_root):
                continue
            if path.stat().st_size > self.MAX_RECORD_BYTES:
                continue
            report = path.read_text(encoding="utf-8", errors="replace")
            heading = re.search(r"^#\s+(.+)$", report, re.M)
            rel = str(path.relative_to(self.research_root))
            created = path.parent.name[:8]
            date = f"{created[:4]}-{created[4:6]}-{created[6:8]}" if created.isdigit() else ""
            codes, missing = [], []
            references = set(re.findall(r"`([^`\n]+\.(?:py|r|R|sql|js|sh))`", report))
            for reference in sorted(references):
                candidates = [self.research_root / reference, path.parent / reference]
                found = next(
                    (
                        p
                        for p in candidates
                        if _inside(p.resolve(), self.research_root)
                        and p.is_file()
                        and not p.is_symlink()
                        and p.stat().st_size <= self.MAX_CODE_BYTES
                    ),
                    None,
                )
                if found is None:
                    missing.append(reference)
                elif found.name not in {c["name"] for c in codes}:
                    codes.append({"name": found.name, "content": found.read_text(encoding="utf-8", errors="replace")})
            for image in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", report):
                candidate = (path.parent / image).resolve()
                if not _inside(candidate, self.research_root) or not candidate.is_file():
                    missing.append(image)
            if not codes:
                missing.append("未归档可核验的分析代码；保留原文供追溯")
            records.append(
                dict(
                    id="history-" + hashlib.sha256(rel.encode()).hexdigest()[:20],
                    title=heading.group(1) if heading else path.parent.name,
                    background="",
                    method="",
                    conclusion="",
                    usage="详见原始报告中的复现说明；附件完整性以提示为准。",
                    tags=["历史分析"],
                    related_experiments=[],
                    codes=codes,
                    status="active",
                    version=0,
                    created_at=date,
                    updated_at=date,
                    historical=True,
                    report=report,
                    source=rel,
                    attachments_missing=missing,
                )
            )
        return records

    def list(self) -> list[dict]:
        rows = self._history()
        for path in self.root.glob("*.json"):
            rows.append(self.get(path.stem))
        return sorted(rows, key=lambda r: r.get("updated_at", ""), reverse=True)

    def export(self, record_id: str) -> bytes:
        row = self.get(record_id)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("REPORT.md", row["report"])
            for code in row["codes"]:
                archive.writestr("code/" + code["name"], code["content"])
            metadata = {k: v for k, v in row.items() if k not in {"codes", "report"}}
            archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        return output.getvalue()
