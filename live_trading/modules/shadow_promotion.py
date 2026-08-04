"""Fail-closed retirement of a claimed, unexecuted Shadow batch."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from live_trading.modules.signal_schema import SchemaError


_BATCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_unexecuted_state(payload: dict, batch_id: str) -> None:
    """Reject any active state that might already have reached execution."""
    if payload.get("batch_id") != batch_id:
        raise SchemaError("active state batch_id mismatch")
    if payload.get("phase") != "SELL":
        raise SchemaError("active state phase must still be SELL")
    for field in (
        "trading_started", "execution_authorized", "execution_live",
    ):
        if payload.get(field) is not False:
            raise SchemaError(f"active state {field} must be false")
    if payload.get("submitted") != []:
        raise SchemaError("active state submitted must be empty")
    if payload.get("fills") != {}:
        raise SchemaError("active state fills must be empty")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _copy_verified(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    if _sha256(source) != _sha256(destination):
        raise OSError(f"archive checksum mismatch: {source}")


def _read_shadow_header(path: Path, batch_id: str) -> dict:
    try:
        with path.open("r", encoding="utf-8") as stream:
            first_line = stream.readline()
        header = json.loads(first_line)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"invalid claimed signal header: {exc}") from exc
    if header.get("type") != "batch_header":
        raise SchemaError("claimed signal must start with batch_header")
    if header.get("batch_id") != batch_id:
        raise SchemaError("claimed signal batch_id mismatch")
    if header.get("mode") != "SIMULATE":
        raise SchemaError("claimed signal must be SIMULATE")
    if header.get("account_environment") != "SIMULATION":
        raise SchemaError("claimed signal account_environment must be SIMULATION")
    return header


def retire_claimed_shadow(
    bridge_root: Path,
    batch_id: str,
    *,
    execute: bool,
    now: datetime | None = None,
) -> dict:
    """Inspect or archive one exact claimed Shadow batch.

    The caller must stop the QMT strategy before using ``execute=True``.
    Files are copied and verified before any known source is removed.
    """
    if not _BATCH_ID_RE.fullmatch(batch_id):
        raise SchemaError("batch_id contains unsafe path characters")

    root = Path(bridge_root).resolve()
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    archive_dir = root / "archive" / (
        f"operator_retired_{batch_id}_{timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    )
    sources = [
        root / "processing" / f"signal_{batch_id}.jsonl",
        root / "processing" / f"signal_{batch_id}.done",
        root / "state" / f"active_{batch_id}.json",
    ]
    for source in sources:
        if not source.is_file():
            raise SchemaError(f"required claimed batch file missing: {source}")

    _read_shadow_header(sources[0], batch_id)
    try:
        state = json.loads(sources[2].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"invalid active state: {exc}") from exc
    validate_unexecuted_state(state, batch_id)

    files = [{
        "archive": str(archive_dir / source.name),
        "sha256": _sha256(source),
        "size": source.stat().st_size,
        "source": str(source),
    } for source in sources]
    manifest = {
        "archive_dir": str(archive_dir),
        "batch_id": batch_id,
        "files": files,
        "retired_at": timestamp.isoformat(),
        "status": "dry_run",
    }
    if not execute:
        return manifest

    archive_dir.mkdir(parents=True, exist_ok=False)
    for row in files:
        _copy_verified(Path(row["source"]), Path(row["archive"]))

    manifest["status"] = "retired"
    manifest_path = archive_dir / "retirement_manifest.json"
    temporary = archive_dir / ".retirement_manifest.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(manifest_path)

    for row in files:
        Path(row["source"]).unlink()
    return manifest
