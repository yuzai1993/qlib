"""Train a fixed-cadence expanding sequence of Phase-M models.

Each invocation handles one seed and writes one parent result session. Every
fold remains a normal train-only MLflow experiment so the existing artifact
format and model loader continue to work.
"""

from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qlib
from qlib.constant import REG_CN
from qlib.utils import exists_qlib_data

from config_loader import RESULT_ROOT, build_task, load_config
from report_utils import make_session_dir, write_json
from run_backtest import (
    _finalize_session,
    exit_code_for_summary,
    run_train_only_once,
)


def _calendar_pos(calendar: pd.DatetimeIndex, raw_date: str) -> int:
    timestamp = pd.Timestamp(raw_date)
    pos = int(calendar.searchsorted(timestamp))
    if pos >= len(calendar) or calendar[pos] != timestamp:
        raise ValueError(f"date is not on the Qlib trading calendar: {raw_date}")
    return pos


def _date(calendar: pd.DatetimeIndex, pos: int) -> str:
    if pos < 0 or pos >= len(calendar):
        raise ValueError(f"calendar position out of range: {pos}")
    return str(pd.Timestamp(calendar[pos]).date())


def build_expanding_folds(
    cfg: dict,
    calendar: pd.DatetimeIndex,
    *,
    step: int,
) -> list[dict]:
    """Build contiguous test folds while expanding only the train end."""
    if step <= 0:
        raise ValueError("rolling step must be positive")
    calendar = pd.DatetimeIndex(calendar)
    segments = cfg["segments"]
    train_start, train_end = (str(v) for v in segments["train"])
    valid_start, valid_end = (str(v) for v in segments["valid"])
    test_start, test_end = (str(v) for v in segments["test"])

    train_end_pos = _calendar_pos(calendar, train_end)
    valid_start_pos = _calendar_pos(calendar, valid_start)
    valid_end_pos = _calendar_pos(calendar, valid_end)
    test_start_pos = _calendar_pos(calendar, test_start)
    test_end_pos = _calendar_pos(calendar, test_end)
    if valid_end_pos + 1 != test_start_pos:
        raise ValueError("valid must end on the trading day before test starts")

    folds: list[dict] = []
    offset = 0
    while test_start_pos + offset <= test_end_pos:
        fold_test_start = test_start_pos + offset
        fold_test_end = min(fold_test_start + step - 1, test_end_pos)
        fold = {
            "fold": len(folds) + 1,
            "segments": {
                "train": [
                    train_start,
                    _date(calendar, train_end_pos + offset),
                ],
                "valid": [
                    _date(calendar, valid_start_pos + offset),
                    _date(calendar, valid_end_pos + offset),
                ],
                "test": [
                    _date(calendar, fold_test_start),
                    _date(calendar, fold_test_end),
                ],
            },
        }
        folds.append(fold)
        offset += step
    return folds


def apply_fold(cfg: dict, fold: dict) -> dict:
    """Return a fold-specific config without changing any model treatment."""
    out = copy.deepcopy(cfg)
    out["segments"] = copy.deepcopy(fold["segments"])
    handler = out["data"]["handler"]
    handler["fit_start_time"] = out["segments"]["train"][0]
    handler["fit_end_time"] = out["segments"]["train"][1]
    handler["end_time"] = out["segments"]["test"][1]
    return out


def _meta_run(result: dict, fold: dict) -> dict:
    return {
        "run": result.get("run"),
        "fold": fold["fold"],
        "status": result.get("status"),
        "segments": copy.deepcopy(fold["segments"]),
        "train_experiment_name": result.get("train_experiment_name"),
        "train_experiment_id": result.get("train_experiment_id"),
        "train_recorder_id": result.get("train_recorder_id"),
    }


def run_rolling_session(
    cfg: dict,
    *,
    calendar: pd.DatetimeIndex,
    step: int,
    session_dir: Path,
) -> int:
    """Train every fold for one seed and keep the parent session auditable."""
    folds = build_expanding_folds(cfg, calendar, step=step)
    seed = int(cfg["model"]["kwargs"]["seed"])
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    session_name = session_dir.name
    note = str(cfg["run"].get("note") or "")
    start_time = datetime.now()
    meta = {
        "session_name": session_name,
        "note": note,
        "mode": "rolling_train_only",
        "created_at": start_time.isoformat(timespec="seconds"),
        "config_path": cfg["_config_path"],
        "provider_uri": cfg["data"]["provider_uri"],
        "market": cfg["data"]["instruments"],
        "benchmark": cfg["data"]["benchmark"],
        "handler": cfg["data"]["handler"]["class"],
        "seed": seed,
        "step": int(step),
        "rolling_type": "expanding",
        "expected_fold_count": len(folds),
        "official_test_segment": list(cfg["segments"]["test"]),
        "rolling_folds": folds,
        "runs": [],
    }
    write_json(session_dir / "meta.json", meta)

    results = []
    for fold in folds:
        fold_cfg = apply_fold(cfg, fold)
        task = build_task(fold_cfg)
        result = run_train_only_once(
            fold["fold"],
            len(folds),
            session_dir,
            session_name,
            note,
            task,
        )
        results.append(result)
        meta["runs"].append(_meta_run(result, fold))
        write_json(session_dir / "meta.json", meta)

    summary = _finalize_session(
        session_dir,
        session_name,
        note,
        len(folds),
        results,
        start_time,
    )
    return exit_code_for_summary(summary, len(folds))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="B5 Phase-M expanding rolling train-only runner"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--step",
        type=int,
        default=None,
        help="rolling cadence in trading days; defaults to config rolling.step",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config)
    rolling_cfg = cfg.get("rolling") or {}
    step = int(args.step or rolling_cfg.get("step") or 252)
    if str(rolling_cfg.get("type") or "expanding") != "expanding":
        raise ValueError("only expanding rolling type is supported")

    provider_uri = cfg["data"]["provider_uri"]
    if not exists_qlib_data(provider_uri):
        raise RuntimeError(f"Qlib data not found: {provider_uri}")
    region = cfg["data"].get("region", "cn")
    qlib.init(
        provider_uri=provider_uri,
        region=REG_CN if region == "cn" else region,
    )
    from qlib.data import D

    calendar = pd.DatetimeIndex(
        D.calendar(
            start_time=cfg["data"]["handler"]["start_time"],
            end_time=cfg["segments"]["test"][1],
        )
    )
    session_dir = make_session_dir(
        RESULT_ROOT,
        note=cfg["run"].get("note") or "",
    )
    print(f"rolling result session: {session_dir}")
    return run_rolling_session(
        cfg,
        calendar=calendar,
        step=step,
        session_dir=session_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
