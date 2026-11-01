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
def _read_map(path: Path, mtime_ns: int, size: int) -> dict[str, dict[str, str]]:
    del mtime_ns  # Part of the cache key, invalidated on an atomic map update.
    if size > MAX_MAP_BYTES:
        raise ValueError("research layout map exceeds 8 MiB")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("unsupported research layout map")
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("research layout map must contain a paths object")
    directories = document.get("directories", {})
    if not isinstance(directories, dict):
        raise ValueError("research layout directories must be an object")
    directories = {_relative(old): _relative(new) for old, new in directories.items()}
    for old, new in directories.items():
        if old == new or new.startswith(old + "/"):
            raise ValueError("research layout directory alias is recursive")
        if not old.startswith("backtest/experiments/") or not new.startswith("backtest/experiments/"):
            raise ValueError("directory aliases must stay in the experiment archive")
    return {
        "paths": {_relative(old): _relative(new) for old, new in paths.items()},
        "directories": directories,
    }


def _maps(root: Path) -> dict[str, dict[str, str]]:
    path = root / MAP_PATH
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("research layout map escapes repository root")
    if not path.exists():
        return {"paths": {}, "directories": {}}
    stat = path.stat()
    return _read_map(path, stat.st_mtime_ns, stat.st_size)


def _mapping(root: Path) -> dict[str, str]:
    return _maps(root)["paths"]


def _directory_target(key: str, directories: dict[str, str]) -> str | None:
    candidates = [source for source in directories if key == source or key.startswith(source + "/")]
    if not candidates:
        return None
    source = max(candidates, key=len)
    return directories[source] + key[len(source):]


def _resolve_key(key: str, paths: dict[str, str], directories: dict[str, str]) -> str:
    visited = set()
    # Explicit directory cycles can grow the suffix and never repeat a full key.
    for _ in range(len(paths) + len(directories) + 2):
        if key in visited:
            raise ValueError("research layout map contains a path cycle")
        visited.add(key)
        target = paths.get(key)
        if target is None:
            target = _directory_target(key, directories)
        if target is None:
            return key
        key = target
    raise ValueError("research layout map contains a recursive directory cycle")


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
    maps = _maps(root)
    mapping, directories = maps["paths"], maps["directories"]
    if key not in mapping and _directory_target(key, directories) is None:
        return raw
    key = _resolve_key(key, mapping, directories)
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
    maps = _maps(root)
    mapping, directories = maps["paths"], maps["directories"]
    predecessors: dict[str, list[str]] = {}
    for source, destination in mapping.items():
        predecessors.setdefault(destination, []).append(source)
    def previous(current: str) -> set[str]:
        candidates = set(predecessors.get(current, []))
        for source, destination in directories.items():
            if current == destination or current.startswith(destination + "/"):
                candidate = source + current[len(destination):]
                # Invert only the actual next hop: an exact alias or a longer
                # directory rule can override this apparent directory edge.
                next_step = mapping.get(candidate)
                if next_step is None:
                    next_step = _directory_target(candidate, directories)
                if next_step == current:
                    candidates.add(candidate)
        return candidates

    if key not in mapping and not previous(key) and _directory_target(key, directories) is None:
        return raw

    # The canonical asset still has to respect the same path boundary as the
    # read resolver, even when this call starts with the final destination.
    canonical = _resolve_key(key, mapping, directories)
    if not (root / canonical).resolve().is_relative_to(root):
        raise ValueError("mapped research path escapes repository root")

    visited = set()
    for _ in range(len(mapping) + len(directories) + 2):
        sources = previous(key)
        if not sources:
            return str(root / key) if absolute else key
        if key in visited:
            raise ValueError("research layout map contains a path cycle")
        visited.add(key)
        if len(sources) != 1:
            raise ValueError("research layout path has ambiguous original identities")
        key = next(iter(sources))
    raise ValueError("research layout map contains a recursive directory cycle")


def report_href(target: Path | str, output: Path | str, repo_root: Path) -> str:
    """Link to a relocated file from the directory of the generated report."""
    target_path = resolve_research_path(target, repo_root)
    output_path = resolve_research_path(output, repo_root)
    return Path(os.path.relpath(target_path, output_path.parent)).as_posix()
