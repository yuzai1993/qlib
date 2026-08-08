"""Build IM continuous (most-liquid) futures series from CFFEX daily settle cache.

Each trading day picks the IM contract with highest volume (tie-break: open
interest, then contract code). Overnight returns use the **held** contract's
settle-to-settle change; rolls do not inject price jumps.

Usage:
    /opt/anaconda3/envs/qlib/bin/python backtest/scripts/build_im_continuous.py \\
        --cffex-dir backtest/experiments/ic/cffex_daily \\
        --output backtest/data/im/im_continuous_daily.csv \\
        --write-hash
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

IM_CONTRACT_RE = re.compile(r"^IM\d{4}$")
NUMERIC_COLS = ["成交量", "持仓量", "今结算"]


def select_active_contracts(raw: pd.DataFrame) -> pd.DataFrame:
    """Pick the most-liquid IM contract per date."""
    required = {"date", "合约代码", "成交量", "持仓量", "今结算"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"raw missing columns: {sorted(missing)}")

    work = raw.sort_values(
        ["date", "成交量", "持仓量", "合约代码"],
        ascending=[True, False, False, True],
    )
    active = work.groupby("date", as_index=True).first()
    return active.rename(
        columns={
            "合约代码": "contract",
            "今结算": "settle",
            "成交量": "volume",
            "持仓量": "oi",
        }
    )[["contract", "settle", "volume", "oi"]]


def settle_to_settle_returns(
    settle_panel: pd.DataFrame, held_contract: pd.Series
) -> pd.Series:
    """Settle-to-settle returns on the overnight-held contract."""
    held = held_contract.reindex(settle_panel.index)
    rets: list[float] = []
    for i, dt in enumerate(settle_panel.index):
        contract = held.iloc[i]
        if pd.isna(contract) or i == 0:
            rets.append(np.nan)
            continue
        prev = settle_panel.index[i - 1]
        if contract not in settle_panel.columns:
            rets.append(np.nan)
            continue
        s0 = settle_panel.at[prev, contract]
        s1 = settle_panel.at[dt, contract]
        if pd.isna(s0) or pd.isna(s1) or s0 == 0:
            rets.append(np.nan)
        else:
            rets.append(float(s1 / s0 - 1.0))
    return pd.Series(rets, index=settle_panel.index, name="fut_ret")


def _load_cffex_raw(cffex_dir: Path) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for path in sorted(cffex_dir.glob("*.csv")):
        parts.append(pd.read_csv(path, parse_dates=["date"]))
    if not parts:
        raise FileNotFoundError(f"no CSV files under {cffex_dir}")

    raw = pd.concat(parts, ignore_index=True)
    raw["合约代码"] = raw["合约代码"].astype(str).str.strip()
    for col in NUMERIC_COLS:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw = raw[raw["合约代码"].str.match(IM_CONTRACT_RE)].copy()
    raw = raw.drop_duplicates(subset=["date", "合约代码"], keep="last")
    return raw.sort_values(["date", "合约代码"]).reset_index(drop=True)


def build_continuous_from_cffex_dir(cffex_dir: Path) -> pd.DataFrame:
    """Build continuous IM series from a directory of monthly CFFEX CSVs."""
    raw = _load_cffex_raw(cffex_dir)
    active = select_active_contracts(raw)
    settle_panel = raw.pivot_table(
        index="date", columns="合约代码", values="今结算", aggfunc="first"
    ).sort_index()

    held = active["contract"].shift(1)
    fut_ret = settle_to_settle_returns(settle_panel, held)

    out = active.copy()
    out["fut_ret"] = fut_ret
    out["roll"] = out["contract"] != out["contract"].shift(1)
    return out.reset_index()


def _write_hash(output: Path, digest: str) -> Path:
    hash_path = output.with_suffix(output.suffix + ".sha256")
    hash_path.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return hash_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build IM continuous futures CSV")
    parser.add_argument(
        "--cffex-dir",
        type=Path,
        required=True,
        help="Directory of monthly CFFEX daily CSV caches (YYYYMM.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output CSV path",
    )
    parser.add_argument(
        "--write-hash",
        action="store_true",
        help="Write sidecar <output>.sha256 with content hash",
    )
    args = parser.parse_args(argv)

    df = build_continuous_from_cffex_dir(args.cffex_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)

    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f"wrote {args.output} ({len(df)} rows)")
    print(f"sha256={digest}")
    if args.write_hash:
        hash_path = _write_hash(args.output, digest)
        print(f"wrote {hash_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
