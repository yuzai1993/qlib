"""Bounded optional source collection and explicit release-time alignment."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd

from .data import fingerprint


def align_released(signals, records, max_age_days=10):
    dates = pd.DatetimeIndex(signals)
    left = pd.DataFrame({"date": dates, "cutoff": dates + pd.Timedelta(hours=20)})
    right = records.sort_values("available_at").drop_duplicates("available_at", keep="last")
    merged = pd.merge_asof(
        left,
        right,
        left_on="cutoff",
        right_on="available_at",
        direction="backward",
        tolerance=pd.Timedelta(days=max_age_days),
    )
    merged["age_days"] = (merged.cutoff - merged.available_at).dt.total_seconds() / 86400
    return merged.set_index("date").drop(columns=["cutoff", "available_at"])


def aggregate_daily_basic(rows):
    out = {"n": len(rows)}
    for name in ("total_mv", "circ_mv", "pe_ttm", "pb", "turnover_rate", "dv_ttm"):
        if name not in rows:
            continue
        s = pd.to_numeric(rows[name], errors="coerce")
        if name in ("pe_ttm", "pb"):
            if name == "pe_ttm":
                out["pe_nonpositive_share"] = float((s.le(0) | s.isna()).mean())
            s = s.where(s > 0)
        out[f"{name}_median"] = float(s.median())
        for q in (0.1, 0.9):
            out[f"{name}_q{int(q*100)}"] = float(s.quantile(q))
        if name in ("total_mv", "circ_mv"):
            out[f"{name}_sum"] = float(s.sum(min_count=1))
            out[f"{name}_top10_share"] = float(s.nlargest(10).sum() / s.sum())
    return out


def _client():
    import tushare as ts
    from dotenv import dotenv_values

    token = os.environ.get("TUSHARE_TOKEN")
    env_path = Path("~/.qlib_live_env").expanduser()
    if not token and env_path.is_file():
        token = dotenv_values(env_path).get("TUSHARE_TOKEN")
    if not token:
        raise ValueError("Tushare credential unavailable")
    return ts.pro_api(token, timeout=15)


def collect_optional(calendar, cache_dir, index_prices, daily_basic_limit=6000):
    """Probe availability without turning current schedules into historical facts.

    This campaign has no historical release-timestamp archive for these sources.
    A successful API response proves access, not point-in-time availability. Save
    bounded probe evidence and exclude each block until that evidence is supplied.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    calendar = pd.DatetimeIndex(calendar)
    audit = {
        "sources": {},
        "excluded": [],
        "requests": 0,
        "vendor_vintage": "current API responses; historical release timing unverified",
    }
    exclusions = [
        ("sector_membership", "no verified historical membership source"),
        ("option_IV_ETF_flows", "no historical chain/share cache with release-time evidence"),
        ("FX_overseas", "no verified historical timestamp-aligned source"),
        ("northbound_accounts", "nonuniform historical disclosures; no stable full-window series"),
    ]
    audit["excluded"].extend({"source": name, "reason": reason} for name, reason in exclusions)
    try:
        pro = _client()
    except (ImportError, ValueError) as exc:
        audit["excluded"].append({"source": "Tushare", "reason": str(exc)})
        return pd.DataFrame(index=calendar), audit
    last = calendar[-1].strftime("%Y%m%d")
    probes = [
        ("shibor", "shibor", {"start_date": calendar[-1].strftime("%Y0101"), "end_date": last}),
        ("margin", "margin", {"trade_date": last}),
        ("IF", "fut_daily", {"ts_code": "IF.CFX", "trade_date": last}),
        ("IC", "fut_daily", {"ts_code": "IC.CFX", "trade_date": last}),
        ("IM", "fut_daily", {"ts_code": "IM.CFX", "trade_date": last}),
        ("daily_basic", "daily_basic", {"trade_date": last}),
    ]
    for name, api, kwargs in probes:
        reason = "historical release times not verified; current schedule alone is insufficient"
        try:
            audit["requests"] += 1
            raw = pro.query(api, **kwargs)
            path = cache_dir / f"{name}-availability-probe.parquet"
            raw.to_parquet(path)
            audit["sources"][name] = {
                "file": fingerprint(path),
                "n_rows": len(raw),
                "historical_time_verified": False,
                "used_for_training": False,
            }
        except Exception as exc:
            # Do not persist SDK exception text, which may contain request details.
            reason += f"; availability probe failed ({type(exc).__name__})"
        audit["excluded"].append({"source": name, "reason": reason})
        print(f"optional source {name}: excluded pending historical release evidence", flush=True)
        time.sleep(0.35)
    return pd.DataFrame(index=calendar), audit
