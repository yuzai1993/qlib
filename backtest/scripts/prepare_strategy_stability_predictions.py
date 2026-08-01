"""Compose frozen Phase S valid/test scores into full-period diagnostic bundles."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from generate_phase_s_predictions import prediction_index_sha256  # noqa: E402
from phase_s_protocol import CURRENT_MODEL_REFS, MODEL_REFS, sha256_file  # noqa: E402

FULL_START = "2020-01-13"
FULL_END = "2026-07-31"


def _load_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_pickle(path)
    if isinstance(frame, pd.Series):
        frame = frame.rename("score").to_frame()
    if not isinstance(frame, pd.DataFrame) or frame.shape[1] != 1:
        raise ValueError("prediction must be a one-column DataFrame")
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != [
        "datetime",
        "instrument",
    ]:
        raise ValueError("prediction index must be datetime/instrument")
    frame = frame.rename(columns={frame.columns[0]: "score"}).sort_index()
    if frame.index.has_duplicates:
        raise ValueError("prediction contains duplicate index rows")
    return frame


def _validate_source(path: Path, entry: dict[str, Any], frame: pd.DataFrame) -> None:
    declared = Path(str(entry.get("path") or "")).expanduser()
    if declared.resolve() != path.resolve():
        raise ValueError("prediction source path differs from manifest")
    if sha256_file(path) != entry.get("prediction_sha256"):
        raise ValueError(f"prediction source SHA mismatch: {path}")
    coverage = entry.get("coverage") or {}
    if prediction_index_sha256(frame.index) != coverage.get("index_sha256"):
        raise ValueError(f"prediction source index SHA mismatch: {path}")
    dates = frame.index.get_level_values("datetime")
    actual = {
        "start": str(dates.min().date()),
        "end": str(dates.max().date()),
        "n_dates": int(dates.nunique()),
        "n_rows": int(len(frame)),
    }
    if any(actual[key] != coverage.get(key) for key in actual):
        raise ValueError(f"prediction source coverage mismatch: {path}")


def compose_prediction(
    valid_path: Path,
    test_path: Path,
    valid_entry: dict[str, Any],
    test_entry: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    valid_identity = (
        valid_entry.get("model_ref"),
        valid_entry.get("pool"),
        valid_entry.get("segment"),
    )
    test_identity = (
        test_entry.get("model_ref"),
        test_entry.get("pool"),
        test_entry.get("segment"),
    )
    if (
        valid_identity[0] != test_identity[0]
        or valid_identity[1] != test_identity[1]
        or valid_identity[1] != "csi1000"
        or valid_identity[2] != "valid"
        or test_identity[2] != "test"
    ):
        raise ValueError("prediction source identity mismatch")
    valid = _load_frame(Path(valid_path))
    test = _load_frame(Path(test_path))
    _validate_source(Path(valid_path), valid_entry, valid)
    _validate_source(Path(test_path), test_entry, test)
    overlap = valid.index.intersection(test.index)
    if len(overlap):
        raise ValueError("valid/test prediction indices overlap")
    if valid.index.get_level_values("datetime").max() >= test.index.get_level_values("datetime").min():
        raise ValueError("valid/test prediction date ranges overlap or are reversed")
    full = pd.concat([valid, test]).sort_index()
    dates = full.index.get_level_values("datetime")
    if str(dates.min().date()) != FULL_START or str(dates.max().date()) != FULL_END:
        raise ValueError("composed prediction does not cover the exact full period")
    if dates.nunique() != (
        int(valid_entry["coverage"]["n_dates"])
        + int(test_entry["coverage"]["n_dates"])
    ):
        raise ValueError("composed prediction has missing or duplicated trading dates")
    audit = {
        "model_ref": valid_identity[0],
        "pool": "csi1000",
        "segment": "full",
        "sources": [
            {
                "segment": entry["segment"],
                "path": entry["path"],
                "prediction_sha256": entry["prediction_sha256"],
                "index_sha256": entry["coverage"]["index_sha256"],
            }
            for entry in (valid_entry, test_entry)
        ],
        "coverage": {
            "start": FULL_START,
            "end": FULL_END,
            "n_dates": int(dates.nunique()),
            "n_rows": int(len(full)),
            "index_sha256": prediction_index_sha256(full.index),
        },
    }
    return full, audit


def _source_path(entry: dict[str, Any]) -> Path:
    path = Path(entry["path"]).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def prepare_bundles(
    source_manifest_path: Path,
    output_root: Path,
    model_refs: Sequence[str],
) -> dict[str, Any]:
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    entries = source_manifest.get("predictions") or []
    composed_entries = []
    for model_ref in model_refs:
        by_segment = {
            entry["segment"]: entry
            for entry in entries
            if entry.get("model_ref") == model_ref and entry.get("pool") == "csi1000"
        }
        if set(by_segment) != {"valid", "test"}:
            raise ValueError(f"source prediction matrix incomplete for {model_ref}")
        full, audit = compose_prediction(
            _source_path(by_segment["valid"]),
            _source_path(by_segment["test"]),
            by_segment["valid"],
            by_segment["test"],
        )
        path = output_root / "predictions" / model_ref / "csi1000_full.pkl"
        path.parent.mkdir(parents=True, exist_ok=True)
        full.to_pickle(path)
        composed_entries.append(
            {
                **audit,
                "path": str(path.resolve()),
                "prediction_sha256": sha256_file(path),
                "data_version": source_manifest.get("data_version"),
            }
        )
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_manifest": str(source_manifest_path.resolve()),
        "source_manifest_sha256": sha256_file(source_manifest_path),
        "data_version": source_manifest.get("data_version"),
        "predictions": composed_entries,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "prediction_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=REPO_ROOT / "backtest/experiments/strategy/20260801_b1_b6/prediction_manifest.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "backtest/experiments/strategy-stability/20260801_full_period",
    )
    parser.add_argument(
        "--model-ref",
        nargs="+",
        choices=MODEL_REFS,
        default=list(CURRENT_MODEL_REFS),
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    manifest = prepare_bundles(
        args.source_manifest.resolve(), args.output_root.resolve(), args.model_ref
    )
    print(f"{len(manifest['predictions'])} full-period predictions -> {args.output_root / 'prediction_manifest.json'}")


if __name__ == "__main__":
    main()
