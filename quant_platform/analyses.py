"""Discover analysis directories and atomically publish versioned documents."""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import stat
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
    left, right = Path(str(left).casefold()), Path(str(right).casefold())
    return _inside(left, right) or _inside(right, left)


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


class AnalysisStore:
    MAX_CODE_BYTES = 256 * 1024
    MAX_RECORD_BYTES = 2 * 1024 * 1024
    SCHEMA_VERSION = 1
    SECTIONS = ("background", "method", "conclusion", "usage")

    def __init__(self, data_root: Path | None, research_root: Path):
        self.research_root = Path(research_root).expanduser().resolve()
        canonical = self.research_root / "backtest/diagnostics"
        requested = Path(data_root).expanduser() if data_root is not None else canonical
        if requested.is_symlink():
            raise ValueError("分析根目录不能是符号链接")
        self.root = requested.resolve()
        canonical_allowed = self.root == canonical
        if _storage_overlaps(self.root, self.research_root) and not canonical_allowed:
            raise ValueError("研究源内仅允许使用 backtest/diagnostics 分析目录")
        if "live_trading" in [part.casefold() for part in self.root.parts]:
            raise ValueError("分析存储不能位于实盘目录")
        if _storage_overlaps(self.root, Path("/Volumes/qmt_bridge").resolve()):
            raise ValueError("分析存储不能位于交易共享目录，也不能包含该目录")
        if not canonical_allowed:
            for location in (Path.home() / "Project/qlib",):
                if _storage_overlaps(self.root, location.resolve()):
                    raise ValueError("分析存储不得位于生产根或交易共享目录，也不能包含这些目录")
        if requested.absolute() == canonical and not canonical_allowed:
            raise ValueError("分析目录不能经符号链接逃离研究源")
        if canonical_allowed and canonical.parent.is_symlink():
            raise ValueError("分析目录的父目录不能是符号链接")
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_id(record_id: str) -> None:
        if not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", record_id):
            raise ValueError("无效的分析标识")

    def _safe_file(self, directory: Path, relative: str) -> Path:
        if (not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative
                or Path(relative).is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/"))):
            raise ValueError("分析文件必须使用目录内的安全相对路径")
        current = directory
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("分析文件不能经符号链接读取")
        if directory.is_symlink() or not _inside(current.resolve(), directory.resolve()):
            raise ValueError("分析文件路径越界")
        return current

    def _read(self, path: Path, limit: int, expected_hash: str | None = None) -> str:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            attributes = os.fstat(source.fileno())
            if not stat.S_ISREG(attributes.st_mode):
                raise ValueError("分析文件必须为普通文件")
            if attributes.st_size > limit:
                raise ValueError("分析文件超过大小限制")
            payload = source.read(limit + 1)
        if len(payload) > limit:
            raise ValueError("分析文件超过大小限制")
        if expected_hash is not None and (
            not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)
            or hashlib.sha256(payload).hexdigest() != expected_hash
        ):
            raise ValueError("分析文件 SHA256 校验失败")
        return payload.decode("utf-8")

    @contextmanager
    def _lock(self):
        fd = os.open(self.root / ".write.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
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
        for field in ("title", *AnalysisStore.SECTIONS):
            value, limit = payload.get(field), 200 if field == "title" else 100_000
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{field} 必须填写，且不能超过 {limit} 字符")
            record[field] = value.strip()
        for field in ("tags", "related_experiments"):
            values = payload.get(field, [])
            if not isinstance(values, list) or len(values) > 50:
                raise ValueError(f"{field} 最多 50 项")
            if any(not isinstance(value, str) or len(value) > 300 for value in values):
                raise ValueError(f"{field} 内容格式无效")
            record[field] = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        status = payload.get("status", "active")
        if status not in {"active", "archived"}:
            raise ValueError("状态必须是 active 或 archived")
        record["status"] = status
        codes = payload.get("codes")
        if not isinstance(codes, list) or not 1 <= len(codes) <= 20:
            raise ValueError("请归档 1–20 份代码文件")
        record["codes"], names = [], set()
        for code in codes:
            if not isinstance(code, dict):
                raise ValueError("代码文件格式无效")
            name, content = code.get("name"), code.get("content")
            if (not isinstance(name, str) or not 1 <= len(name) <= 100 or name.startswith(".")
                    or name != name.strip() or re.search(r"[\\/:\x00-\x1f\x7f]", name)):
                raise ValueError("代码文件名必须是安全的单一文件名")
            if name.casefold() in names:
                raise ValueError("代码文件名不能重复")
            names.add(name.casefold())
            if (not isinstance(content, str) or not content.strip()
                    or len(content.encode("utf-8")) > AnalysisStore.MAX_CODE_BYTES or "\x00" in content):
                raise ValueError("每份代码须为非空 UTF-8 文本，最多 256 KiB")
            record["codes"].append({"name": name, "content": content})
        if len(json.dumps(record, ensure_ascii=False).encode()) > AnalysisStore.MAX_RECORD_BYTES:
            raise ValueError("分析记录超过 2 MiB")
        return record

    @staticmethod
    def _report(row: dict) -> str:
        sections = [("背景", "background"), ("方法", "method"), ("结论", "conclusion"), ("使用说明", "usage")]
        return f"# {row['title']}\n\n" + "\n\n".join(f"## {title}\n\n{row[field]}" for title, field in sections) + "\n"

    def _source(self, path: Path) -> str:
        return path.relative_to(self.research_root).as_posix() if _inside(path, self.research_root) else str(path)

    def _legacy_record(self, directory: Path) -> dict:
        path = self._safe_file(directory, "REPORT.md")
        report = self._read(path, self.MAX_RECORD_BYTES)
        heading = re.search(r"^#\s+(.+)$", report, re.M)
        rel = self._source(path)
        created = directory.name[:8]
        date = f"{created[:4]}-{created[4:6]}-{created[6:8]}" if created.isdigit() else ""
        missing = sorted(set(re.findall(r"`([^`\n]+\.(?:py|r|R|sql|js|sh))`", report)))
        missing.extend(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", report))
        missing.append("未归档可核验的分析代码；保留原文供追溯")
        return dict(id="history-" + hashlib.sha256(rel.encode()).hexdigest()[:20],
                    title=heading.group(1) if heading else directory.name,
                    background="", method="", conclusion="", usage="详见原始报告中的复现说明；附件完整性以提示为准。",
                    tags=["历史分析"], related_experiments=[], codes=[], status="active", version=0,
                    created_at=date, updated_at=date, historical=True, report=report, source=rel,
                    attachments_missing=missing)

    def _load_directory(self, directory: Path) -> dict | None:
        if directory.is_symlink():
            raise ValueError("分析目录不能是符号链接")
        metadata_path = self._safe_file(directory, "metadata.json")
        if not metadata_path.exists():
            return self._legacy_record(directory) if (directory / "REPORT.md").exists() else None
        try:
            metadata = json.loads(self._read(metadata_path, self.MAX_RECORD_BYTES))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"分析元数据损坏：{directory.name}") from exc
        if (not isinstance(metadata, dict) or type(metadata.get("schema_version")) is not int
                or metadata.get("schema_version") != self.SCHEMA_VERSION):
            raise ValueError("不支持的分析元数据格式")
        self._validate_id(metadata.get("id"))
        historical = metadata.get("historical", True)
        if type(historical) is not bool:
            raise ValueError("分析 historical 必须是布尔值")
        version = metadata.get("version", 0 if historical else 1)
        if type(version) is not int or version < (0 if historical else 1):
            raise ValueError("分析版本号无效")
        title = metadata.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("分析标题无效")
        report_path = self._safe_file(directory, metadata.get("report_path"))
        report = self._read(report_path, self.MAX_RECORD_BYTES, metadata.get("report_sha256"))
        sections = {field: "" for field in self.SECTIONS}
        if historical:
            sections["usage"] = "详见原始报告中的复现说明；附件完整性以提示为准。"
        if metadata.get("document_path"):
            document = json.loads(self._read(self._safe_file(directory, metadata["document_path"]),
                                            self.MAX_RECORD_BYTES, metadata.get("document_sha256")))
            if not isinstance(document, dict) or any(not isinstance(document.get(field), str) for field in self.SECTIONS):
                raise ValueError("分析正文格式损坏")
            sections = {field: document[field] for field in self.SECTIONS}
        elif not historical:
            raise ValueError("可编辑分析缺少正文文件")
        missing = metadata.get("attachments_missing", [])
        if not isinstance(missing, list) or any(not isinstance(value, str) for value in missing):
            raise ValueError("分析附件说明格式无效")
        missing = list(missing)
        code_files = metadata.get("code_files", [])
        if not isinstance(code_files, list) or len(code_files) > 20:
            raise ValueError("分析代码清单格式无效")
        codes, names = [], set()
        for code in code_files:
            if not isinstance(code, dict):
                raise ValueError("分析代码清单格式无效")
            name = code.get("name")
            if (not isinstance(name, str) or not 1 <= len(name) <= 100 or name.startswith(".") or name != name.strip()
                    or re.search(r"[\\/:\x00-\x1f\x7f]", name) or name.casefold() in names):
                raise ValueError("分析代码文件名无效或重复")
            names.add(name.casefold())
            path = self._safe_file(directory, code.get("path"))
            if not path.is_file() and historical:
                missing.append(code["path"])
                continue
            content = self._read(path, self.MAX_CODE_BYTES, code.get("sha256"))
            if not content.strip() or "\x00" in content:
                raise ValueError("分析代码必须是非空 UTF-8 文本")
            codes.append({"name": name, "content": content})
        if not historical and not codes:
            raise ValueError("可编辑分析缺少代码文件")
        row = dict(id=metadata["id"], title=title, **sections, codes=codes, historical=historical,
                   version=version, status=metadata.get("status", "active"), tags=metadata.get("tags", []),
                   related_experiments=metadata.get("related_experiments", []),
                   created_at=metadata.get("created_at", ""), updated_at=metadata.get("updated_at", ""),
                   report=report, source=self._source(report_path), attachments_missing=list(dict.fromkeys(missing)))
        if not isinstance(row["status"], str) or row["status"] not in {"active", "archived"}:
            raise ValueError("分析状态无效")
        for field in ("tags", "related_experiments"):
            if not isinstance(row[field], list) or len(row[field]) > 50 or any(not isinstance(v, str) for v in row[field]):
                raise ValueError("分析标签或关联实验格式无效")
        if any(not isinstance(row[field], str) for field in ("created_at", "updated_at")):
            raise ValueError("分析日期格式无效")
        if len(json.dumps(row, ensure_ascii=False).encode()) > self.MAX_RECORD_BYTES:
            raise ValueError("完整报告与代码超过 2 MiB")
        return row

    def _directories(self):
        if self.root.is_symlink():
            raise ValueError("分析根目录不能是符号链接")
        for directory in sorted(self.root.iterdir()):
            if directory.name.startswith("."):
                continue
            if directory.is_symlink():
                raise ValueError("分析目录不能是符号链接")
            if directory.is_dir():
                yield directory

    def _records(self) -> list[tuple[Path, dict]]:
        records, ids = [], set()
        for directory in self._directories():
            row = self._load_directory(directory)
            if row is None:
                continue
            if row["id"] in ids:
                raise ValueError(f"分析标识重复：{row['id']}")
            ids.add(row["id"])
            records.append((directory, row))
        return records

    @staticmethod
    def _write_file(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())

    def _write(self, row: dict, directory: Path | None = None) -> None:
        generation = f"v{row['version']}-{uuid.uuid4().hex}"
        destination = directory if directory is not None else self.root / row["id"]
        stored = dict(row, source=self._source(destination / "revisions" / generation / "REPORT.md"))
        if len(json.dumps(stored, ensure_ascii=False).encode()) > self.MAX_RECORD_BYTES:
            raise ValueError("完整报告与代码超过 2 MiB，请缩短内容或拆分分析")
        creating = directory is None
        base = Path(tempfile.mkdtemp(prefix=".analysis-", dir=self.root)) if creating else directory
        metadata_temp, revision = None, None
        try:
            revisions = self._safe_file(base, "revisions")
            revisions.mkdir(exist_ok=True)
            revision = revisions / generation
            revision.mkdir()
            prefix = f"revisions/{generation}"
            report = row["report"].encode("utf-8")
            document = (json.dumps({field: row[field] for field in self.SECTIONS},
                                   ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            self._write_file(revision / "REPORT.md", report)
            self._write_file(revision / "document.json", document)
            codes = []
            for code in row["codes"]:
                content = code["content"].encode("utf-8")
                self._write_file(revision / "code" / code["name"], content)
                codes.append({"name": code["name"], "path": f"{prefix}/code/{code['name']}",
                              "sha256": hashlib.sha256(content).hexdigest()})
            metadata = {key: row[key] for key in ("id", "title", "tags", "related_experiments", "status",
                                                "version", "created_at", "updated_at", "historical",
                                                "attachments_missing")}
            metadata.update(schema_version=self.SCHEMA_VERSION, report_path=f"{prefix}/REPORT.md",
                            report_sha256=hashlib.sha256(report).hexdigest(), document_path=f"{prefix}/document.json",
                            document_sha256=hashlib.sha256(document).hexdigest(), code_files=codes)
            descriptor, temporary = tempfile.mkstemp(prefix=".metadata-", suffix=".tmp", dir=base)
            metadata_temp = Path(temporary)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(metadata, target, ensure_ascii=False, indent=2, allow_nan=False)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(metadata_temp, base / "metadata.json")
            if creating:
                os.rename(base, self.root / row["id"])
        except BaseException:
            if creating or revision is not None:
                shutil.rmtree(base if creating else revision, ignore_errors=True)
            raise
        finally:
            if metadata_temp is not None:
                metadata_temp.unlink(missing_ok=True)

    def create(self, payload: dict) -> dict:
        row = self._validate(payload)
        now = _now()
        row.update(id=uuid.uuid4().hex, version=1, created_at=now, updated_at=now,
                   historical=False, attachments_missing=[])
        row["report"] = self._report(row)
        with self._lock():
            self._records()
            self._write(row)
            return self.get(row["id"])

    def update(self, record_id: str, payload: dict) -> dict:
        self._validate_id(record_id)
        row = self._validate(payload)
        with self._lock():
            matches = [(directory, current) for directory, current in self._records() if current["id"] == record_id]
            if not matches:
                raise FileNotFoundError("分析不存在")
            directory, previous = matches[0]
            if previous["historical"]:
                raise ValueError("历史源报告为只读，请新建补充分析")
            version = payload.get("version")
            if type(version) is not int or version != previous["version"]:
                raise ConflictError("记录已被更新，请重新载入后再保存")
            row.update(id=record_id, version=version + 1, created_at=previous["created_at"],
                       updated_at=_now(), historical=False, attachments_missing=[])
            row["report"] = self._report(row)
            self._write(row, directory)
            return self._load_directory(directory)

    def get(self, record_id: str) -> dict:
        self._validate_id(record_id)
        for _, row in self._records():
            if row["id"] == record_id:
                return row
        raise FileNotFoundError("分析不存在")

    def list(self) -> list[dict]:
        return sorted((row for _, row in self._records()), key=lambda row: row["updated_at"], reverse=True)

    def export(self, record_id: str) -> bytes:
        row = self.get(record_id)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("REPORT.md", row["report"])
            for code in row["codes"]:
                archive.writestr("code/" + code["name"], code["content"])
            metadata = {key: value for key, value in row.items() if key not in {"codes", "report", *self.SECTIONS}}
            metadata.update(schema_version=self.SCHEMA_VERSION, report_path="REPORT.md",
                            report_sha256=hashlib.sha256(row["report"].encode("utf-8")).hexdigest(),
                            code_files=[{"name": code["name"], "path": "code/" + code["name"],
                                         "sha256": hashlib.sha256(code["content"].encode("utf-8")).hexdigest()}
                                        for code in row["codes"]])
            if not row["historical"]:
                document = json.dumps({field: row[field] for field in self.SECTIONS}, ensure_ascii=False, indent=2)
                archive.writestr("document.json", document)
                metadata.update(document_path="document.json",
                                document_sha256=hashlib.sha256(document.encode("utf-8")).hexdigest())
            archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        return output.getvalue()
