"""Replay TopkDropout selection state and summarize holding durations.

This is deliberately not a portfolio backtest: it uses prediction ranks and
the pure instrument selector only. It does not instantiate an executor,
exchange, account, or portfolio-analysis record.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from config_loader import load_config  # noqa: E402
from eval_ic_multi_pool import (  # noqa: E402
    _build_dataset,
    _init_qlib,
    _load_model,
    _parse_session,
    _segment_bounds,
)
from qlib.contrib.strategy.topk_dropout import select_topk_dropout  # noqa: E402


@dataclass(frozen=True)
class HoldingSpell:
    instrument: str
    start: str
    end: str
    duration: int
    censored: bool


def _validate_scores(scores: pd.Series) -> None:
    if not isinstance(scores, pd.Series):
        raise TypeError("scores must be a pandas Series")
    if not isinstance(scores.index, pd.MultiIndex):
        raise ValueError("scores index must contain datetime and instrument")
    if set(("datetime", "instrument")) - set(scores.index.names):
        raise ValueError("scores index must contain datetime and instrument")


def replay_holding_spells(
    scores: pd.Series,
    *,
    topk: int,
    n_drop: int,
) -> tuple[list[HoldingSpell], list[HoldingSpell]]:
    """Replay rank-only holdings and return completed and censored spells."""
    _validate_scores(scores)
    ordered = scores.reorder_levels(["datetime", "instrument"]).sort_index()
    dates = pd.DatetimeIndex(
        ordered.index.get_level_values("datetime").unique()
    ).sort_values()
    holdings: dict[str, tuple[int, pd.Timestamp]] = {}
    completed: list[HoldingSpell] = []

    for date_index, date in enumerate(dates):
        day_scores = ordered.xs(date, level="datetime").dropna()
        selection = select_topk_dropout(
            day_scores,
            holdings.keys(),
            topk=topk,
            n_drop=n_drop,
        )
        for instrument in selection.sell:
            start_index, start_date = holdings.pop(str(instrument))
            completed.append(
                HoldingSpell(
                    instrument=str(instrument),
                    start=str(start_date.date()),
                    end=str(pd.Timestamp(date).date()),
                    duration=date_index - start_index,
                    censored=False,
                )
            )
        for instrument in selection.buy:
            holdings[str(instrument)] = (date_index, pd.Timestamp(date))

    censored = [
        HoldingSpell(
            instrument=instrument,
            start=str(start_date.date()),
            end=str(dates[-1].date()),
            duration=len(dates) - start_index,
            censored=True,
        )
        for instrument, (start_index, start_date) in holdings.items()
    ]
    return completed, censored


def kaplan_meier_survival(
    completed: Sequence[int],
    censored: Sequence[int],
) -> dict[int, float]:
    """Return P(T >= k), applying events after recording survival at age k."""
    completed = [int(value) for value in completed]
    censored = [int(value) for value in censored]
    durations = completed + censored
    if not durations:
        return {}
    if min(durations) < 1:
        raise ValueError("holding durations must be positive")

    survival = 1.0
    curve: dict[int, float] = {}
    for age in range(1, max(durations) + 1):
        at_risk = sum(value >= age for value in durations)
        if at_risk == 0:
            break
        curve[age] = survival
        events = sum(value == age for value in completed)
        survival *= 1.0 - events / at_risk
    return curve


def summarize_durations(
    completed: Sequence[int],
    censored: Sequence[int],
    *,
    thresholds: Sequence[int] = (5, 10, 20, 30, 40, 60),
) -> dict:
    """Summarize completed durations and censor-aware survival."""
    completed_arr = np.asarray(list(completed), dtype=float)
    censored_values = [int(value) for value in censored]
    if completed_arr.size == 0:
        raise ValueError("at least one completed holding spell is required")

    curve = kaplan_meier_survival(
        completed_arr.astype(int).tolist(), censored_values
    )
    return {
        "completed_count": int(completed_arr.size),
        "censored_count": len(censored_values),
        "mean": float(completed_arr.mean()),
        "p50": float(np.quantile(completed_arr, 0.50)),
        "p75": float(np.quantile(completed_arr, 0.75)),
        "p90": float(np.quantile(completed_arr, 0.90)),
        "max": int(completed_arr.max()),
        "held_at_least": {
            str(int(threshold)): float(
                np.mean(completed_arr >= int(threshold))
            )
            for threshold in thresholds
        },
        "survival": {
            str(age): float(probability)
            for age, probability in curve.items()
        },
    }


def _as_score_series(pred: object) -> pd.Series:
    if isinstance(pred, pd.DataFrame):
        if pred.shape[1] != 1:
            raise ValueError("model prediction must contain exactly one column")
        pred = pred.iloc[:, 0]
    if not isinstance(pred, pd.Series):
        raise TypeError("model prediction must be a Series or one-column DataFrame")
    pred.index = pred.index.set_names(["datetime", "instrument"])
    return pred.rename("score")


def analyze(
    cfg: dict,
    sessions: Sequence[tuple[str, object]],
    *,
    pool: str,
    segment: str,
    topk: int,
    n_drop: int,
) -> dict:
    from qlib.data import D

    dataset = _build_dataset(cfg, pool, segment=segment)
    per_seed: dict[str, dict] = {}
    pooled_completed: list[int] = []
    pooled_censored: list[int] = []

    for session, seed in sessions:
        model = _load_model(session)
        pred = _as_score_series(model.predict(dataset, segment=segment))
        completed, censored = replay_holding_spells(
            pred, topk=topk, n_drop=n_drop
        )
        completed_durations = [spell.duration for spell in completed]
        censored_durations = [spell.duration for spell in censored]
        pooled_completed.extend(completed_durations)
        pooled_censored.extend(censored_durations)
        per_seed[str(seed)] = {
            "summary": summarize_durations(
                completed_durations, censored_durations
            ),
            "completed_spells": [asdict(spell) for spell in completed],
            "censored_spells": [asdict(spell) for spell in censored],
        }

    start, end = _segment_bounds(cfg, segment)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": cfg.get("_config_path"),
        "pool": pool,
        "segment_name": segment,
        "segment": [start, end],
        "strategy": {"topk": topk, "n_drop": n_drop},
        "diagnostic_only": True,
        "data_version": str(
            pd.Timestamp(D.calendar(start_time="2020-01-01")[-1]).date()
        ),
        "sessions": [
            {"session": session, "seed": seed}
            for session, seed in sessions
        ],
        "seeds": per_seed,
        "pooled": summarize_durations(
            pooled_completed, pooled_censored
        ),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TopkDropout rank-only holding-duration diagnostic"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--sessions",
        nargs="+",
        required=True,
        metavar="SESSION:SEED",
    )
    parser.add_argument("--pool", default="csi300")
    parser.add_argument("--segment", choices=("train", "valid"), default="valid")
    parser.add_argument("--topk", type=int, default=30)
    parser.add_argument("--n-drop", type=int, default=1)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    cfg = load_config(args.config)
    _init_qlib(cfg)
    result = analyze(
        cfg,
        [_parse_session(value) for value in args.sessions],
        pool=args.pool,
        segment=args.segment,
        topk=args.topk,
        n_drop=args.n_drop,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
