"""Pure functions for B2-S + IM beta overlay (lagged rolling beta, port construction)."""

from __future__ import annotations

import pandas as pd

IM_WINDOW = ("2022-07-22", "2026-07-31")
TARGET_BETA = 1.0
ROLL_WINDOW = 60


def rolling_beta_lagged(
    net: pd.Series, bench: pd.Series, window: int = ROLL_WINDOW
) -> pd.Series:
    frame = pd.concat([net.rename("net"), bench.rename("bench")], axis=1)
    return (
        frame["net"].rolling(window).cov(frame["bench"])
        / frame["bench"].rolling(window).var()
    ).shift(1)


def overlay_from_gap(net: pd.Series, gap: pd.Series, fut_ret: pd.Series) -> pd.Series:
    return (net + gap * fut_ret).rename("port")


def apply_beta_overlay(
    net: pd.Series,
    bench: pd.Series,
    fut_ret: pd.Series,
    *,
    target: float = TARGET_BETA,
    window: int = ROLL_WINDOW,
) -> pd.DataFrame:
    beta_hat = rolling_beta_lagged(net, bench, window=window)
    gap = (target - beta_hat).rename("gap")
    port = overlay_from_gap(net, gap, fut_ret)
    return pd.concat(
        [
            net.rename("net"),
            bench.rename("bench"),
            beta_hat.rename("beta_hat"),
            gap,
            fut_ret.rename("fut_ret"),
            port,
        ],
        axis=1,
    )


def discrete_lots(
    gap: pd.Series,
    account_value: pd.Series,
    settle: pd.Series,
    *,
    multiplier: int = 200,
) -> pd.Series:
    return (gap * account_value / (settle * multiplier)).round().astype("Int64")


def slice_im_window(frame: pd.DataFrame) -> pd.DataFrame:
    start, end = IM_WINDOW
    return frame.loc[start:end]


def report_from_port(port: pd.Series, base_report: pd.DataFrame) -> pd.DataFrame:
    out = base_report.copy()
    out = out.reindex(port.dropna().index)
    out["return"] = port.reindex(out.index).astype(float)
    out["cost"] = 0.0
    return out
