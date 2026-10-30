"""Lossless experiment registry storage, shared by reports, writers and readers.

The JSONL file is an ordered event index. Each indexed event points at an
immutable, content-addressed JSON containing the entire original record. Legacy
inline events remain readable; callers always receive the original dictionary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

SCHEMA = "qlib-experiment-index-v2"
LEGACY_SCHEMA = "qlib-experiment-index-v1"
MAX_JSON_BYTES = 16 * 1024 * 1024
DETAIL_DIRECTORY = "records"
LEGACY_DETAIL_DIRECTORY = "registry_records"
INDEX_FIELDS = (
    "exp_id", "date", "state", "phase", "direction", "phase_m_protocol",
    "report_kind", "display_name", "baseline_ref", "baseline_version",
    "is_current_baseline", "conclusion", "promoted_to", "promoted_from",
)
_TRANSPORT = {"_registry_schema", "detail_path", "detail_sha256"}


def _json_bytes(row: dict) -> bytes:
    # Keep historical floats, including -0.0 and any legacy non-finite numbers.
    return (json.dumps(row, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _experiment_parts(exp_id: object) -> list[str]:
    if not isinstance(exp_id, str):
        raise ValueError("registry exp_id must be a safe POSIX experiment path")
    parts = exp_id.split("/")
    if any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part) for part in parts):
        raise ValueError("registry exp_id must contain safe POSIX path components")
    return parts


def _detail_relative(row: dict, digest: str, schema: str) -> str:
    if schema == LEGACY_SCHEMA:
        return f"{LEGACY_DETAIL_DIRECTORY}/{digest}.json"
    if schema == SCHEMA:
        parts = _experiment_parts(row.get("exp_id"))
        return "/".join([*parts, DETAIL_DIRECTORY, f"{digest}.json"])
    raise ValueError(f"unsupported registry schema: {schema!r}")


def _index(row: dict, digest: str, schema: str = SCHEMA) -> dict:
    return {
        "_registry_schema": schema,
        **{key: row[key] for key in INDEX_FIELDS if key in row},
        "detail_path": _detail_relative(row, digest, schema),
        "detail_sha256": digest,
    }


def _canonical_write_row(registry_path: Path, row: dict) -> dict:
    """Canonicalize only explicitly relocated asset paths when writing research rows."""
    directory = registry_path.parent.resolve()
    if directory.name != "experiments" or directory.parent.name != "backtest":
        return row
    if not (directory / "_catalog/path_map.json").is_file():
        return row
    root = directory.parent.parent
    if __package__:
        from .research_layout import relocate_path
    else:
        from research_layout import relocate_path

    def relocate(value):
        if isinstance(value, dict):
            return {key: item if key == "exp_id" else relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if isinstance(value, str) and value.startswith(("backtest/", "docs/", str(root) + os.sep)):
            return relocate_path(value, root)
        return value

    return relocate(row)


def _detail_path(registry_path: Path, entry: dict) -> Path:
    digest = entry.get("detail_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("registry detail SHA256 must be 64 lowercase hex characters")
    relative = _detail_relative(entry, digest, entry["_registry_schema"])
    if entry.get("detail_path") != relative:
        raise ValueError("registry detail path must match its content-addressed filename")
    base = Path(registry_path).parent.resolve()
    path = base
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError("registry detail symlinks are not allowed")
    try:
        path.resolve().relative_to(base)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("registry detail escapes its directory") from exc
    return path


def decode_registry_line(line: str, registry_path: Path) -> dict:
    """Hydrate one event, rejecting missing, changed or inconsistent details."""
    entry = json.loads(line)
    if not isinstance(entry, dict):
        raise ValueError("registry event must be a JSON object")
    if "_registry_schema" not in entry:
        if _TRANSPORT.intersection(entry):
            raise ValueError("registry detail reference is missing its schema")
        return entry
    if not isinstance(entry["_registry_schema"], str) or entry["_registry_schema"] not in {SCHEMA, LEGACY_SCHEMA}:
        raise ValueError(f"unsupported registry schema: {entry['_registry_schema']!r}")
    path = _detail_path(registry_path, entry)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("registry detail exceeds 16 MiB")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry["detail_sha256"]:
        raise ValueError(f"registry detail SHA256 mismatch: {entry['detail_path']}")
    row = json.loads(payload)
    if not isinstance(row, dict) or _TRANSPORT.intersection(row):
        raise ValueError("registry detail must be an original event object")
    if entry != _index(row, entry["detail_sha256"], entry["_registry_schema"]):
        raise ValueError("registry index metadata disagrees with its detail")
    return row


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("registry exceeds 16 MiB")
    return path.read_text(encoding="utf-8")


def load_registry(path: Path) -> list[dict]:
    """Return every historical event, in file order, without deduplication."""
    path = Path(path)
    result = []
    for number, line in enumerate(_read_text(path).splitlines(), 1):
        if line.strip():
            try:
                result.append(decode_registry_line(line, path))
            except (OSError, ValueError, UnicodeError) as exc:
                raise ValueError(f"registry line {number}: {exc}") from exc
    return result


def _indexed(path: Path, indexed: bool | None) -> bool:
    if indexed is not None:
        return indexed
    if not path.exists():
        return False
    return any(
        "_registry_schema" in json.loads(line)
        for line in _read_text(path).splitlines() if line.strip()
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("refusing to replace a symlinked registry file")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _registry_lock(path: Path):
    # The registry inode changes on replace. A stable sidecar lock serializes
    # append transactions across both threads and independent Python processes.
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(f".{path.name}.lock")
    if lock.is_symlink():
        raise ValueError("registry lock symlinks are not allowed")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if not os.fstat(descriptor).st_size:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def encode_registry_row(registry_path: Path, row: dict, *, indexed: bool | None = None) -> str:
    """Encode one event, writing its immutable detail before returning the index."""
    registry_path = Path(registry_path)
    if not isinstance(row, dict) or _TRANSPORT.intersection(row):
        raise ValueError("expected a complete registry event, not an index entry")
    row = _canonical_write_row(registry_path, row)
    entry = row
    if _indexed(registry_path, indexed):
        payload = _json_bytes(row)
        if len(payload) > MAX_JSON_BYTES:
            raise ValueError("registry detail exceeds 16 MiB")
        digest = hashlib.sha256(payload).hexdigest()
        entry = _index(row, digest)
        detail = _detail_path(registry_path, entry)
        if detail.exists():
            if detail.read_bytes() != payload:
                raise ValueError("content-addressed registry detail was modified")
        else:
            _atomic_write(detail, payload)
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def write_registry(path: Path, rows: Iterable[dict], *, indexed: bool | None = None) -> None:
    """Atomically replace an index after all referenced details are durable."""
    path = Path(path)
    with _registry_lock(path):
        if path.exists():
            load_registry(path)  # Never overwrite an unreadable history with a partial view.
        mode = _indexed(path, indexed)
        payload = "".join(encode_registry_row(path, row, indexed=mode) + "\n" for row in rows).encode("utf-8")
        if len(payload) > MAX_JSON_BYTES:
            raise ValueError("registry exceeds 16 MiB")
        _atomic_write(path, payload)


def append_registry(path: Path, row: dict, *, indexed: bool | None = None) -> None:
    """Append an event while retaining all existing bytes and storage format."""
    path = Path(path)
    with _registry_lock(path):
        original = b""
        if path.exists():
            load_registry(path)
            original = path.read_bytes()
        line = encode_registry_row(path, row, indexed=indexed).encode("utf-8")
        separator = b"\n" if original and not original.endswith((b"\n", b"\r")) else b""
        payload = original + separator + line + b"\n"
        if len(payload) > MAX_JSON_BYTES:
            raise ValueError("registry exceeds 16 MiB")
        _atomic_write(path, payload)
