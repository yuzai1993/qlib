"""Diagnose vol20-scaled vs raw H40 ranking divergence on a short CSI1000 window."""

from __future__ import annotations

import sys
from pathlib import Path

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))

import pandas as pd

import qlib
from qlib.data import D

qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

raw = "Ref($close, -41)/Ref($close, -1)-1"
vol = "Std($close/Ref($close,1)-1,20)"
scaled = f"({raw})/If(Gt({vol},0.005),{vol},0.005)"

df = D.features(
    D.instruments("csi1000"),
    [raw, vol, scaled],
    start_time="2021-07-16",
    end_time="2021-08-16",
)
df.columns = ["raw", "vol", "scaled"]
df = df.dropna()
print("rows", len(df))

spearman = []
raw_vol = []
scaled_vol = []
for _, g in df.groupby(level=0):
    if len(g) < 50:
        continue
    spearman.append(g["raw"].corr(g["scaled"], method="spearman"))
    raw_vol.append(g["raw"].corr(g["vol"], method="spearman"))
    scaled_vol.append(g["scaled"].corr(g["vol"], method="spearman"))

sp = pd.Series(spearman)
print(
    "daily Spearman(raw, scaled): mean",
    round(sp.mean(), 4),
    "std",
    round(sp.std(), 4),
    "min",
    round(sp.min(), 4),
)
print("vol floor hit rate:", round(float((df["vol"] <= 0.005).mean()), 4))
print("vol describe:", {k: round(v, 4) for k, v in df["vol"].describe().to_dict().items()})
print("Spearman(raw, vol):", round(float(pd.Series(raw_vol).mean()), 4))
print("Spearman(scaled, vol):", round(float(pd.Series(scaled_vol).mean()), 4))
