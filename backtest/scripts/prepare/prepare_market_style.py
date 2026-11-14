"""Materialize market-style labels/features with frozen retrospective semantics."""

from __future__ import annotations

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import argparse
import json
import platform
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from backtest.market_style.data import read_market, daily_aggregates, expand_features, fingerprint
from backtest.market_style.labels import build_labels, make_targets
from backtest.market_style.external import collect_optional

ROOT = Path(__file__).resolve().parents[3]


def write_json(path, obj):
    def convert(value):
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, (Path, pd.Timestamp, pd.Period)):
            return str(value)
        raise TypeError(type(value).__name__)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False, default=convert) + "\n")


def prepare(archive, artifact_dir, provider):
    archive, artifact_dir = Path(archive).resolve(), Path(artifact_dir).resolve()
    if (archive / "data/features.parquet").exists() or (archive / "docs/label-audit.json").exists():
        raise ValueError("preparation already has frozen outputs; inspect receipts instead of overwriting")
    for path in (archive / "data", archive / "docs", artifact_dir):
        path.mkdir(parents=True, exist_ok=True)
    started = time.time()
    panel, indices, source = read_market(provider)
    print("Computing daily market aggregates", flush=True)
    daily = daily_aggregates(panel)
    del panel
    daily.to_parquet(archive / "data/daily-market.parquet")
    indices.to_parquet(archive / "data/index-prices.parquet")
    write_json(archive / "docs/market-inputs.json", source)
    historical_path = (
        ROOT
        / "backtest/experiments/strategies/topk-dropout/v2-top5-hold-f100/variants/bt-v5/configs/monthly_regime_labels_eval_window_v1.csv"
    )
    historical = pd.read_csv(historical_path, comment="#", parse_dates=["datetime"]).set_index("datetime").regime3
    print("Fitting the single frozen four-cluster label model (100 internal initializations)", flush=True)
    monthly, labels, audit, model = build_labels(daily, historical)
    audit["inputs"] = {
        "daily_market": fingerprint(archive / "data/daily-market.parquet"),
        "legacy": fingerprint(historical_path),
        "recipe": fingerprint(Path(__file__)),
    }
    monthly.to_csv(archive / "data/monthly-labels.csv", index_label="month")
    joblib.dump(model, artifact_dir / "label-model.joblib")
    audit["model"] = fingerprint(artifact_dir / "label-model.joblib")
    audit["elapsed_seconds"] = time.time() - started
    write_json(archive / "docs/label-audit.json", audit)
    print(
        json.dumps(
            {k: audit.get(k) for k in ("accepted", "mapping", "training_counts", "failure_reason")}, ensure_ascii=False
        ),
        flush=True,
    )
    if not audit["accepted"]:
        write_json(
            archive / "docs/preparation-result.json",
            {
                "status": "invalid",
                "reason": audit["failure_reason"],
                "label_audit": fingerprint(archive / "docs/label-audit.json"),
                "classification_fits": 0,
                "label_fits": 1,
                "elapsed_seconds": time.time() - started,
            },
        )
        return 2
    features, manifest = expand_features(daily, indices)
    print(f"Local predictors: {len(features.columns)}; checking optional external sources", flush=True)
    external, ext_audit = collect_optional(daily.index, archive / "data/external", indices)
    features = pd.concat([features, external], axis=1)
    targets = make_targets(daily.index, labels)
    # Never output September/August as supervised evaluation: August only closes the centered label edge.
    features = features.loc["2004-01-02":"2026-07-31"]
    targets = targets.reindex(features.index)
    if targets.label.isna().any() or not features.index.equals(targets.index):
        raise ValueError("missing supervised labels or target alignment")
    train = features.loc[:"2020-07-31"]
    dropped = {
        c: "all missing in training" if train[c].isna().all() else "constant in training"
        for c in features
        if train[c].nunique(dropna=True) <= 1
    }
    # Exact duplicate feature columns carry no extra information; derive removal only from training.
    kept, digests = [], {}
    for name in features.columns:
        if name in dropped:
            continue
        digest = pd.util.hash_pandas_object(train[name], index=False).to_numpy().tobytes()
        if digest in digests:
            dropped[name] = f"training-exact duplicate of {digests[digest]}"
        else:
            kept.append(name)
            digests[digest] = name
    features = features[kept]
    manifest["core_features"] = [c for c in manifest["core_features"] if c in kept]
    manifest["full_features"] = kept
    manifest["dropped_features"] = dropped
    manifest["data_version"] = source["data_version"]
    manifest["classification_protocol"] = "monthly-k4-next-day-v1"
    manifest["classes"] = ["D", "F", "T", "P"]
    manifest["label_audit"] = fingerprint(archive / "docs/label-audit.json")
    manifest["source_audit"] = fingerprint(archive / "docs/market-inputs.json")
    for name in kept:
        info = manifest["features"].setdefault(
            name,
            {
                "group": name.split("_")[0],
                "source": "Tushare external release-aligned source",
                "available_at": "explicit per-source release rule; asof <=signal20:00",
                "uses_future": False,
                "formula": name,
            },
        )
        info.update(
            train_missing_fraction=float(features.loc[:"2020-07-31", name].isna().mean()),
            eval_missing_fraction=float(features.loc["2020-08-03":, name].isna().mean()),
            first_valid_date=str(features[name].first_valid_index().date()) if features[name].notna().any() else None,
        )
    manifest["features"] = {c: manifest["features"][c] for c in kept}
    manifest["external_audit"] = ext_audit
    manifest["segments"] = {"train": ["2004-01-02", "2020-07-31"], "evaluation": ["2020-08-03", "2026-07-31"]}
    features.to_parquet(archive / "data/features.parquet")
    targets.to_parquet(archive / "data/targets.parquet")
    manifest["dataset_files"] = {
        "features": fingerprint(archive / "data/features.parquet"),
        "targets": fingerprint(archive / "data/targets.parquet"),
    }
    write_json(archive / "configs/feature-manifest.json", manifest)
    write_json(
        archive / "docs/feature-audit.json",
        {
            "n_rows": len(features),
            "core_features": manifest["core_features"],
            "n_full_features": len(kept),
            "dropped_features": dropped,
            "external": ext_audit,
            "manifest": fingerprint(archive / "configs/feature-manifest.json"),
        },
    )
    write_json(
        archive / "docs/preparation-result.json",
        {
            "status": "completed",
            "elapsed_seconds": time.time() - started,
            "label_fits": 1,
            "label_initializations": 100,
            "classification_fits": 0,
            "features": fingerprint(archive / "data/features.parquet"),
            "targets": fingerprint(archive / "data/targets.parquet"),
            "manifest": fingerprint(archive / "configs/feature-manifest.json"),
            "python": platform.python_version(),
        },
    )
    print(
        f"Prepared {len(features)} market days, {len(kept)} features, {len(manifest['core_features'])} core", flush=True
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--provider", type=Path, default=Path("~/.qlib/qlib_data/cn_data").expanduser())
    args = parser.parse_args()
    raise SystemExit(prepare(args.archive, args.artifact_dir, args.provider))


if __name__ == "__main__":
    main()
