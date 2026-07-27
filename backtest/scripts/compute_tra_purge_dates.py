"""Compute purged train/valid segment ends for TRA (41 trading days, parity with PurgedHorizonDataset)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))

import qlib
from qlib.data import D

qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

# PurgedHorizonDataset: offset = label_horizon + 1 = 41; purged end = calendar[-1 - offset]
OFFSET = 41

for name, (start, end) in {
    "train": ("2016-01-02", "2020-01-10"),
    "valid": ("2020-01-13", "2021-07-15"),
}.items():
    cal = pd.DatetimeIndex(D.calendar(start_time=start, end_time=end))
    purged_end = pd.Timestamp(cal[-1 - OFFSET]).date()
    print(f"{name}: {start} ~ {end} -> purged end {purged_end} (n_days {len(cal)})")
