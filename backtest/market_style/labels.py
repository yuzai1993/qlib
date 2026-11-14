"""Explicitly retrospective monthly four-style labels; never predictor inputs."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

LABEL_COLUMNS = ["mkt", "mkt_vol", "small_big", "lowvol_highvol", "momentum"]


def smooth_monthly(labels):
    output = labels.copy()
    for i in range(1, len(labels) - 1):
        counts = labels.iloc[i - 1 : i + 2].value_counts()
        if counts.iloc[0] >= 2:
            output.iloc[i] = counts.index[0]
    return output


def assign_semantics(centers):
    panic = centers.mkt.idxmin()
    if not (
        centers.loc[panic, "mkt"] < 0
        and centers.loc[panic, "lowvol_highvol"] > 0
        and centers.loc[panic, "mkt_vol"] > centers.mkt_vol.median()
    ):
        raise ValueError("no identifiable panic center under the frozen semantic criteria")
    trend_candidates = centers[(centers.small_big < 0) & (centers.lowvol_highvol < 0) & (centers.momentum > 0)]
    trend_candidates = trend_candidates.drop(index=panic, errors="ignore")
    if len(trend_candidates) != 1:
        raise ValueError("expected exactly one large-cap momentum trend center")
    trend = trend_candidates.index[0]
    others = centers.drop(index=[panic, trend])
    frenzy = others.mkt.idxmax()
    defensive = others.drop(index=frenzy).index[0]
    if not (
        centers.loc[frenzy, "mkt"] > 0
        and centers.loc[frenzy, "mkt_vol"] >= centers.loc[defensive, "mkt_vol"]
        and centers.loc[frenzy, "momentum"] < 0
        and centers.loc[defensive, "small_big"] > 0
        and centers.loc[defensive, "lowvol_highvol"] > 0
    ):
        raise ValueError("F/D centers do not satisfy the frozen style interpretation")
    return {int(panic): "P", int(trend): "T", int(frenzy): "F", int(defensive): "D"}


def make_targets(calendar, monthly):
    dates = pd.DatetimeIndex(calendar)
    if not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError("target calendar must be sorted and unique")
    labels = monthly.copy()
    if not isinstance(labels.index, pd.PeriodIndex):
        labels.index = pd.to_datetime(labels.index).to_period("M")
    target_days = dates[1:]
    out = pd.DataFrame({"target_date": target_days, "label": target_days.to_period("M").map(labels)}, index=dates[:-1])
    out.index.name = "date"
    return out.dropna(subset=["label"])


def aggregate_monthly(daily):
    """Use actual trading sessions; a short holiday month is still complete."""
    returns = daily[["mkt", "small_big", "lowvol_highvol", "momentum"]]
    for name in returns:
        first = returns[name].first_valid_index()
        if first is None or returns.loc[first:, name].isna().any():
            raise ValueError(f"internal missing daily label observations: {name}")
    monthly = returns.resample("ME").sum(min_count=1)
    monthly["mkt_vol"] = daily.mkt.resample("ME").std()
    monthly = monthly.loc["2003-02-01":"2026-08-31", LABEL_COLUMNS]
    if monthly.isna().any().any():
        raise ValueError("incomplete monthly label inputs; cannot fill future observations")
    counts = returns.resample("ME").count()
    sessions = returns.resample("ME").size()
    coverage = [
        {
            "month": str(day.to_period("M")),
            "sessions": int(sessions.loc[day]),
            "valid_days": {name: int(counts.loc[day, name]) for name in returns},
        }
        for day in monthly.index
    ]
    return monthly, coverage


def build_labels(daily, historical=None):
    monthly, coverage = aggregate_monthly(daily)
    scaler = StandardScaler()
    z = scaler.fit_transform(monthly)
    model = KMeans(n_clusters=4, random_state=42, n_init=100)
    raw = pd.Series(model.fit_predict(z), index=monthly.index, name="raw_cluster")
    centers = pd.DataFrame(scaler.inverse_transform(model.cluster_centers_), columns=LABEL_COLUMNS)
    audit = {
        "accepted": False,
        "retrospective": True,
        "fit_start": str(monthly.index.min().date()),
        "fit_end": str(monthly.index.max().date()),
        "centers": centers.to_dict("index"),
        "raw_counts": {str(k): int(v) for k, v in raw.value_counts().items()},
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "kmeans_inertia": float(model.inertia_),
        "n_init": 100,
        "monthly_coverage": coverage,
        "warning": "Full-history KMeans and centered 3-month smoothing; reconstructed, not exact legacy labels.",
    }
    try:
        mapping = assign_semantics(centers)
        smooth = smooth_monthly(raw)
        named = smooth.map(mapping)
        named.index = named.index.to_period("M")
        training = named.loc["2004-01":"2020-07"]
        if set(training) != {"D", "F", "T", "P"}:
            raise ValueError("a style is absent from the smoothed training period")
        audit.update(
            accepted=True,
            mapping=mapping,
            smoothed_counts=named.value_counts().to_dict(),
            training_counts=training.value_counts().to_dict(),
        )
        episodes = []
        for _, group in named.groupby(named.ne(named.shift()).cumsum()):
            episodes.append(
                {
                    "label": group.iloc[0],
                    "start": str(group.index[0]),
                    "end": str(group.index[-1]),
                    "n_months": len(group),
                }
            )
        audit["episodes"] = episodes
        if historical is not None:
            old = historical.copy()
            old.index = (
                pd.to_datetime(old.index).to_period("M") if not isinstance(old.index, pd.PeriodIndex) else old.index
            )
            compare = pd.concat([old.rename("old"), named.replace("P", "D").rename("new_merged")], axis=1).dropna()
            audit["legacy_comparison"] = {
                "n_months": len(compare),
                "agreement": float(compare.old.eq(compare.new_merged).mean()),
                "differences": [
                    {"month": str(i), **r.to_dict()} for i, r in compare[compare.old != compare.new_merged].iterrows()
                ],
            }
        monthly["raw_cluster"] = raw
        monthly["label"] = named.to_numpy()
    except ValueError as exc:
        audit["failure_reason"] = str(exc)
        named = pd.Series(dtype=str)
        monthly["raw_cluster"] = raw
    return monthly, named, audit, {"scaler": scaler, "kmeans": model}
