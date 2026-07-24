"""Safe loading for Git-tracked live model artifacts."""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path


def load_model_artifact(
    model_path: str | Path,
    expected_sha256: str,
    *,
    project_root: Path,
):
    """Load a model inside ``project_root`` after verifying its SHA-256."""
    if not model_path:
        raise ValueError("model_path is required")
    if not expected_sha256:
        raise ValueError("model sha256 is required")

    root = project_root.resolve()
    candidate = Path(model_path)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (root / candidate).resolve()
    )
    if not resolved.is_relative_to(root):
        raise ValueError(f"model_path must stay inside project root: {model_path}")
    if not resolved.is_file():
        raise FileNotFoundError(f"Model artifact not found at {resolved}")

    model_bytes = resolved.read_bytes()
    actual_sha256 = hashlib.sha256(model_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(
            "Model SHA-256 mismatch: "
            f"expected={expected_sha256}, actual={actual_sha256}, path={resolved}"
        )
    return pickle.loads(model_bytes), resolved
