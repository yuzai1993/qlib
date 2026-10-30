"""生成 regime-adapt 实验的模型输入特征 CSV（计划 v3 第 5 节 A0b 产出）.

来源: diagnostics/20260809_a0b_regime_features/{tier1,tier2}_signals_daily.csv
形态: 对入选信号做因果 z (250日滚动均值/std, shift(1) 防前视, clip ±5);
      idx_dd250/idx_uplow60 另保留原始值 (有界且量纲本身有含义, A0b 亚型判别变量).
Tier-2 历史深度不足处 保留 NaN (LightGBM 原生哨兵分支).
输出: backtest/configs/regime-adapt/regime_features_v1.csv (随 configs 冻结入 Git)
"""
from pathlib import Path

import pandas as pd

DIAG = Path("/Users/yuxianqi/Project/qlib_exp/backtest/diagnostics/20260809_a0b_regime_features")
OUT = Path("/Users/yuxianqi/Project/qlib_exp/backtest/configs/regime-adapt/regime_features_v1.csv")

TIER1_Z = ["amount_surge", "mom_cum20", "idx_uplow60", "idx_dd250",
           "lowvol_highvol_cum20", "limitup_share", "disp20"]
TIER2_Z = ["basis_pct", "shibor1w_neg_chg20"]
RAW_KEEP = ["idx_dd250", "idx_uplow60"]
Z_WIN, Z_MIN, Z_CLIP = 250, 120, 5.0


def causal_z(s: pd.Series) -> pd.Series:
    mu = s.rolling(Z_WIN, min_periods=Z_MIN).mean().shift(1)
    sd = s.rolling(Z_WIN, min_periods=Z_MIN).std().shift(1)
    return ((s - mu) / sd).clip(-Z_CLIP, Z_CLIP)


def main():
    t1 = pd.read_csv(DIAG / "tier1_signals_daily.csv", index_col=0, parse_dates=True)
    sp = pd.read_csv(
        DIAG.parent / "20260809_style_regimes/style_spreads_daily_2003_2026.csv",
        index_col=0, parse_dates=True,
    )
    t1["mom_cum20"] = sp["mom"].rolling(20).sum().reindex(t1.index)
    t1["lowvol_highvol_cum20"] = sp["lowvol_highvol"].rolling(20).sum().reindex(t1.index)
    t2 = pd.read_csv(DIAG / "tier2_signals_daily.csv", index_col=0, parse_dates=True).reindex(t1.index)

    out = pd.DataFrame(index=t1.index)
    for c in TIER1_Z:
        out[f"{c}_z"] = causal_z(t1[c])
    for c in TIER2_Z:
        out[f"{c}_z"] = causal_z(t2[c])
    for c in RAW_KEEP:
        out[c] = t1[c]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        f.write(f"# regime_features v1 | generated {pd.Timestamp.now():%Y-%m-%d} | "
                f"causal z win={Z_WIN} min={Z_MIN} clip=±{Z_CLIP} | tier2 NaN=哨兵\n")
    out.to_csv(OUT, float_format="%.5f", mode="a")
    cov = out.notna().mean()
    print(f"{OUT}: {len(out)} 天 x {out.shape[1]} 列")
    print("覆盖率:")
    print(cov.to_string(float_format="%.3f"))
    print("2004-01 后首个全 Tier-1 有效日:",
          out[[f"{c}_z" for c in TIER1_Z]].dropna().index.min())


if __name__ == "__main__":
    main()
