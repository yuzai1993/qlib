"""Safe, dependency-free discovery and maintenance of experiment archives.

Metadata describes assets, never registry status or baseline authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter
from datetime import date, datetime
from pathlib import Path

MAX_METADATA_BYTES = 1024 * 1024
KINDS = {"model": "models", "strategy": "strategies"}
EXPERIMENT_TYPES = {
    "training_target": "训练目标", "early_stopping": "早停方法", "model_structure": "模型结构",
    "features": "特征", "evaluation_method": "评估方法", "strategy_backtest": "策略回测",
    "uncategorized": "未分类",
}
INVENTORY_FIELDS = ("configs", "reports", "docs", "notes", "data", "metrics", "shared_assets", "records",
                    "recipe_sources", "recipe_session_metadata", "archived", "sources")


def experiment_parts(exp_id: object) -> list[str]:
    if not isinstance(exp_id, str) or len(exp_id) > 512:
        raise ValueError("exp_id must be a safe POSIX experiment path")
    parts = exp_id.split("/")
    if any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part) for part in parts):
        raise ValueError("exp_id must contain safe POSIX path components")
    return parts


def _safe_path(base: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("asset path must be a local relative POSIX path")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} or "\x00" in part for part in parts):
        raise ValueError("asset path must not contain traversal or absolute components")
    path = base
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("archive symlinks are not allowed")
    try:
        path.resolve().relative_to(base.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("archive path escapes its root") from exc
    return path


def _kind_path(root: Path, relative: object, kind: str) -> Path:
    path = _safe_path(root, relative)
    base = _safe_path(root, f"backtest/experiments/{KINDS[kind]}")
    if path == base or not path.is_relative_to(base):
        raise ValueError("archive path must stay within its kind directory")
    return path


def _mapped_directory(root: Path, exp_id: str, kind: str) -> Path:
    if __package__:
        from .research_layout import relocate_path
    else:
        from research_layout import relocate_path
    relative = f"backtest/experiments/{KINDS[kind]}/{exp_id}"
    _kind_path(root, relative, kind)  # Reject original symlink spellings as well.
    _safe_path(root, "backtest/experiments/_catalog/path_map.json")
    return _kind_path(root, relocate_path(relative, root), kind)


def _validate_analysis(base: Path, value: object) -> None:
    fields = {"conclusion", "limitations", "evidence", "review_status", "updated_at"}
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError("analysis must be an object containing only editorial fields")
    for field in ("conclusion", "limitations"):
        if field in value and not isinstance(value[field], str):
            raise ValueError(f"analysis {field} must be a string")
    if "evidence" in value:
        if not isinstance(value["evidence"], list):
            raise ValueError("analysis evidence must be a list of local paths")
        for item in value["evidence"]:
            _safe_path(base, item)
    if "review_status" in value and value["review_status"] not in ("draft", "reviewed"):
        raise ValueError("analysis review_status must be draft or reviewed")
    if "updated_at" in value and value["updated_at"] not in (None, ""):
        updated = value["updated_at"]
        if not isinstance(updated, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.+)?", updated):
            raise ValueError("analysis updated_at must be an empty or ISO date string")
        try:
            date.fromisoformat(updated[:10])
            if len(updated) > 10:
                datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("analysis updated_at must be a valid ISO date string") from exc


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate metadata key: {key}")
        result[key] = value
    return result


def _load_object_with_revision(path: Path, *, max_bytes: int, label: str) -> tuple[dict, str]:
    if path.is_symlink():
        raise ValueError(f"{label} symlinks are not allowed")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"{label} must be a regular file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"{label} must be a regular file")
        payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes")
    value = json.loads(payload, object_pairs_hook=_object)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value, hashlib.sha256(payload).hexdigest()


def _load_object(path: Path, *, max_bytes: int, label: str) -> dict:
    return _load_object_with_revision(path, max_bytes=max_bytes, label=label)[0]


def _load_metadata(path: Path) -> dict:
    return _load_object(path, max_bytes=MAX_METADATA_BYTES, label="archive metadata")


def _validate_asset(base: Path, value, field: str) -> None:
    """Check a resource reference while leaving its editorial annotations opaque."""
    if isinstance(value, str):
        _safe_path(base, value)
        return
    if not isinstance(value, dict) or "path" not in value:
        raise ValueError(f"archive {field} inventory entry must have a path")
    _safe_path(base, value["path"])
    if "sha256" in value and (not isinstance(value["sha256"], str)
                             or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])):
        raise ValueError(f"archive {field} SHA256 must be lowercase hex")


def _validate_source_tree(base: Path, value) -> None:
    """Preserve legacy grouped source inventories in addition to flat lists."""
    if isinstance(value, list):
        for item in value:
            _validate_source_tree(base, item)
    elif isinstance(value, dict) and "path" not in value:
        for item in value.values():
            _validate_source_tree(base, item)
    else:
        _validate_asset(base, value, "sources")


def _validate_metadata(root: Path, path: Path, value: dict, kind: str) -> None:
    if value.get("schema_version") != 2 or value.get("entity_type") not in ("experiment", "experiment_group"):
        raise ValueError("archive requires schema_version=2 and a declared experiment entity_type")
    if value.get("entity_type") == "experiment_group":
        _validate_experiment_group(value, kind)
    elif "experiment_group" in value:
        raise ValueError("experiment_group descriptor requires entity_type=experiment_group")
    parts = experiment_parts(value.get("exp_id"))
    if value.get("kind") != kind:
        raise ValueError("archive kind conflicts with its directory")
    expected = (_kind_path(root, value["archive_path"], kind) if "archive_path" in value
                else root / "backtest/experiments" / KINDS[kind] / Path(*parts))
    if path.parent != expected:
        raise ValueError("archive directory conflicts with exp_id")
    if "experiment_type" in value and (not isinstance(value["experiment_type"], str)
                                       or value["experiment_type"] not in EXPERIMENT_TYPES):
        raise ValueError("experiment_type must be a declared experiment category")
    if "analysis" in value:
        _validate_analysis(path.parent, value["analysis"])
    if "model_profile" in value:
        _validate_model_profile(value["model_profile"])
    if "historical_baselines" in value:
        sources = value["historical_baselines"]
        if kind != "model" or not isinstance(sources, list) or len(sources) > 16:
            raise ValueError("historical_baselines must be a bounded list of model source records")
        paths = []
        for source in sources:
            if (not isinstance(source, dict) or not {"path", "sha256"} <= set(source)
                    or set(source) - {"path", "sha256", "profile"}):
                raise ValueError("historical_baselines requires local path and SHA256")
            _validate_asset(path.parent, source, "historical_baselines")
            if "profile" in source:
                profile = source["profile"]
                if not isinstance(profile, dict) or set(profile) != {"path", "sha256"}:
                    raise ValueError("historical_baselines profile requires path and SHA256")
                _validate_asset(path.parent, profile, "historical baseline profile")
            paths.append(source["path"])
        if len(set(paths)) != len(paths):
            raise ValueError("historical_baselines source paths must be unique")
    if "benchmark_outputs" in value:
        outputs = value["benchmark_outputs"]
        if not isinstance(outputs, list) or len(outputs) > 16:
            raise ValueError("benchmark_outputs must be a bounded list of references")
        refs = []
        for output in outputs:
            if (not isinstance(output, dict) or set(output) != {"ref", "version"}
                    or not isinstance(output["version"], str) or not output["version"].strip()):
                raise ValueError("benchmark_outputs requires ref and version")
            experiment_parts(output["ref"])
            refs.append(output["ref"])
        if len(set(refs)) != len(refs):
            raise ValueError("benchmark_outputs references must be unique")
    if "training_target_comparison" in value:
        group = value["training_target_comparison"]
        if (kind != "model" or not isinstance(group, dict)
                or not {"refs", "control_ref"} <= set(group)
                or set(group) - {"refs", "control_ref", "archived_records"}
                or not isinstance(group["refs"], list) or not 2 <= len(group["refs"]) <= 8
                or any(not isinstance(ref, str) for ref in group["refs"])
                or len(set(group["refs"])) != len(group["refs"])
                or group["control_ref"] not in group["refs"]
                or value["exp_id"] in group["refs"]):
            raise ValueError("training_target_comparison requires unique refs and a referenced control_ref")
        try:
            for ref in group["refs"]:
                experiment_parts(ref)
        except ValueError as exc:
            raise ValueError("training_target_comparison requires safe experiment references") from exc
        if "archived_records" in group:
            archived = group["archived_records"]
            if not isinstance(archived, list) or len(archived) > len(group["refs"]):
                raise ValueError("training_target_comparison archived_records must be bounded")
            paths = []
            for item in archived:
                if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                    raise ValueError("training_target_comparison archived_records requires path/SHA256")
                _validate_asset(path.parent, item, "training_target_comparison archived_records")
                paths.append(item["path"])
            if len(set(paths)) != len(paths):
                raise ValueError("training_target_comparison archived_records must be unique")
    if "change_summary" in value and not isinstance(value["change_summary"], str):
        raise ValueError("change_summary must be a string")
    if "comparison_baseline" in value:
        baseline = value["comparison_baseline"]
        if (not isinstance(baseline, dict) or not {"ref", "version"} <= set(baseline)
                or set(baseline) - {"ref", "version", "initial"}
                or any(not isinstance(baseline[k], str) for k in ("ref", "version"))
                or ("initial" in baseline and type(baseline["initial"]) is not bool)):
            raise ValueError("comparison_baseline requires ref/version strings and optional initial boolean")
        if baseline.get("initial") and (baseline["ref"] or baseline["version"]):
            raise ValueError("an initial baseline cannot declare a predecessor")
        if baseline["ref"]:
            experiment_parts(baseline["ref"])
            if baseline["ref"] == "self":
                raise ValueError("comparison_baseline must identify the historical comparison, not self")
        if baseline["version"] and not baseline["ref"]:
            raise ValueError("comparison_baseline version requires a reference")
    if "model_ref" in value:
        experiment_parts(value["model_ref"])
    if "strategy_summary" in value:
        summary = value["strategy_summary"]
        if not isinstance(summary, dict) or set(summary) != {"name", "description"}:
            raise ValueError("strategy_summary requires name and description strings")
        if any(not isinstance(summary[key], str) or not summary[key].strip() for key in ("name", "description")):
            raise ValueError("strategy_summary name and description must be nonempty strings")
    if value.get("track") not in ("all_a", "csi1000"):
        raise ValueError("archive track must be all_a or csi1000")
    if not isinstance(value.get("title"), str) or not value["title"].strip():
        raise ValueError("archive title is required")
    if "metrics_source" in value:
        source = value["metrics_source"]
        if not isinstance(source, dict):
            raise ValueError("metrics_source must be an object")
        _validate_asset(path.parent, source, "metrics_source")
    if "annualized_return_basis" in value:
        basis = value["annualized_return_basis"]
        if not isinstance(basis, dict) or basis.get("kind") not in ("arithmetic", "cagr"):
            raise ValueError("annualized_return_basis requires kind arithmetic or cagr")
        _safe_path(path.parent, basis.get("path"))
        if not isinstance(basis.get("source_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", basis["source_sha256"]):
            raise ValueError("annualized_return_basis requires a lowercase source SHA256")
    # Old inventories explicitly declare repository-relative asset snapshots.
    inventory_root = root if value.get("inventory_kind") == "asset_snapshot" else path.parent
    for field in INVENTORY_FIELDS:
        if field not in value:
            continue
        inventory = value[field]
        if field == "sources" and isinstance(inventory, (dict, list)):
            _validate_source_tree(inventory_root, inventory)
            continue
        if not isinstance(inventory, list):
            raise ValueError(f"archive {field} inventory must be a list")
        for item in inventory:
            _validate_asset(inventory_root, item, field)
    if "records_directory" in value:
        _safe_path(inventory_root, value["records_directory"])


def _validate_model_profile(value: object) -> None:
    # Accept the former combined field for old archives, without inferring
    # new editorial facts from it. New profiles store each concept separately.
    text_fields = {"structure", "features", "training_target", "early_stopping", "prediction_signal", "evaluation_method", "changes"}
    sample_fields = {"training_sample", "evaluation_sample"}
    if not isinstance(value, dict) or set(value) - text_fields - sample_fields - {"model_paths"}:
        raise ValueError("model_profile contains only descriptive recipe fields")
    for key in text_fields & set(value):
        if not isinstance(value[key], str):
            raise ValueError(f"model_profile {key} must be text")
    for key in sample_fields & set(value):
        sample = value[key]
        if (not isinstance(sample, dict) or set(sample) - {"pool", "time_range", "filters"}
                or any(not isinstance(item, str) for item in sample.values())):
            raise ValueError(f"model_profile {key} requires pool/time_range/filters text")
    if "model_paths" in value:
        paths = value["model_paths"]
        if not isinstance(paths, list) or len(paths) > 128:
            raise ValueError("model_profile model_paths must be a bounded list")
        for item in paths:
            if not isinstance(item, dict) or "path" not in item or set(item) - {"path", "seed", "label"}:
                raise ValueError("each model path requires path and optional seed/label")
            raw = item["path"]
            if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
                raise ValueError("model path must be repository relative")
            parts = raw.split("/")
            if any(part in {"", ".", ".."} for part in parts) or not (
                parts[:2] in [["backtest", "result"], ["backtest", "models"], ["live_trading", "models"]]
                or parts[0] == "mlruns"
            ) or len(parts) < 3:
                raise ValueError("model path must remain in research/model artifact directories")
            if "seed" in item and (type(item["seed"]) is not int or item["seed"] < 0):
                raise ValueError("model path seed must be a nonnegative integer")
            if "label" in item and not isinstance(item["label"], str):
                raise ValueError("model path label must be text")


def _validate_experiment_group(value: dict, kind: str) -> None:
    """A parameter-study view can describe sources, never become their authority."""
    forbidden = {"current", "current_baseline", "current_bt", "is_baseline", "baseline_version", "bt_version", "baseline_ref",
                 "promoted_to", "promoted_from", "promoted_version", "benchmark_outputs",
                 "metrics_source", "historical_baselines", "training_target_comparison", "model_profile"}
    if kind != "strategy" or value.get("kind") != "strategy" or forbidden & set(value):
        raise ValueError("experiment groups must be strategies without baseline, promotion or numeric authority")
    if value.get("role") not in (None, "experiment", "experiment_group"):
        raise ValueError("experiment groups cannot declare a baseline role")
    experiment_parts(value.get("model_ref"))
    descriptor = value.get("experiment_group")
    if (not isinstance(descriptor, dict)
            or set(descriptor) != {"control_ref", "members", "axes", "fixed_summary"}):
        raise ValueError("experiment_group requires control_ref, members, axes and fixed_summary")
    members = descriptor["members"]
    if not isinstance(members, list) or not 2 <= len(members) <= 32:
        raise ValueError("experiment_group requires 2–32 unique source members")
    refs = []
    for member in members:
        if (not isinstance(member, dict) or set(member) != {"ref", "label"}
                or not isinstance(member["label"], str) or not member["label"].strip()
                or len(member["label"]) > 200):
            raise ValueError("experiment_group members require safe references and nonempty labels")
        experiment_parts(member["ref"])
        refs.append(member["ref"])
    if (len(set(refs)) != len(refs) or value.get("exp_id") in refs
            or descriptor["control_ref"] not in refs):
        raise ValueError("experiment_group requires unique members and a control among those members")
    axes = descriptor["axes"]
    if (not isinstance(axes, list) or not 1 <= len(axes) <= 16
            or any(not isinstance(axis, str) or not axis.strip() or len(axis) > 120 for axis in axes)
            or len(set(axes)) != len(axes)
            or not isinstance(descriptor["fixed_summary"], str) or not descriptor["fixed_summary"].strip()):
        raise ValueError("experiment_group requires bounded unique axes and a fixed protocol summary")


def scan_experiment_archives(repo_root: Path) -> dict:
    """Discover typed manifests, retaining diagnostics instead of guessing identity."""
    root = Path(repo_root).resolve()
    base = root / "backtest/experiments"
    result = {"mode": "legacy", "experiments": [], "groups": [], "errors": []}
    if (root / "backtest").is_symlink() or base.is_symlink():
        result["mode"] = "typed"
        result["errors"].append({"path": "backtest/experiments", "message": "archive ancestors must not be symlinks"})
        return result
    if not any((base / folder).exists() or (base / folder).is_symlink() for folder in KINDS.values()):
        return result
    result["mode"] = "typed"
    candidates = []

    def error(path, message, exp_id=None):
        item = {"path": path.relative_to(root).as_posix(), "message": str(message)}
        if isinstance(exp_id, str):
            item["exp_id"] = exp_id
        result["errors"].append(item)

    for kind, folder in KINDS.items():
        start = base / folder
        if start.is_symlink():
            error(start, "archive root symlinks are not allowed")
            continue
        if not start.exists():
            continue
        if not start.is_dir():
            error(start, "archive root must be a directory")
            continue
        for directory, dirs, files in os.walk(start, followlinks=False):
            directory = Path(directory)
            for name in list(dirs):
                child = directory / name
                if child.is_symlink():
                    error(child, "archive directory symlinks are not allowed")
                    dirs.remove(name)
                elif name == "metadata.json":
                    error(child, "archive metadata must be a regular file")
                    dirs.remove(name)
            if "metadata.json" not in files:
                continue
            path = directory / "metadata.json"
            value = None
            try:
                value, revision = _load_object_with_revision(path, max_bytes=MAX_METADATA_BYTES, label="archive metadata")
                exp_id = value.get("exp_id")
                experiment_parts(exp_id)
                candidates.append((path, value, kind, revision))
            except (OSError, ValueError, UnicodeError, RecursionError) as exc:
                error(path, exc, value.get("exp_id") if value else None)
    counts = Counter(value["exp_id"] for _, value, _, _ in candidates)
    for path, value, kind, revision in candidates:
        try:
            if counts[value["exp_id"]] > 1:
                raise ValueError("duplicate experiment exp_id across archive manifests")
            _validate_metadata(root, path, value, kind)
            bucket = "groups" if value.get("entity_type") == "experiment_group" else "experiments"
            result[bucket].append({**value, "directory": path.parent.relative_to(root).as_posix(),
                                           "metadata_path": path.relative_to(root).as_posix(),
                                           "metadata_revision": revision})
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            error(path, exc, value["exp_id"])
    result["experiments"].sort(key=lambda value: value["exp_id"])
    result["groups"].sort(key=lambda value: value["exp_id"])
    return result


def archive_directory(repo_root: Path, exp_id: str, kind: str | None = None) -> Path:
    """Locate an unambiguous validated archive, or an explicit new typed path."""
    experiment_parts(exp_id)
    root = Path(repo_root).resolve()
    if kind is not None and kind not in KINDS:
        raise ValueError("archive kind must be model or strategy")
    mapped = _mapped_directory(root, exp_id, kind) if kind is not None else None
    scan = scan_experiment_archives(root)
    for error in scan["errors"]:
        if error.get("exp_id") == exp_id:
            raise ValueError(error["message"])
    for value in scan["experiments"]:
        if value["exp_id"] == exp_id:
            if kind is not None and value["kind"] != kind:
                raise ValueError("existing archive metadata has a different kind")
            physical = root / value["directory"]
            logical = root / f"backtest/experiments/{KINDS[value['kind']]}/{exp_id}"
            if mapped is not None and mapped != logical and mapped != physical:
                raise ValueError("archive directory alias conflicts with its metadata declaration")
            return physical
    if mapped is not None:
        metadata_path = mapped / "metadata.json"
        if metadata_path.exists() or metadata_path.is_symlink():
            value = _load_metadata(metadata_path)
            _validate_metadata(root, metadata_path, value, kind)
            if value["exp_id"] != exp_id:
                raise ValueError("existing archive metadata has a different exp_id")
        return mapped
    raise ValueError(f"no unambiguous validated archive for exp_id {exp_id!r}")


def ensure_archive_directory_alias(repo_root: Path, exp_id: str, kind: str, *, atomic_writer) -> Path:
    """Bind a uniquely declared category inside the caller's registry transaction.

    Only an unmapped, empty logical location can gain a new binding. Existing
    aliases and populated legacy archives require an explicit migration instead.
    """
    if __package__:
        from .research_layout import MAP_PATH, MAX_MAP_BYTES, _resolve_key, relocate_path
    else:
        from research_layout import MAP_PATH, MAX_MAP_BYTES, _resolve_key, relocate_path
    root = Path(repo_root).resolve()
    mapping_path = _safe_path(root, MAP_PATH.as_posix())
    document = (_load_object(mapping_path, max_bytes=MAX_MAP_BYTES, label="research layout map")
                if mapping_path.exists() else {"schema_version": 1, "paths": {}, "directories": {}})
    directory = archive_directory(root, exp_id, kind)
    logical = f"backtest/experiments/{KINDS[kind]}/{exp_id}"
    current = _mapped_directory(root, exp_id, kind)
    if current == directory:
        return directory
    if current != root / logical:
        raise ValueError("archive directory alias conflicts with its metadata declaration")
    source = root / logical
    if source.exists() and any(item.is_file() or item.is_symlink() for item in source.rglob("*")):
        raise ValueError("populated logical archive requires an explicit migration alias")
    # archive_directory already validated the declaration and rejected duplicate
    # IDs. Validate the existing map before preserving all its unrelated fields.
    if relocate_path(logical, root) != logical:
        raise ValueError("archive directory alias is already declared")
    destination = directory.relative_to(root).as_posix()
    if relocate_path(destination, root) != destination:
        raise ValueError("declared archive_path conflicts with an existing directory alias")
    directories = dict(document.get("directories", {}))
    directories[logical] = destination
    paths = document["paths"]
    # Validate the prospective graph in memory. A rejected append must never
    # publish an alias that makes otherwise readable history cyclic.
    for candidate in set(paths) | set(paths.values()) | set(directories) | set(directories.values()):
        _resolve_key(candidate, paths, directories)
    if _resolve_key(logical, paths, directories) != destination:
        raise ValueError("new archive directory alias has a conflicting final destination")
    document["directories"] = directories
    atomic_writer(mapping_path, (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode())
    return directory


def classify_experiment(row: dict) -> dict:
    """Classify historical research identity without changing phase or direction."""
    report = str(row.get("report_kind") or "")
    kind = "strategy" if row.get("phase") == "S" or report.startswith("phase_m_v1_b") else "model"
    all_a = row.get("phase_m_protocol") == "v1" or report.startswith("phase_m_v1_b")
    if kind == "model" and (report == "eval_protocol_diag" or str(row.get("exp_id", "")).startswith("diag/")):
        source = " ".join(str(row.get(key, "")) for key in ("eval_protocol", "baseline_ref", "hypothesis"))
        all_a = all_a or any(marker in source.lower() for marker in ("全a", "all_a", "all-a", "phase-m-v1"))
    activity = "execution_backtest" if kind == "strategy" and report.startswith("phase_m_v1_b") else (
        "strategy_research" if kind == "strategy" else "evaluation_diagnostic" if report == "eval_protocol_diag" else "model_research")
    return {"kind": kind, "track": "all_a" if all_a else "csi1000", "activity": activity,
            "role": "baseline" if str(row.get("exp_id", "")).startswith("baseline/") else "experiment",
            "changed_axes": row.get("changed_axes", [])}


def ensure_experiment_metadata(repo_root: Path, row: dict, *, atomic_writer=None) -> dict:
    """Create/update a manifest and actual asset inventory, preserving editorial fields."""
    root = Path(repo_root).resolve()
    classification = classify_experiment(row)
    exp_id = row.get("exp_id")
    directory = archive_directory(root, exp_id, classification["kind"])
    path = directory / "metadata.json"
    existing = _load_metadata(path) if path.exists() else {}
    if existing and existing.get("exp_id") != exp_id:
        raise ValueError("existing archive metadata has a different exp_id")
    if existing.get("kind", classification["kind"]) != classification["kind"]:
        raise ValueError("existing archive metadata has a different kind")
    directory.mkdir(parents=True, exist_ok=True)
    metadata = {**existing, **classification, "schema_version": 2, "entity_type": "experiment", "exp_id": exp_id,
                "title": existing.get("title") or row.get("display_name") or row.get("arm") or exp_id,
                "inventory_kind": "asset_snapshot", "registry_authority": "backtest/experiments/registry.jsonl"}
    logical = root / f"backtest/experiments/{KINDS[classification['kind']]}/{exp_id}"
    if directory != logical:
        metadata["archive_path"] = directory.relative_to(root).as_posix()
    for field in ("phase", "direction"):
        if field in row:
            metadata[field] = row[field]
    if __package__:
        from .research_layout import relocate_path
    else:
        from research_layout import relocate_path

    def relocated(item):
        if isinstance(item, str):
            inventory_base = root if existing.get("inventory_kind") == "asset_snapshot" else directory
            relative = _safe_path(inventory_base, item).relative_to(root).as_posix()
            return relocate_path(relative, root)
        if isinstance(item, list):
            return [relocated(value) for value in item]
        if isinstance(item, dict):
            if "path" in item:
                return {**item, "path": relocated(item["path"])}
            return {key: relocated(value) if isinstance(value, (dict, list)) else value for key, value in item.items()}
        return item

    for field in INVENTORY_FIELDS:
        if field in metadata:
            metadata[field] = relocated(metadata[field])
    for field, folders in (("configs", ("configs",)), ("reports", ("reports",)), ("records", ("records",)),
                           ("data", ("data", "metrics")), ("docs", ("docs", "notes"))):
        inventory = metadata.get(field, [])
        if not isinstance(inventory, list):
            raise ValueError(f"archive {field} inventory must be a list")
        assets = {}
        for item in inventory:
            relative = item.get("path") if isinstance(item, dict) else item
            if not isinstance(relative, str):
                raise ValueError(f"archive {field} inventory entry must have a path")
            assets[relative] = item
        for folder in folders:
            asset_root = directory / folder
            if asset_root.exists():
                for asset in sorted(asset_root.rglob("*")):
                    relative = asset.relative_to(root).as_posix()
                    _safe_path(root, relative)
                    if asset.is_file():
                        previous = assets.get(relative)
                        if field == "configs":
                            assets[relative] = {**(previous if isinstance(previous, dict) else {}), "path": relative,
                                                "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}
                        else:
                            assets.setdefault(relative, relative)
        metadata[field] = list(assets.values())
    source_field = "portable_eval_output" if "portable_eval_output" in row else "eval_output"
    if source_field in row:
        # Key presence is authoritative, including a missing/invalid portable
        # source. Registry consumers diagnose that source without a stale alias.
        metadata.pop("metrics_source", None)
        candidate = row[source_field]
        if isinstance(candidate, str) and candidate:
            candidate = relocate_path(candidate, root)
            asset = Path(candidate) if Path(candidate).is_absolute() else root / candidate
            try:
                relative = asset.relative_to(directory).as_posix()
                asset = _safe_path(directory, relative)
            except ValueError:
                pass
            else:
                metadata["metrics_source"] = {"path": relative}
                if asset.is_file():
                    metadata["metrics_source"]["sha256"] = hashlib.sha256(asset.read_bytes()).hexdigest()
    metadata["records_directory"] = (directory / "records").relative_to(root).as_posix()
    _validate_metadata(root, path, metadata, classification["kind"])
    if atomic_writer is None:
        if __package__:
            from .experiment_registry import _atomic_write
        else:
            from experiment_registry import _atomic_write
        atomic_writer = _atomic_write
    atomic_writer(path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode())
    return metadata
