"""A0c: 生成风格分层测试日清单 (regime-adapt 实验协议, 计划 v3 第 2.4 节).

流程:
  1. 候选期 2020-08-03 ~ 2026-07-31 的交易日, 按月度风格标签 (D/F/T) 归类;
  2. 按全历史风格占比确定各态目标天数 (F 稀缺, 全取, 反推总量);
  3. 分层内随机 70/30 拆分 (seed=42):
       test_dates_stratified_70.csv  -> 本实验 valid+test
       test_dates_reserved_30.csv    -> 封存, 留给后续"增加最新样本"实验

清单一经预登记冻结, 不得改动. 若风格划分修订, 须带新版本标签重跑本脚本.
"""
import numpy as np
import pandas as pd
from pathlib import Path

SEED = 42
CAND_START = pd.Timestamp("2020-08-03")
CAND_END = pd.Timestamp("2026-07-31")
CALENDAR = Path("~/.qlib/qlib_data/cn_data/calendars/day.txt").expanduser()
LABELS_CSV = "/Users/yuxianqi/Project/qlib_exp/backtest/diagnostics/20260809_style_regimes/monthly_regime_3style.csv"
LABELS_VERSION = "monthly_regime_3style.csv (v1, 2026-08-09, A0a 复核后维持不变)"
OUT_DIR = Path("/Users/yuxianqi/Project/qlib_exp/backtest/configs/regime-adapt")


def main():
    cal = pd.to_datetime(pd.read_csv(CALENDAR, header=None)[0])
    days = cal[(cal >= CAND_START) & (cal <= CAND_END)].reset_index(drop=True)

    lab = pd.read_csv(LABELS_CSV, index_col=0, parse_dates=True)["regime3"]
    # 全历史占比 (月度)
    hist_prop = lab.value_counts(normalize=True)
    print("全历史月度占比:", {k: f"{v:.3f}" for k, v in hist_prop.items()})

    day_regime = lab.reindex(days.dt.to_period("M").dt.to_timestamp("M").values)
    df = pd.DataFrame({"date": days.values, "regime": day_regime.values}).dropna()
    avail = df["regime"].value_counts()
    print("候选期各态可用交易日:", avail.to_dict())

    n_f = int(avail.get("F", 0))  # F 稀缺, 全取
    total = n_f / hist_prop["F"]
    target = {
        "F": n_f,
        "D": min(int(round(total * hist_prop["D"])), int(avail.get("D", 0))),
        "T": min(int(round(total * hist_prop["T"])), int(avail.get("T", 0))),
    }
    print("目标采样天数:", target, f"(反推总量 {total:.0f})")

    rng = np.random.RandomState(SEED)
    picked = []
    for reg, n in target.items():
        pool = df[df["regime"] == reg]["date"].values
        sel = pool if len(pool) <= n else rng.choice(pool, size=n, replace=False)
        picked.append(pd.DataFrame({"date": np.sort(sel), "regime": reg}))
    sample = pd.concat(picked).sort_values("date").reset_index(drop=True)

    # 分层内随机 70/30
    parts_70, parts_30 = [], []
    for reg, g in sample.groupby("regime"):
        idx = rng.permutation(len(g))
        k = int(round(len(g) * 0.7))
        parts_70.append(g.iloc[np.sort(idx[:k])])
        parts_30.append(g.iloc[np.sort(idx[k:])])
    d70 = pd.concat(parts_70).sort_values("date")
    d30 = pd.concat(parts_30).sort_values("date")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    header = (
        f"# frozen 2026-08-09 | seed={SEED} | labels={LABELS_VERSION}\n"
        f"# candidate={CAND_START:%Y-%m-%d}..{CAND_END:%Y-%m-%d} | 计划 v3 2.4 节, 预登记后不得改动\n"
    )
    for name, d in [("test_dates_stratified_70.csv", d70), ("test_dates_reserved_30.csv", d30)]:
        p = OUT_DIR / name
        with open(p, "w") as f:
            f.write(header)
        d.assign(date=d["date"].astype(str).str[:10]).to_csv(OUT_DIR / name, index=False, mode="a")
        print(f"{p}: {len(d)} 天, 构成 {d['regime'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
