"""Persist only human conclusions in validated experiment metadata."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backtest.scripts.experiment_archive import (
    MAX_METADATA_BYTES, _load_object_with_revision, _safe_path, _validate_metadata,
    experiment_parts, scan_experiment_archives,
)
from backtest.scripts.experiment_registry import _atomic_write, _registry_lock
from .analyses import ConflictError


class ResearchAnnotations:
    MAX_CONCLUSION_CHARS = 500

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _find(self, exp_id: str) -> dict:
        discovered = scan_experiment_archives(self.root)
        matches = [m for m in [*discovered["experiments"], *discovered["groups"]] if m["exp_id"] == exp_id]
        if len(matches) != 1:
            raise FileNotFoundError("没有唯一且有效的实验目录")
        return matches[0]

    def update(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"id", "conclusion", "revision"}:
            raise ValueError("仅允许保存实验ID、结论和原文件版本")
        exp_id, conclusion, revision = (payload[k] for k in ("id", "conclusion", "revision"))
        experiment_parts(exp_id)
        if not isinstance(conclusion, str) or not 1 <= len(conclusion.strip()) <= self.MAX_CONCLUSION_CHARS:
            raise ValueError("请填写1–500字符的精简结论")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
            raise ValueError("缺少有效文件版本，请刷新后编辑")
        self._find(exp_id)  # Unknown IDs must not create directories or lock files.
        registry = _safe_path(self.root, "backtest/experiments/registry.jsonl")
        with _registry_lock(registry):
            manifest = self._find(exp_id)
            path = _safe_path(self.root, manifest["metadata_path"])
            metadata, current_revision = _load_object_with_revision(
                path, max_bytes=MAX_METADATA_BYTES, label="archive metadata"
            )
            _validate_metadata(self.root, path, metadata, manifest["kind"])
            if metadata["exp_id"] != exp_id or revision != current_revision:
                raise ConflictError("实验内容已在别处更新，请刷新后重新编辑；本次未覆盖文件")
            analysis = {**metadata.get("analysis", {}), "conclusion": conclusion.strip(),
                        "review_status": "draft",
                        "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()}
            metadata["analysis"] = analysis
            _validate_metadata(self.root, path, metadata, manifest["kind"])
            data = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            _atomic_write(path, data)
        return {"id": exp_id, "analysis": analysis, "analysis_revision": hashlib.sha256(data).hexdigest()}
