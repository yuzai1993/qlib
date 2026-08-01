"""Generate immutable Phase S predictions from tracked baseline model artifacts."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import load_config  # noqa: E402
from eval_ic_multi_pool import _build_dataset, _init_qlib  # noqa: E402
from phase_s_protocol import (  # noqa: E402
    CURRENT_MODEL_REFS,
    MODEL_REFS,
    POOL_BENCHMARKS,
    TEST_SEGMENT,
    VALID_SEGMENT,
    FrozenModel,
    load_frozen_model,
    sha256_file,
)


def normalize_prediction(pred: pd.Series | pd.DataFrame) -> pd.Series:
    if isinstance(pred, pd.DataFrame):
        if pred.shape[1] != 1:
            raise ValueError("prediction must be a Series or one-column DataFrame")
        pred = pred.iloc[:, 0]
    if not isinstance(pred, pd.Series):
        raise TypeError("prediction must be a pandas Series or DataFrame")
    if not isinstance(pred.index, pd.MultiIndex):
        raise ValueError("prediction index must be a MultiIndex")
    names = list(pred.index.names)
    if set(names) != {"datetime", "instrument"}:
        raise ValueError(
            "prediction index levels must be named datetime and instrument"
        )
    if names != ["datetime", "instrument"]:
        pred = pred.reorder_levels(["datetime", "instrument"])
    pred = pred.sort_index().rename("score")
    return pred


def prediction_index_sha256(index: pd.MultiIndex) -> str:
    return hashlib.sha256(
        pd.util.hash_pandas_object(index.to_frame(index=False), index=False)
        .to_numpy()
        .tobytes()
    ).hexdigest()


def validate_prediction_index(
    pred: pd.Series, expected_dates: pd.DatetimeIndex
) -> dict[str, Any]:
    if not isinstance(pred.index, pd.MultiIndex) or list(pred.index.names) != [
        "datetime",
        "instrument",
    ]:
        raise ValueError("prediction index must be named datetime/instrument")
    if pred.index.has_duplicates:
        raise ValueError("prediction index contains duplicate rows")
    actual = pd.DatetimeIndex(
        pred.index.get_level_values("datetime").unique()
    ).sort_values()
    expected = pd.DatetimeIndex(expected_dates).sort_values()
    missing = expected.difference(actual)
    extra = actual.difference(expected)
    if len(missing) or len(extra):
        raise ValueError(
            "prediction date coverage mismatch: "
            f"missing={[str(v.date()) for v in missing[:5]]}, "
            f"extra={[str(v.date()) for v in extra[:5]]}"
        )
    if len(actual) == 0:
        raise ValueError("prediction contains no trading dates")
    return {
        "start": str(actual[0].date()),
        "end": str(actual[-1].date()),
        "n_dates": int(len(actual)),
        "n_rows": int(len(pred)),
        "index_sha256": prediction_index_sha256(pred.index),
    }


def build_prediction_manifest_entry(
    path: Path,
    frozen: FrozenModel | Any,
    *,
    pool: str,
    segment: str,
    coverage: dict[str, Any],
    data_version: str,
) -> dict[str, Any]:
    pred_path = Path(path)
    return {
        "model_ref": frozen.model_ref,
        "manifest_path": str(frozen.manifest_path),
        "model_path": str(frozen.model_path),
        "model_sha256": frozen.model_sha256,
        "config_path": str(frozen.source_config),
        "config_sha256": sha256_file(Path(frozen.source_config)),
        "pool": pool,
        "segment": segment,
        "path": str(pred_path),
        "prediction_sha256": sha256_file(pred_path),
        "coverage": dict(coverage),
        "data_version": data_version,
    }


def _segment_bounds(name: str) -> tuple[str, str]:
    if name == "valid":
        return VALID_SEGMENT
    if name == "test":
        return TEST_SEGMENT
    raise ValueError(f"unsupported Phase S prediction segment: {name}")


def generate_model_predictions(
    repo_root: Path,
    model_ref: str,
    output_root: Path,
    *,
    pools: Sequence[str],
    segments: Sequence[str],
) -> tuple[FrozenModel, list[dict[str, Any]], str]:
    from qlib.data import D

    frozen = load_frozen_model(repo_root, model_ref)
    cfg = load_config(str(frozen.source_config))
    cfg["segments"]["valid"] = list(VALID_SEGMENT)
    cfg["segments"]["test"] = list(TEST_SEGMENT)
    _init_qlib(cfg)
    data_version = str(
        pd.Timestamp(D.calendar(start_time="2020-01-01")[-1]).date()
    )
    with frozen.model_path.open("rb") as handle:
        model = pickle.load(handle)

    entries: list[dict[str, Any]] = []
    for pool in pools:
        if pool not in POOL_BENCHMARKS:
            raise ValueError(f"unsupported Phase S pool: {pool}")
        for segment in segments:
            start, end = _segment_bounds(segment)
            expected_dates = pd.DatetimeIndex(
                D.calendar(start_time=start, end_time=end)
            )
            dataset = _build_dataset(
                cfg,
                pool,
                segment=segment,
                end_override=end,
            )
            pred = normalize_prediction(model.predict(dataset, segment=segment))
            coverage = validate_prediction_index(pred, expected_dates)
            path = output_root / "predictions" / model_ref / f"{pool}_{segment}.pkl"
            path.parent.mkdir(parents=True, exist_ok=True)
            pred.to_frame().to_pickle(path)
            entries.append(
                build_prediction_manifest_entry(
                    path,
                    frozen,
                    pool=pool,
                    segment=segment,
                    coverage=coverage,
                    data_version=data_version,
                )
            )
            del dataset, pred
            gc.collect()
    del model
    gc.collect()
    return frozen, entries, data_version


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 backtest/models/baselines 生成 Phase S 冻结预测"
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "backtest/experiments/strategy/20260801_b1_b6",
    )
    parser.add_argument(
        "--model-ref",
        nargs="+",
        choices=MODEL_REFS,
        default=list(CURRENT_MODEL_REFS),
    )
    parser.add_argument("--pools", nargs="+", default=list(POOL_BENCHMARKS))
    parser.add_argument("--segments", nargs="+", choices=("valid", "test"), default=["valid", "test"])
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    models: dict[str, Any] = {}
    versions = set()
    for model_ref in args.model_ref:
        frozen, model_entries, version = generate_model_predictions(
            repo_root,
            model_ref,
            output_root,
            pools=args.pools,
            segments=args.segments,
        )
        entries.extend(model_entries)
        versions.add(version)
        models[model_ref] = {
            "manifest_path": str(frozen.manifest_path),
            "model_path": str(frozen.model_path),
            "model_sha256": frozen.model_sha256,
            "config_path": str(frozen.source_config),
            "config_sha256": sha256_file(frozen.source_config),
        }
    if len(versions) != 1:
        raise ValueError(f"data version differs across models: {sorted(versions)}")
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_version": next(iter(versions)),
        "models": models,
        "predictions": entries,
    }
    manifest_path = output_root / "prediction_manifest.json"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"{len(entries)} predictions -> {manifest_path}")


if __name__ == "__main__":
    main()
