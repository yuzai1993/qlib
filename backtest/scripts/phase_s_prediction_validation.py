"""Authoritative validation for the active Phase S full prediction artifact."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]

from generate_phase_s_predictions import prediction_index_sha256
from phase_s_protocol import FULL_SEGMENT, load_frozen_model, sha256_file

FULL_MODEL_REF = "b6-m"
FULL_POOL = "csi1000"
FULL_SEGMENT_NAME = "full"
FULL_DATA_VERSION = "2026-07-31"
DEFAULT_FULL_PREDICTION_MANIFEST = (
    REPO_ROOT
    / "backtest/experiments/strategy-stability/20260801_full_period"
    / "prediction_manifest.json"
)
FULL_PREDICTION_COVERAGE = {
    "start": "2020-01-13",
    "end": "2026-07-31",
    "n_dates": 1587,
    "n_rows": 1584284,
    "index_sha256": "e6336cd92cc988f71f61afe2907980451ad20201388b6b39a542e469c1313abd",
}


def prediction_path(entry: Mapping[str, Any], repo_root: Path = REPO_ROOT) -> Path:
    raw = entry.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("full prediction path is required")
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (Path(repo_root) / path).resolve()


def full_prediction_entry(manifest: Mapping[str, Any]) -> dict[str, Any]:
    matches = [
        entry
        for entry in manifest.get("predictions") or []
        if (entry.get("model_ref"), entry.get("pool"), entry.get("segment"))
        == (FULL_MODEL_REF, FULL_POOL, FULL_SEGMENT_NAME)
    ]
    if len(matches) != 1:
        raise ValueError(
            "prediction manifest requires exactly one b6-m/csi1000/full artifact"
        )
    return copy.deepcopy(matches[0])


def prediction_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_pickle(path)
    if isinstance(frame, pd.Series):
        frame = frame.rename("score").to_frame()
    if not isinstance(frame, pd.DataFrame) or frame.shape[1] != 1:
        raise ValueError("full prediction must be a one-column DataFrame")
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != [
        "datetime",
        "instrument",
    ]:
        raise ValueError("full prediction index must be datetime/instrument")
    if frame.index.has_duplicates:
        raise ValueError("full prediction contains duplicate index rows")
    return frame.sort_index()


def prediction_coverage(frame: pd.DataFrame) -> dict[str, Any]:
    dates = pd.DatetimeIndex(frame.index.get_level_values("datetime"))
    if dates.empty:
        raise ValueError("full prediction contains no rows")
    return {
        "start": str(dates.min().date()),
        "end": str(dates.max().date()),
        "n_dates": int(dates.nunique()),
        "n_rows": int(len(frame)),
        "index_sha256": prediction_index_sha256(frame.index),
    }


def validate_prediction_artifact(
    entry: Mapping[str, Any],
    path: Path,
    *,
    repo_root: Path = REPO_ROOT,
    expected_coverage: Mapping[str, Any] = FULL_PREDICTION_COVERAGE,
) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"full prediction file missing: {resolved}")
    if prediction_path(entry, repo_root) != resolved:
        raise ValueError("full prediction path differs from manifest")
    if sha256_file(resolved) != entry.get("prediction_sha256"):
        raise ValueError("full prediction file SHA differs from manifest")
    actual = prediction_coverage(prediction_frame(resolved))
    if (actual["start"], actual["end"]) != FULL_SEGMENT:
        raise ValueError("full prediction does not cover the exact selection period")
    declared = entry.get("coverage") or {}
    if actual != declared:
        raise ValueError("full prediction coverage differs from manifest")
    if actual != dict(expected_coverage):
        raise ValueError("full prediction differs from canonical coverage/index")
    return actual


def validate_full_prediction_manifest(
    manifest: Mapping[str, Any],
    manifest_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
    authoritative_manifest_path: Path = DEFAULT_FULL_PREDICTION_MANIFEST,
    expected_coverage: Mapping[str, Any] = FULL_PREDICTION_COVERAGE,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the canonical manifest, model binding, and actual DataFrame."""
    declared_path = Path(manifest_path).expanduser().resolve()
    authoritative_path = Path(authoritative_manifest_path).expanduser().resolve()
    if declared_path != authoritative_path:
        raise ValueError(
            "full prediction manifest differs from authoritative manifest path"
        )
    if not authoritative_path.is_file():
        raise FileNotFoundError(
            f"authoritative full prediction manifest missing: {authoritative_path}"
        )
    authoritative = json.loads(authoritative_path.read_text(encoding="utf-8"))
    if dict(manifest) != authoritative:
        raise ValueError("full prediction manifest differs from authoritative manifest")
    if manifest.get("schema_version") != 1:
        raise ValueError("full prediction manifest schema_version must be 1")
    if manifest.get("data_version") != FULL_DATA_VERSION:
        raise ValueError(
            f"full prediction manifest data_version must be {FULL_DATA_VERSION}"
        )
    if len(manifest.get("predictions") or []) != 1:
        raise ValueError("authoritative manifest requires exactly one prediction")

    entry = full_prediction_entry(manifest)
    if entry.get("data_version") != FULL_DATA_VERSION:
        raise ValueError(f"full prediction data_version must be {FULL_DATA_VERSION}")
    frozen = load_frozen_model(Path(repo_root), FULL_MODEL_REF)
    model_raw = entry.get("model_path")
    model_path = Path(str(model_raw or "")).expanduser()
    if not model_path.is_absolute():
        model_path = Path(repo_root) / model_path
    if (
        model_path.resolve() != frozen.model_path.resolve()
        or entry.get("model_sha256") != frozen.model_sha256
    ):
        raise ValueError(
            "full prediction requires the tracked B6-M seed-4000 model binding"
        )
    coverage = validate_prediction_artifact(
        entry,
        prediction_path(entry, Path(repo_root)),
        repo_root=Path(repo_root),
        expected_coverage=expected_coverage,
    )
    return entry, coverage
