"""Read frozen model baselines retained inside a consolidated research archive.

These receipts protect historical comparisons and model artifacts. They are not
active experiment registration or authority to choose a current baseline.
"""

from __future__ import annotations

import re
from pathlib import Path

if __package__:
    from .experiment_archive import (
        _load_object_with_revision, _safe_path, classify_experiment, scan_experiment_archives,
    )
else:
    from experiment_archive import (
        _load_object_with_revision, _safe_path, classify_experiment, scan_experiment_archives,
    )


def load_historical_baselines(repo_root: Path) -> list[dict]:
    """Return original verified rows, failing without partial results on errors."""
    return [source["row"] for source in load_historical_baseline_sources(repo_root)]


def load_historical_baseline_sources(repo_root: Path) -> list[dict]:
    """Include source ownership for benchmark presentation, without granting authority."""
    root = Path(repo_root).resolve()
    scan = scan_experiment_archives(root)
    if scan["errors"]:
        raise ValueError("historical baseline archive discovery failed: " + str(scan["errors"]))
    rows = []
    ids = set()
    for manifest in scan["experiments"]:
        references = manifest.get("historical_baselines", [])
        if not isinstance(references, list) or len(references) > 16:
            raise ValueError("historical_baselines must be a bounded receipt list")
        for reference in references:
            if (manifest["kind"] != "model" or not isinstance(reference, dict)
                    or not {"path", "sha256"} <= set(reference)
                    or set(reference) - {"path", "sha256", "profile"}
                    or not isinstance(reference["sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", reference["sha256"])):
                raise ValueError("historical baseline receipt requires a model path/SHA256")
            path = _safe_path(root / manifest["directory"], reference["path"])
            row, digest = _load_object_with_revision(path, max_bytes=16 * 1024 * 1024,
                                                     label="historical baseline receipt")
            if digest != reference["sha256"]:
                raise ValueError(f"historical baseline receipt SHA256 mismatch: {path}")
            classification = classify_experiment(row)
            version = row.get("baseline_version")
            exp_id = row.get("exp_id")
            if (classification["kind"] != "model" or classification["track"] != manifest["track"]
                    or row.get("phase_m_protocol") != "v1" or row.get("state") != "completed"
                    or not isinstance(version, str) or not re.fullmatch(r"v\d+(?:\.\d+)*", version)
                    or not isinstance(exp_id, str) or not exp_id or exp_id in ids):
                raise ValueError(f"historical baseline receipt identity invalid or duplicated: {path}")
            ids.add(exp_id)
            rows.append({"row": row, "manifest": manifest, "reference": reference})
    return rows
