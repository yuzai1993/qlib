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

SCHEMA = "qlib-experiment-index-v3"
V2_SCHEMA = LEGACY_V2_SCHEMA = "qlib-experiment-index-v2"
LEGACY_SCHEMA = "qlib-experiment-index-v1"
MAX_JSON_BYTES = 16 * 1024 * 1024
DETAIL_DIRECTORY = "records"
LEGACY_DETAIL_DIRECTORY = "registry_records"
INDEX_FIELDS = (
    "exp_id", "date", "state", "phase", "direction", "phase_m_protocol",
    "report_kind", "display_name", "baseline_ref", "baseline_version",
    "is_current_baseline", "conclusion", "promoted_to", "promoted_from",
)
_TRANSPORT = {"_registry_schema", "_archive_kind", "detail_path", "detail_sha256"}


def _archive_module():
    if __package__:
        from . import experiment_archive
    else:
        import experiment_archive
    return experiment_archive


def _repository_root(path: Path) -> Path | None:
    directory = path.parent.resolve()
    if directory.name == "experiments" and directory.parent.name == "backtest":
        return directory.parent.parent
    return None


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
    if schema in {V2_SCHEMA, SCHEMA}:
        parts = _experiment_parts(row.get("exp_id"))
        if schema == SCHEMA:
            kind = row.get("_archive_kind")
            if kind not in ("model", "strategy"):
                raise ValueError("registry archive kind must be model or strategy")
            parts.insert(0, "models" if kind == "model" else "strategies")
        return "/".join([*parts, DETAIL_DIRECTORY, f"{digest}.json"])
    raise ValueError(f"unsupported registry schema: {schema!r}")


def _index(row: dict, digest: str, schema: str = SCHEMA) -> dict:
    transport = {}
    if schema == SCHEMA:
        transport["_archive_kind"] = _archive_module().classify_experiment(row)["kind"]
    return {
        "_registry_schema": schema,
        **transport,
        **{key: row[key] for key in INDEX_FIELDS if key in row},
        "detail_path": _detail_relative({**row, **transport}, digest, schema),
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


def _detail_path(registry_path: Path, entry: dict, *, for_write: bool = False) -> Path:
    digest = entry.get("detail_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("registry detail SHA256 must be 64 lowercase hex characters")
    relative = _detail_relative(entry, digest, entry["_registry_schema"])
    if entry.get("detail_path") != relative:
        raise ValueError("registry detail path must match its content-addressed filename")
    raw_base = Path(registry_path).parent
    if raw_base.name == "experiments" and raw_base.parent.name == "backtest":
        _archive_module()._safe_path(raw_base.parent.parent.resolve(), "backtest/experiments")
    base = Path(registry_path).parent.resolve()
    path = base
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError("registry detail symlinks are not allowed")
    root = _repository_root(Path(registry_path))
    if root is not None:
        if __package__:
            from .research_layout import relocate_path, resolve_research_path
        else:
            from research_layout import relocate_path, resolve_research_path
        archive = _archive_module()
        archive._safe_path(root, "backtest/experiments/_catalog/path_map.json")
        logical = path.relative_to(root).as_posix()
        # Inspect the un-resolved physical spelling before resolve() can hide a
        # symlink that points back inside the repository.
        archive._safe_path(root, relocate_path(logical, root))
        path = resolve_research_path(logical, root)
        if entry["_registry_schema"] == SCHEMA:
            kind = entry["_archive_kind"]
            archive._kind_path(root, path.relative_to(root).as_posix(), kind)
            if path.name != f"{digest}.json" or path.parent.name != DETAIL_DIRECTORY:
                raise ValueError("registry physical detail must retain its content-addressed filename")
            if for_write:
                directory = archive.archive_directory(root, entry["exp_id"], kind)
                if path.parent.parent != directory:
                    raise ValueError("registry detail alias conflicts with the experiment archive")
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
    if not isinstance(entry["_registry_schema"], str) or entry["_registry_schema"] not in {SCHEMA, V2_SCHEMA, LEGACY_SCHEMA}:
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
    if _repository_root(path) is not None:
        return True
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
    root = _repository_root(registry_path)
    entry = row
    if _indexed(registry_path, indexed):
        payload = _json_bytes(row)
        if len(payload) > MAX_JSON_BYTES:
            raise ValueError("registry detail exceeds 16 MiB")
        digest = hashlib.sha256(payload).hexdigest()
        entry = _index(row, digest, SCHEMA if root is not None else V2_SCHEMA)
        detail = _detail_path(registry_path, entry, for_write=True)
        if detail.exists():
            if detail.read_bytes() != payload:
                raise ValueError("content-addressed registry detail was modified")
        else:
            _atomic_write(detail, payload)
    if root is not None:
        _archive_module().ensure_experiment_metadata(root, row, atomic_writer=_atomic_write)
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def _prepare_archive_binding(path: Path, row: dict) -> None:
    """Prepare category aliases only while a formal writer holds its lock."""
    if not isinstance(row, dict) or _TRANSPORT.intersection(row):
        raise ValueError("expected a complete registry event, not an index entry")
    root = _repository_root(path)
    if root is not None:
        archive = _archive_module()
        archive.ensure_archive_directory_alias(root, row.get("exp_id"), archive.classify_experiment(row)["kind"],
                                               atomic_writer=_atomic_write)


def write_registry(path: Path, rows: Iterable[dict], *, indexed: bool | None = None) -> None:
    """Atomically replace an index after all referenced details are durable."""
    path = Path(path)
    with _registry_lock(path):
        originals = {}
        if path.exists():
            history = load_registry(path)  # Never overwrite unreadable history with a partial view.
            if _repository_root(path) is not None:
                lines = [line for line in _read_text(path).splitlines() if line.strip()]
                for row, line in zip(history, lines):
                    if indexed is True and "_registry_schema" not in json.loads(line):
                        continue
                    originals.setdefault(_json_bytes(row), []).append(line)
        mode = _indexed(path, indexed)
        lines = []
        for row in rows:
            _prepare_archive_binding(path, row)
            existing = originals.get(_json_bytes(row))
            if existing:
                lines.append(existing.pop(0))
                _archive_module().ensure_experiment_metadata(_repository_root(path), row, atomic_writer=_atomic_write)
            else:
                lines.append(encode_registry_row(path, row, indexed=mode))
        payload = "".join(line + "\n" for line in lines).encode("utf-8")
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
        _prepare_archive_binding(path, row)
        line = encode_registry_row(path, row, indexed=indexed).encode("utf-8")
        separator = b"\n" if original and not original.endswith((b"\n", b"\r")) else b""
        payload = original + separator + line + b"\n"
        if len(payload) > MAX_JSON_BYTES:
            raise ValueError("registry exceeds 16 MiB")
        _atomic_write(path, payload)
