"""Evaluate the B2-S portfolio with a lagged rolling-beta IM overlay."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from analyze_b2s_style_regime import sha256_of, upsert_registry
from beta_overlay_core import (
    IM_WINDOW,
    ROLL_WINDOW,
    TARGET_BETA,
    apply_beta_overlay,
    discrete_lots,
    report_from_port,
    slice_im_window,
)
from strategy_stability_metrics import summarize_period

EXP_ID = "strategy-beta-overlay/b2s-im-target1-roll60"
DIRECTION = "strategy-beta-overlay"
MULTIPLIER = 200


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def decide_promotion(
    *, overlay_sharpe: float | None, baseline_sharpe: float | None
) -> dict[str, bool | str]:
    """Apply the preregistered strict Sharpe comparison without promoting."""
    if not (_finite(overlay_sharpe) and _finite(baseline_sharpe)):
        return {
            "eligible": False,
            "reason": "overlay and baseline sharpe_ratio must both be finite",
        }
    overlay = float(overlay_sharpe)
    baseline = float(baseline_sharpe)
    eligible = overlay > baseline
    operator = ">" if eligible else "<="
    return {
        "eligible": eligible,
        "reason": f"sharpe_ratio {overlay} {operator} {baseline}",
    }


def _validate_frame(frame: pd.DataFrame, name: str, required: set[str]) -> pd.DataFrame:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{name} missing columns: {missing}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError(f"{name} index must be a DatetimeIndex")
    if frame.index.has_duplicates:
        raise ValueError(f"{name} contains duplicate dates")
    return frame.sort_index()


def _aligned_im(report: pd.DataFrame, im: pd.DataFrame) -> pd.DataFrame:
    window_start, window_end = map(pd.Timestamp, IM_WINDOW)
    report_window = report.loc[window_start:window_end]
    if report_window.empty or im.empty:
        raise ValueError("report and IM data do not overlap the IM window")
    missing_endpoints = [
        str(endpoint.date())
        for endpoint in (window_start, window_end)
        if endpoint not in report_window.index
    ]
    if missing_endpoints:
        raise ValueError(
            "report must cover both IM window endpoints; missing "
            + ", ".join(missing_endpoints)
        )

    expected = report_window.index
    aligned = im.reindex(expected)
    if aligned["settle"].isna().any():
        first = aligned.index[aligned["settle"].isna()][0]
        raise ValueError(f"IM settle is incomplete from {first.date()}")

    missing_returns = aligned["fut_ret"].isna()
    if missing_returns.any():
        allowed_leading = (
            aligned.index[0] == window_start
            and bool(missing_returns.iloc[0])
            and missing_returns.sum() == 1
        )
        if not allowed_leading:
            first = aligned.index[missing_returns][0]
            raise ValueError(f"IM fut_ret is incomplete from {first.date()}")
    return aligned


def _basis_note(im: pd.DataFrame) -> dict | None:
    basis_columns = [column for column in im.columns if "basis" in str(column).lower()]
    if not basis_columns:
        return None
    note: dict[str, object] = {
        "n_observations": int(im[basis_columns].notna().any(axis=1).sum()),
        "columns": {},
    }
    for column in basis_columns:
        values = pd.to_numeric(im[column], errors="coerce").dropna()
        note["columns"][str(column)] = {
            "mean": float(values.mean()) if not values.empty else None,
            "min": float(values.min()) if not values.empty else None,
            "max": float(values.max()) if not values.empty else None,
        }
    return note


def run_experiment(
    report: pd.DataFrame,
    im: pd.DataFrame,
    *,
    account: float = 2_800_000,
) -> dict:
    """Compute common-window baseline, continuous overlay, and integer-lot sensitivity."""
    if not _finite(account) or float(account) <= 0:
        raise ValueError("account must be a positive finite number")
    report = _validate_frame(report, "report", {"return", "cost", "bench", "turnover"})
    im = _validate_frame(im, "IM", {"fut_ret", "settle"})
    aligned_im = _aligned_im(report, im)

    net = report["return"].astype(float) - report["cost"].astype(float)
    im_on_report = im.reindex(report.index)
    overlay = apply_beta_overlay(
        net,
        report["bench"].astype(float),
        im_on_report["fut_ret"].astype(float),
        target=TARGET_BETA,
        window=ROLL_WINDOW,
    )
    overlay = slice_im_window(overlay).reindex(aligned_im.index)
    common_index = aligned_im.index[aligned_im["fut_ret"].notna()]
    required_complete = {
        "return-cost (net)": overlay["net"],
        "bench": overlay["bench"],
        "beta_hat": overlay["beta_hat"],
        "fut_ret": overlay["fut_ret"],
        "continuous portfolio": overlay["port"],
    }
    for name, values in required_complete.items():
        missing = values.reindex(common_index).isna()
        if missing.any():
            first = missing.index[missing][0]
            raise ValueError(f"{name} is incomplete from {first.date()}")
    overlay = overlay.loc[common_index]
    settle = aligned_im.loc[common_index, "settle"].astype(float)

    if {"value", "cash"}.issubset(report.columns):
        account_value = report.loc[common_index, "value"].astype(float) + report.loc[
            common_index, "cash"
        ].astype(float)
        if account_value.isna().any() or (account_value <= 0).any():
            raise ValueError("report value+cash must be complete and positive")
    else:
        account_value = pd.Series(float(account), index=common_index)

    lots = discrete_lots(overlay["gap"], account_value, settle, multiplier=MULTIPLIER)
    notional_fraction = lots.astype(float) * settle * MULTIPLIER / account_value
    port_discrete = overlay["net"] + notional_fraction * overlay["fut_ret"]

    baseline = summarize_period(
        report_from_port(overlay["net"], report.loc[common_index])
    )
    continuous = summarize_period(
        report_from_port(overlay["port"], report.loc[common_index])
    )
    discrete = summarize_period(
        report_from_port(port_discrete, report.loc[common_index])
    )
    promote = decide_promotion(
        overlay_sharpe=continuous.get("sharpe_ratio"),
        baseline_sharpe=baseline.get("sharpe_ratio"),
    )
    return {
        "schema_version": 1,
        "evaluation_mode": "im_window_in_sample",
        "im_window": list(IM_WINDOW),
        "evaluated_window": [
            str(common_index.min().date()),
            str(common_index.max().date()),
        ],
        "account": float(account),
        "target_beta": TARGET_BETA,
        "roll_window": ROLL_WINDOW,
        "futures_multiplier": MULTIPLIER,
        "account_value_basis": (
            "daily_value_plus_cash"
            if {"value", "cash"}.issubset(report.columns)
            else "fixed_initial_account"
        ),
        "baseline": baseline,
        "overlay_continuous": continuous,
        "overlay_discrete": discrete,
        "promote": promote,
        "basis_note": _basis_note(im.loc[aligned_im.index]),
        "disclosure": (
            "IM-window in-sample comparison; not an out-of-sample test or the "
            "standard full-history Phase S selection basis. No automatic promotion."
        ),
    }


def build_registry_row(
    payload: dict, output_path: Path, *, exp_id: str = EXP_ID
) -> dict:
    promote = payload.get("promote") or {}
    baseline_sharpe = (payload.get("baseline") or {}).get("sharpe_ratio")
    overlay_sharpe = (payload.get("overlay_continuous") or {}).get("sharpe_ratio")
    if not (_finite(baseline_sharpe) and _finite(overlay_sharpe)):
        conclusion = "complete"
    elif promote.get("eligible") is True:
        conclusion = "accepted_pending_promotion"
    else:
        conclusion = "rejected_vs_baseline"
    im_window = payload.get("im_window") or list(IM_WINDOW)
    evaluated_window = payload.get("evaluated_window") or im_window
    account = payload.get("account")
    account_note = (
        f"account={int(account)}"
        if isinstance(account, (int, float)) and float(account).is_integer()
        else f"account={account}"
    )
    return {
        "exp_id": exp_id,
        "direction": DIRECTION,
        "phase": "S",
        "date": str(pd.Timestamp.today().date()),
        "state": "complete",
        "conclusion": conclusion,
        "hypothesis": (
            "A long IM overlay targeting portfolio beta 1.0 with a lagged 60-day "
            "estimate improves after-cost absolute-return Sharpe in the IM window."
        ),
        "baseline_ref": "B2-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "data_version": evaluated_window[-1],
        "pool": "csi1000",
        "benchmark": "SH000852",
        "evaluation_mode": payload.get("evaluation_mode"),
        "im_window": im_window,
        "account": payload.get("account"),
        "selection_metric": "after_cost_absolute_return_sharpe",
        "overlay": {
            "instrument": "IM",
            "target_beta": payload.get("target_beta", TARGET_BETA),
            "roll_window": payload.get("roll_window", ROLL_WINDOW),
            "multiplier": payload.get("futures_multiplier", MULTIPLIER),
        },
        "metrics_summary": {
            "baseline": payload.get("baseline"),
            "overlay_continuous": payload.get("overlay_continuous"),
            "overlay_discrete": payload.get("overlay_discrete"),
        },
        "promotion_assessment": promote,
        "basis_note": payload.get("basis_note"),
        "input_artifacts": {
            "report_path": payload.get("report_path"),
            "report_sha256": payload.get("report_sha256"),
            "im_path": payload.get("im_path"),
            "im_sha256": payload.get("im_sha256"),
        },
        "result_path": str(output_path),
        "result_sha256": sha256_of(output_path),
        "result_dirs": ([payload["result_dir"]] if payload.get("result_dir") else []),
        "cleanup_retention_eligible": False,
        "note": (
            f"IM-window beta-overlay comparison ({account_note}); "
            "non-OOS; does not auto-promote a baseline. Shown in "
            "strategy_stability_report.html."
        ),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--im", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--account", type=float, default=2_800_000)
    parser.add_argument("--exp-id", default=EXP_ID)
    parser.add_argument("--registry", type=Path, default=None)
    parser.add_argument("--result-dir", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    report = pd.read_csv(args.report, index_col=0, parse_dates=True)
    im = pd.read_csv(args.im, index_col=0, parse_dates=True)
    payload = run_experiment(report, im, account=args.account)
    payload["report_path"] = str(args.report)
    payload["report_sha256"] = sha256_of(args.report)
    payload["im_path"] = str(args.im)
    payload["im_sha256"] = sha256_of(args.im)
    if args.result_dir:
        payload["result_dir"] = args.result_dir
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"written: {args.output}")
    if args.registry is not None:
        row = build_registry_row(payload, args.output, exp_id=args.exp_id)
        upsert_registry(args.registry, row)
        print(f"registered: {row['exp_id']}")


if __name__ == "__main__":
    main()
