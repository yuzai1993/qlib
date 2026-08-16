"""regime-adapt 训练数据分块预处理（计划 v3 7.2 节第 1 步）.

对 2004~2020 每年建一个短跨度 handler（前置 6 个月 warmup 供 Alpha158 滚动窗，
后延 3 个月供 H40 标签前视；2020 年 handler 截止 2020-07-31，标签窗跨界的行由
DropnaLabel 自动剔除 = purge），processor 后 learn 数据（DK_L）裁剪到本年训练日，
剔除上市 < 60 交易日次新股，降为 float32 pickle 落盘。

CSRankNorm（label 逐日截面）与 DropnaLabel 均为日级操作，分年处理无跨界影响。
保留全部训练时代交易日（D 态下采样在组装阶段由 train_dates_v1.csv 完成，缓存
与采样协议解耦）。

用法:
  python backtest/scripts/prepare_regime_train_chunks.py --arm m3 [--years 2019]
产出:
  backtest/datasets/regime-adapt-cache/<arm>/year=YYYY.pkl  （跑完实验按规范 6.3 清理）
"""

from __future__ import annotations

import argparse
import gc
import resource
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP_ROOT))

CACHE_ROOT = EXP_ROOT / "backtest" / "datasets" / "regime-adapt-cache"
REGIME_CSV = EXP_ROOT / "backtest" / "configs" / "regime-adapt" / "regime_features_v1.csv"

TRAIN_START = pd.Timestamp("2004-01-02")
TRAIN_END = pd.Timestamp("2020-07-31")
LABEL_EXPR = "Ref($close, -41)/Ref($close, -1)-1"
MIN_LISTING_DAYS = 60

# 训练池: 全A 股票（剔除指数/B股/北交所），与 8 年 dry-run 一致
INSTRUMENTS = {
    "market": "all",
    "filter_pipe": [
        {
            "filter_type": "NameDFilter",
            "name_rule_re": "^(SH60|SH68|SZ00|SZ30)",
            "filter_start_time": None,
            "filter_end_time": None,
        }
    ],
}


def rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9


def build_handler(arm: str, start: str, end: str, instruments=None):
    common = dict(
        feature_groups=["range"],
        instruments=instruments if instruments is not None else INSTRUMENTS,
        start_time=start,
        end_time=end,
        fit_start_time=start,
        fit_end_time=end,
        infer_processors=[{"class": "ProcessInf"}],
        learn_processors=[
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ],
        label=[[LABEL_EXPR], ["LABEL0"]],
    )
    if arm == "m3":
        from backtest.features.regime import Alpha158RegimeTechnical

        return Alpha158RegimeTechnical(regime_csv=str(REGIME_CSV), **common)
    if arm == "m0":
        from backtest.features.technical import Alpha158Technical

        return Alpha158Technical(**common)
    raise ValueError(f"unknown arm: {arm}")


def build_listing_mask(index: pd.MultiIndex, cal: pd.DatetimeIndex, first_pos: dict) -> pd.Series:
    dt_pos = np.asarray(cal.searchsorted(index.get_level_values("datetime")))
    inst_first = np.array(
        [first_pos.get(i, 10**9) for i in index.get_level_values("instrument")], dtype=np.int64
    )
    return pd.Series((dt_pos - inst_first) >= MIN_LISTING_DAYS, index=index)


def listing_first_pos(cal: pd.DatetimeIndex) -> dict:
    from qlib.data import D

    spans = D.list_instruments(
        D.instruments("all"), start_time=str(cal[0].date()), end_time=str(cal[-1].date()),
        as_list=False,
    )
    return {
        code: int(cal.searchsorted(min(pd.Timestamp(s) for s, _ in sp)))
        for code, sp in spans.items()
    }


def prepare_year(arm: str, year: int, cal: pd.DatetimeIndex, first_pos: dict, out_dir: Path) -> None:
    out_path = out_dir / f"year={year}.pkl"
    if out_path.exists():
        print(f"[{arm} {year}] 已存在, 跳过", flush=True)
        return
    t0 = time.time()
    # warmup 6 个月（Alpha158 最长滚动窗 60 日）；后延 3 个月给 H40 前视标签
    start = f"{year - 1}-07-01"
    end = min(pd.Timestamp(f"{year + 1}-03-31"), TRAIN_END)
    row_lo = max(pd.Timestamp(f"{year}-01-01"), TRAIN_START)
    row_hi = min(pd.Timestamp(f"{year}-12-31"), TRAIN_END)

    handler = build_handler(arm, start, str(end.date()))
    from qlib.data.dataset.handler import DataHandlerLP

    df = handler.fetch(slice(row_lo, row_hi), col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
    del handler
    gc.collect()

    mask = build_listing_mask(df.index, cal, first_pos)
    n_drop = int((~mask).sum())
    df = df[mask.to_numpy()]
    df = df.astype("float32")

    df.to_pickle(out_path)
    mem = df.memory_usage(deep=True).sum() / 1e9
    print(
        f"[{arm} {year}] {df.shape[0]} 行 x {df.shape[1]} 列 "
        f"(剔次新 {n_drop} 行), {mem:.2f} GB, 峰值 RSS {rss_gb():.2f} GB, "
        f"{time.time() - t0:.0f}s",
        flush=True,
    )
    del df
    gc.collect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", required=True, choices=["m0", "m3"])
    parser.add_argument("--years", type=int, nargs="*", default=None,
                        help="默认 2004..2020 全量")
    args = parser.parse_args()

    import qlib

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")
    from qlib.data import D

    out_dir = CACHE_ROOT / args.arm
    out_dir.mkdir(parents=True, exist_ok=True)

    cal = pd.DatetimeIndex(D.calendar(start_time="2003-01-01", end_time=str(TRAIN_END.date())))
    first_pos = listing_first_pos(cal)

    years = args.years or list(range(2004, 2021))
    for year in years:
        prepare_year(args.arm, year, cal, first_pos, out_dir)
    print(f"[{args.arm}] 全部完成: {sorted(p.name for p in out_dir.glob('year=*.pkl'))}", flush=True)


if __name__ == "__main__":
    main()
