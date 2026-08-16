"""生成 regime-adapt 实验的训练日保留清单与 day 级权重表（计划 v3 第 3/7.2 节）.

行采样协议（两臂 M0/M3 共用，保证样本行数与内存对齐）：
- 训练日 = 交易日历 [2004-01-02, 2020-07-31]，风格取当月 monthly_regime_3style.csv 标签。
- D 态日下采样（seed=42）：保留数 = n_F * (55/30)，F/T 全保留（对齐目标占比 D/F=55/30）。
- 下采样在期望意义下等效于权重（补偿因子 1/f_D），直接减少样本行数省内存。

权重（date,weight，均值归一到 1）：
- M0（自然分布锚点）：weight = 下采样补偿（D 态 1/f_D，F/T 为 1），无风格上调、无时间衰减，
  期望意义下还原自然分布。
- M3（风格平衡臂）：weight = g(regime) * decay(t) * 下采样补偿；
  decay = 0.5^(距 2020-07-31 的月数 / 48)；g 按"保留日集内各风格 总权重占比 = 55/30/15"反解。

输出（backtest/configs/regime-adapt/，冻结后入 Git）：
- train_dates_v1.csv       date,regime  保留训练日
- day_weights_m0_v1.csv    date,weight
- day_weights_m3_v1.csv    date,weight

注：v1 已于 2026-08-09 生成并冻结（seed=42），本脚本保留用于复现审计，勿重跑覆盖。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = EXP_ROOT / "backtest" / "configs" / "regime-adapt"
REGIME_CSV = EXP_ROOT / "backtest" / "diagnostics" / "20260809_style_regimes" / "monthly_regime_3style.csv"
CALENDAR = Path("~/.qlib/qlib_data/cn_data/calendars/day.txt").expanduser()

TRAIN_START = pd.Timestamp("2004-01-02")
TRAIN_END = pd.Timestamp("2020-07-31")
TARGET = {"D": 0.55, "F": 0.30, "T": 0.15}
DECAY_HALFLIFE_MONTHS = 48.0
SEED = 42


def load_train_days() -> pd.DataFrame:
    cal = pd.DatetimeIndex(pd.read_csv(CALENDAR, header=None)[0].map(pd.Timestamp))
    days = cal[(cal >= TRAIN_START) & (cal <= TRAIN_END)]

    monthly = pd.read_csv(REGIME_CSV, parse_dates=["datetime"])
    monthly["month"] = monthly["datetime"].dt.to_period("M")
    regime_by_month = monthly.set_index("month")["regime3"]

    df = pd.DataFrame({"date": days})
    df["regime"] = df["date"].dt.to_period("M").map(regime_by_month)
    if df["regime"].isna().any():
        missing = sorted(df.loc[df["regime"].isna(), "date"].dt.to_period("M").unique())
        raise ValueError(f"monthly regime labels missing for train months: {missing}")
    return df


def main() -> None:
    df = load_train_days()
    n = df.groupby("regime")["date"].count()
    print(f"训练时代交易日: 共 {len(df)} 天, 自然分布 {n.to_dict()}")

    # --- D 态下采样：保留数对齐 D/F = 55/30 ---
    keep_d = int(round(n["F"] * TARGET["D"] / TARGET["F"]))
    if keep_d >= n["D"]:
        raise ValueError("D 态自然数量不足以下采样，检查标签")
    f_d = keep_d / n["D"]
    rng = np.random.default_rng(SEED)
    d_idx = df.index[df["regime"] == "D"].to_numpy()
    kept_d = set(rng.choice(d_idx, size=keep_d, replace=False))
    df["kept"] = df["regime"].ne("D") | df.index.isin(kept_d)
    kept = df[df["kept"]].drop(columns="kept").reset_index(drop=True)
    nk = kept.groupby("regime")["date"].count()
    print(f"D 态下采样: 保留 {keep_d}/{n['D']} (f_D={f_d:.4f}); 保留日集 {nk.to_dict()}")

    # --- 下采样补偿因子（期望意义还原自然分布）---
    comp = kept["regime"].map(lambda r: 1.0 / f_d if r == "D" else 1.0)

    # --- M0: 仅补偿, 无衰减/上调 ---
    w_m0 = comp / comp.mean()

    # --- M3: g(regime) * decay * 补偿 ---
    delta_months = (TRAIN_END - kept["date"]).dt.days / 30.4375
    decay = np.power(0.5, delta_months / DECAY_HALFLIFE_MONTHS)
    base = comp * decay
    mass = base.groupby(kept["regime"]).sum()
    g = {r: TARGET[r] / mass[r] for r in TARGET}
    w_m3 = base * kept["regime"].map(g)
    w_m3 = w_m3 / w_m3.mean()

    for name, w in [("M0", w_m0), ("M3", w_m3)]:
        share = w.groupby(kept["regime"]).sum() / w.sum()
        ess = float(w.sum() ** 2 / (w**2).sum())
        print(f"{name}: 权重质量占比 {share.round(4).to_dict()}, "
              f"有效样本日数 ESS={ess:.0f}/{len(kept)}, "
              f"weight 范围 [{w.min():.4f}, {w.max():.4f}]")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hdr = (
        f"# regime-adapt train day sampling v1 | frozen 2026-08-09 | seed={SEED}\n"
        f"# train={TRAIN_START.date()}..{TRAIN_END.date()} | labels=monthly_regime_3style.csv\n"
        f"# D downsample keep={keep_d}/{n['D']} (f_D={f_d:.6f}); target D/F/T=55/30/15; "
        f"decay halflife={DECAY_HALFLIFE_MONTHS:.0f}m (M3 only)\n"
    )

    def dump(path: Path, frame: pd.DataFrame) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(hdr)
            frame.to_csv(fh, index=False)
        print(f"写出 {path.relative_to(EXP_ROOT)} ({len(frame)} 行)")

    dump(OUT_DIR / "train_dates_v1.csv",
         kept.assign(date=kept["date"].dt.strftime("%Y-%m-%d"))[["date", "regime"]])
    dump(OUT_DIR / "day_weights_m0_v1.csv",
         pd.DataFrame({"date": kept["date"].dt.strftime("%Y-%m-%d"), "weight": w_m0.round(8)}))
    dump(OUT_DIR / "day_weights_m3_v1.csv",
         pd.DataFrame({"date": kept["date"].dt.strftime("%Y-%m-%d"), "weight": w_m3.round(8)}))


if __name__ == "__main__":
    main()
