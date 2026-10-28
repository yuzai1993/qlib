"""Export frozen v2 models for offline BT v5 deployment preparation; no training."""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from live_trading.modules.signal_generator import _InferenceDataset
from live_trading.scripts.export_live_model import SEEDS, export


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source_root: Path, destination: Path) -> dict:
    source_root, destination = source_root.resolve(), destination.resolve()
    sources = [
        source_root
        / f"backtest/result/regimeadaptfast_m0h20_t5h5es_s{seed}/run_01/artifacts_root/artifacts/trained_model"
        for seed in SEEDS
    ]
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)
    members = []
    for seed, source in zip(SEEDS, sources):
        with source.open("rb") as fh:
            original = pickle.load(fh)
        if original.es_metric != "top5_h5_net_ann":
            raise ValueError(f"not a frozen v2 model: {source}: {original.es_metric}")
        target = destination / f"s{seed}" / "trained_model"
        info = export(source, target)
        with target.open("rb") as fh:
            slim = pickle.load(fh)
        # Finite and missing feature values exercise real Booster inference.
        frame = pd.DataFrame(
            np.random.default_rng(seed).normal(size=(128, original.model.num_feature()))
        )
        frame.iloc[::3, ::7] = np.nan
        dataset = _InferenceDataset(frame)
        pd.testing.assert_series_equal(
            original.predict(dataset), slim.predict(dataset), check_exact=True
        )
        members.append(
            {
                "seed": seed,
                "source": str(source),
                "source_sha256": sha256(source),
                "model_path": str(target.relative_to(ROOT)),
                "sha256": info["sha256"],
                "source_bytes": info["src_bytes"],
                "bytes": info["dst_bytes"],
                "best_iteration": original.model.best_iteration,
                "num_features": original.model.num_feature(),
                "prediction_parity": "exact_on_128_rows_including_missing_values",
            }
        )
    manifest = {
        "model_ref": "v2",
        "experiment": "regime-adapt/m0-h20-t5h5-es-v1",
        "bt_baseline": "baseline/phase-m-v1-bt-v5",
        "es_metric": "top5_h5_net_ann",
        "ensemble": "daily_zscore_mean",
        "training": "frozen_no_retraining",
        "members": members,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    args = parser.parse_args()
    result = prepare(args.source_root, ROOT / "live_trading/models/v2_t5h5es")
    print(
        json.dumps(
            {
                "members": len(result["members"]),
                "iterations": [m["best_iteration"] for m in result["members"]],
            }
        )
    )


if __name__ == "__main__":
    main()
