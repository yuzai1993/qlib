"""Resolve explicit research-asset relocations without changing frozen records.

The map records concrete old/new repository paths. Unknown historical paths,
including cleaned result sessions, retain their original meaning. No artifact
is deserialized or inferred from a filename during resolution.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path, PurePosixPath

MAP_PATH = Path("backtest/experiments/_catalog/path_map.json")
MAX_MAP_BYTES = 8 * 1024 * 1024


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("research layout paths must be nonempty POSIX strings")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("research layout paths must be normalized repository-relative paths")
    if path.parts[0] not in {"backtest", "docs"}:
        raise ValueError("research layout paths must stay in backtest/ or docs/")
    return value


@lru_cache(maxsize=16)
def _read_map(path: Path, mtime_ns: int, size: int) -> dict[str, str]:
    del mtime_ns  # Part of the cache key, invalidated on an atomic map update.
    if size > MAX_MAP_BYTES:
        raise ValueError("research layout map exceeds 8 MiB")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("unsupported research layout map")
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("research layout map must contain a paths object")
    return {_relative(old): _relative(new) for old, new in paths.items()}


def _mapping(root: Path) -> dict[str, str]:
    path = root / MAP_PATH
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("research layout map escapes repository root")
    if not path.exists():
        return {}
    stat = path.stat()
    return _read_map(path, stat.st_mtime_ns, stat.st_size)


def relocate_path(raw: str, repo_root: Path) -> str:
    """Return the mapped spelling, preserving absolute/relative input form."""
    if not isinstance(raw, str):
        raise TypeError("research path must be a string")
    root = Path(repo_root).expanduser().resolve()
    path = Path(raw)
    absolute = path.is_absolute()
    if absolute:
        try:
            key = path.relative_to(root).as_posix()
        except ValueError:
            return raw
    else:
        key = path.as_posix()
    mapping = _mapping(root)
    if key not in mapping:
        return raw
    visited = set()
    while key in mapping:
        if key in visited:
            raise ValueError("research layout map contains a path cycle")
        visited.add(key)
        key = mapping[key]
    if not (root / key).resolve().is_relative_to(root):
        raise ValueError("mapped research path escapes repository root")
    return str(root / key) if absolute else key


def resolve_research_path(raw: Path | str, repo_root: Path) -> Path:
    """Resolve one asset path after applying only declared layout aliases."""
    root = Path(repo_root).expanduser().resolve()
    path = Path(relocate_path(str(raw), root)).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def research_path_identity(raw: str, repo_root: Path) -> str:
    """Keep the earliest declared spelling for a relocated protocol asset.

    This is an identity for comparison contracts, not a filesystem lookup.
    Only explicit map aliases are equivalent; unrelated files are never merged
    by their contents. Absolute and relative spellings keep their original form.
    """
    if not isinstance(raw, str):
        raise TypeError("research path must be a string")
    root = Path(repo_root).expanduser().resolve()
    path = Path(raw)
    if path.as_posix() != raw:
        return raw
    absolute = path.is_absolute()
    if absolute:
        try:
            key = path.relative_to(root).as_posix()
        except ValueError:
            return raw
    else:
        key = path.as_posix()
    mapping = _mapping(root)
    predecessors: dict[str, list[str]] = {}
    for source, destination in mapping.items():
        predecessors.setdefault(destination, []).append(source)
    if key not in mapping and key not in predecessors:
        return raw

    # The canonical asset still has to respect the same path boundary as the
    # read resolver, even when this call starts with the final destination.
    canonical, visited = key, set()
    while canonical in mapping:
        if canonical in visited:
            raise ValueError("research layout map contains a path cycle")
        visited.add(canonical)
        canonical = mapping[canonical]
    if not (root / canonical).resolve().is_relative_to(root):
        raise ValueError("mapped research path escapes repository root")

    visited = set()
    while key in predecessors:
        if key in visited:
            raise ValueError("research layout map contains a path cycle")
        visited.add(key)
        sources = predecessors[key]
        if len(sources) != 1:
            raise ValueError("research layout path has ambiguous original identities")
        key = sources[0]
    return str(root / key) if absolute else key


def report_href(target: Path | str, output: Path | str, repo_root: Path) -> str:
    """Link to a relocated file from the directory of the generated report."""
    target_path = resolve_research_path(target, repo_root)
    output_path = resolve_research_path(output, repo_root)
    return Path(os.path.relpath(target_path, output_path.parent)).as_posix()
