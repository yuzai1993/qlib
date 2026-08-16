"""从 m0 年度特征缓存上覆盖 CSRankNorm(Hh) 训练标签（不重算 Alpha158）。

同一行样本（H40 DropnaLabel + 上市龄过滤后的 index）只换标签值，保证改标签
消融与 m0-fast H40 臂可归因。CSRankNorm 在该日缓存截面上做，与分块预处理一致。

产出: backtest/datasets/regime-adapt-cache/labels/h{1,5,10}_csrank.pkl
"""
from __future__ import annotations

import argparse
import gc
import sys
from pathlib import Path

import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP_ROOT))

from backtest.scripts.eval_ic_multi_pool import _horizon_label_expr  # noqa: E402
from backtest.scripts.prepare_regime_train_chunks import CACHE_ROOT, TRAIN_END  # noqa: E402


def csranknorm(s: pd.Series) -> pd.Series:
    t = s.groupby(level="datetime").rank(pct=True)
    return (t - 0.5) * 3.46


def overlay_year(year: int, raw: pd.Series) -> pd.Series:
    path = CACHE_ROOT / "m0" / f"year={year}.pkl"
    df = pd.read_pickle(path)
    idx = df.index
    del df
    gc.collect()
    aligned = raw.reindex(idx)
    return csranknorm(aligned)


def fetch_raw(horizon: int, start: str, end: str) -> pd.Series:
    from qlib.data import D

    expr = _horizon_label_expr(horizon)
    df = D.features(D.instruments("all"), [expr], start_time=start, end_time=end)
    s = df.iloc[:, 0]
    s.index = s.index.set_names(["instrument", "datetime"])
    return s.swaplevel().sort_index()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizons", type=int, nargs="+", default=[1, 5, 10])
    parser.add_argument("--years", type=int, nargs="*", default=None)
    args = parser.parse_args()

    import qlib

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

    years = args.years or list(range(2004, 2021))
    out_dir = CACHE_ROOT / "labels"
    out_dir.mkdir(parents=True, exist_ok=True)

    start = "2004-01-02"
    end = str(TRAIN_END.date())
    for h in args.horizons:
        out = out_dir / f"h{h}_csrank.pkl"
        if out.exists():
            print(f"[h{h}] 已存在, 跳过 {out.name}", flush=True)
            continue
        print(f"[h{h}] 拉取原始标签 {start}..{end}", flush=True)
        raw = fetch_raw(h, start, end)
        parts = []
        for year in years:
            part = overlay_year(year, raw)
            n_ok = int(part.notna().sum())
            print(f"[h{h} {year}] {n_ok}/{len(part)} 非空", flush=True)
            parts.append(part)
        s = pd.concat(parts)
        s.to_pickle(out)
        print(f"[h{h}] 写入 {out}  rows={len(s)} non-null={int(s.notna().sum())}", flush=True)
        del raw, parts, s
        gc.collect()


if __name__ == "__main__":
    main()
