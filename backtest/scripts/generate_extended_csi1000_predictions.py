"""Generate diagnostic CSI1000 predictions for an extended history window.

This is NOT the authoritative Phase S full prediction (2020+). It only supports
long-window figured backtests / diagnostics.
"""

from __future__ import annotations

import argparse
import gc
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import load_config  # noqa: E402
from eval_ic_multi_pool import _build_dataset, _init_qlib  # noqa: E402
from generate_phase_s_predictions import (  # noqa: E402
    normalize_prediction,
    prediction_index_sha256,
    sha256_file,
)
from phase_s_protocol import load_frozen_model  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--model-ref", default="b6-m")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="pickle path for the one-column prediction DataFrame",
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        default=None,
        help="optional manifest json beside the prediction",
    )
    args = parser.parse_args()

    frozen = load_frozen_model(REPO_ROOT, args.model_ref)
    cfg = load_config(str(frozen.source_config))
    cfg["segments"]["test"] = [args.start, args.end]
    _init_qlib(cfg)
    from qlib.data import D

    expected = pd.DatetimeIndex(D.calendar(start_time=args.start, end_time=args.end))
    if len(expected) == 0:
        raise ValueError(f"no trading days in [{args.start}, {args.end}]")

    with frozen.model_path.open("rb") as handle:
        model = pickle.load(handle)
    dataset = _build_dataset(cfg, "csi1000", segment="test", end_override=args.end)
    pred = normalize_prediction(model.predict(dataset, segment="test"))
    actual = pd.DatetimeIndex(pred.index.get_level_values("datetime").unique()).sort_values()
    missing = expected.difference(actual)
    extra = actual.difference(expected)
    if len(missing) or len(extra):
        raise ValueError(
            "prediction coverage mismatch: "
            f"missing_head={[str(v.date()) for v in missing[:5]]} "
            f"extra_head={[str(v.date()) for v in extra[:5]]} "
            f"n_missing={len(missing)} n_extra={len(extra)}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame = pred.to_frame("score")
    frame.to_pickle(args.output)
    coverage = {
        "start": str(actual[0].date()),
        "end": str(actual[-1].date()),
        "n_dates": int(len(actual)),
        "n_rows": int(len(frame)),
        "index_sha256": prediction_index_sha256(frame.index),
    }
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "diagnostic_extended_history",
        "evaluation_mode": "extended_history_in_sample",
        "model_ref": args.model_ref,
        "model_path": str(frozen.model_path),
        "model_sha256": frozen.model_sha256,
        "pool": "csi1000",
        "segment": [args.start, args.end],
        "path": str(args.output),
        "prediction_sha256": sha256_file(args.output),
        "coverage": coverage,
    }
    manifest_path = args.manifest_output or args.output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"prediction": str(args.output), **coverage}, ensure_ascii=False))
    del dataset, pred, model
    gc.collect()


if __name__ == "__main__":
    main()
