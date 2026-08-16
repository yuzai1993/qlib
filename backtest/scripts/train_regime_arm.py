"""regime-adapt M0/M3 训练驱动器（计划 v3 7.2 节分块准备架构）.

流程（顺序为内存峰值最优）：
1. 先建 valid 推理 handler（2020-02 warmup ~ 2026-07-31，DK_I），取 70% 冻结分层
   日集的特征 + 次日 label（fixed_next_day_valid_frame），剔次新后降 float32，释放 handler；
   valid 帧按臂缓存复用（种子间相同）；
2. 再拼接年度缓存块（prepare_regime_train_chunks.py 产物），过滤到 train_dates_v1.csv
   保留日集（D 态下采样行协议）；
3. RegimeWeightedDEnsembleModel（B6-M 冻结超参 + day 级权重）fit_prepared 训练；
4. 产物写成 eval_ic_multi_pool 兼容的 session 目录（meta.json + run_01/mlruns_link.json
   + artifacts/trained_model + train_summary.json）。

用法:
  python backtest/scripts/train_regime_arm.py --arm m3 --seed 42
冒烟:
  python backtest/scripts/train_regime_arm.py --arm m3 --seed 42 --years 2019 \
      --epochs 15 --early-stopping-rounds 5 --valid-instruments csi300 \
      --session-name smoke_m3_s42
"""

from __future__ import annotations

import argparse
import gc
import json
import pickle
import resource
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP_ROOT))

from backtest.scripts.prepare_regime_train_chunks import (  # noqa: E402
    CACHE_ROOT,
    INSTRUMENTS,
    LABEL_EXPR,
    REGIME_CSV,
    build_listing_mask,
    listing_first_pos,
    rss_gb,
)

CONF_DIR = EXP_ROOT / "backtest" / "configs" / "regime-adapt"
RESULT_ROOT = EXP_ROOT / "backtest" / "result"

TRAIN_SEGMENT = ("2004-01-02", "2020-07-31")
VALID_WARMUP_START = "2020-02-03"  # 70% 日集自 2020-08-03 起，前置 ~6 个月滚动窗 warmup
VALID_END = "2026-07-31"
TRAIN_DATES_CSV = CONF_DIR / "train_dates_v1.csv"
VALID_DATES_CSV = CONF_DIR / "test_dates_stratified_70.csv"
DAY_WEIGHTS_CSV = {
    "m0": CONF_DIR / "day_weights_m0_v1.csv",  # 仅下采样补偿 → 期望自然分布
    "m3": CONF_DIR / "day_weights_m3_v1.csv",  # 55/30/15 + 48m 半衰期 + 补偿
}
HANDLER_NAME = {"m0": "Alpha158Technical", "m3": "Alpha158RegimeTechnical"}

# 阶段 1 筛选臂：B3-M 冻结超参（feature-b2/range 单 LGBModel，除 seed 外不动）
# + cs-rank-norm 标签（分块缓存自带）；早停协议同 DoubleEnsemble 臂（RankIC 锚点）
FROZEN_SINGLE_KWARGS = dict(
    colsample_bytree=0.8879,
    learning_rate=0.2,
    subsample=0.8789,
    lambda_l1=205.6999,
    lambda_l2=580.9768,
    max_depth=8,
    num_leaves=210,
    num_threads=8,
    num_boost_round=200,
    early_stopping_rounds=20,
)

# 阶段 2 确认臂：B6-M 冻结超参（mh_rankic_es_lr010，除 seed 外不动）
FROZEN_MODEL_KWARGS = dict(
    base_model="gbm",
    loss="mse",
    num_models=3,
    enable_sr=True,
    enable_fs=True,
    alpha1=1,
    alpha2=1,
    bins_sr=10,
    bins_fs=5,
    decay=0.5,
    sample_ratios=[0.8, 0.7, 0.6, 0.5, 0.4],
    sub_weights=[1, 1, 1],
    epochs=200,
    colsample_bytree=0.8879,
    learning_rate=0.1,
    subsample=0.8789,
    lambda_l1=205.6999,
    lambda_l2=580.9768,
    max_depth=8,
    num_leaves=210,
    num_threads=8,
    early_stopping_rounds=20,
)


def build_valid_frame(
    arm: str,
    valid_dates: pd.DatetimeIndex,
    instruments=None,
) -> pd.DataFrame:
    """独立推理 handler 准备 valid 帧（feature@DK_I + 次日 label），随后释放。

    instruments 仅供冒烟调试换小池；正式跑必须为 None（全A，与评估北极星一致）。
    """
    from qlib.data import D
    from qlib.data.dataset import DatasetH

    from backtest.models.rankic_early_stop import fixed_next_day_valid_frame
    from backtest.scripts.prepare_regime_train_chunks import build_handler

    t0 = time.time()
    handler = build_handler(arm, VALID_WARMUP_START, VALID_END, instruments=instruments)
    dataset = DatasetH(
        handler=handler,
        segments={
            "train": TRAIN_SEGMENT,
            "valid": ("2020-08-03", VALID_END),
            "test": ("2020-08-03", VALID_END),
        },
    )
    frame = fixed_next_day_valid_frame(
        dataset, protocol_id="regime-adapt-v1", valid_dates=valid_dates
    )
    del dataset, handler
    gc.collect()

    cal = pd.DatetimeIndex(D.calendar(start_time="2003-01-01", end_time=VALID_END))
    mask = build_listing_mask(frame.index, cal, listing_first_pos(cal))
    n_drop = int((~mask).sum())
    frame = frame[mask.to_numpy()].astype("float32")
    counts = frame.groupby(level="datetime").size()
    if (counts < 20).any():
        raise ValueError("valid frame has days with <20 instruments after listing filter")
    print(
        f"[valid] {frame.shape[0]} 行 x {frame.shape[1]} 列, "
        f"{counts.size} 天 (剔次新 {n_drop} 行), "
        f"{frame.memory_usage(deep=True).sum() / 1e9:.2f} GB, "
        f"{time.time() - t0:.0f}s, 峰值 RSS {rss_gb():.2f} GB",
        flush=True,
    )
    return frame


def load_train_matrix(arm: str, years: list[int]) -> pd.DataFrame:
    """拼接年度缓存块并过滤到保留训练日集（D 态下采样行协议）。"""
    kept = pd.read_csv(TRAIN_DATES_CSV, comment="#")
    kept_dates = pd.DatetimeIndex(pd.to_datetime(kept["date"]))
    t0 = time.time()
    parts: list[pd.DataFrame] = []
    columns = None
    for year in years:
        path = CACHE_ROOT / arm / f"year={year}.pkl"
        if not path.exists():
            raise FileNotFoundError(f"缺少年度块: {path} (先跑 prepare_regime_train_chunks.py)")
        df = pd.read_pickle(path)
        if columns is None:
            columns = df.columns
        elif not df.columns.equals(columns):
            raise ValueError(f"年度块列结构不一致: {path}")
        keep = df.index.get_level_values("datetime").isin(kept_dates)
        parts.append(df[keep])
        del df
    train = pd.concat(parts, copy=False)
    del parts
    gc.collect()
    dts = train.index.get_level_values("datetime")
    print(
        f"[train] {train.shape[0]} 行 x {train.shape[1]} 列, "
        f"{dts.nunique()} 天 ({dts.min().date()}..{dts.max().date()}), "
        f"{train.memory_usage(deep=True).sum() / 1e9:.2f} GB, "
        f"{time.time() - t0:.0f}s, 峰值 RSS {rss_gb():.2f} GB",
        flush=True,
    )
    return train


def save_session(
    arm: str,
    seed: int,
    session_name: str,
    model,
    summary: dict,
) -> Path:
    session_dir = RESULT_ROOT / session_name
    run_dir = session_dir / "run_01"
    artifacts_dir = run_dir / "artifacts_root" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    with open(artifacts_dir / "trained_model", "wb") as fh:
        pickle.dump(model, fh)

    meta = {
        "session_name": session_name,
        "note": f"regime-adapt {arm} seed={seed}",
        "mode": "train_only",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "n_runs": 1,
        "config_path": str(Path(__file__).resolve()),
        "provider_uri": "~/.qlib/qlib_data/cn_data",
        "market": "all",
        "benchmark": None,
        "handler": HANDLER_NAME[arm],
        "segments": {
            "train": list(TRAIN_SEGMENT),
            "valid": ["2020-08-03", VALID_END],
            "test": ["2020-08-03", VALID_END],
        },
        "regime_adapt": {
            "arm": arm,
            "seed": seed,
            "train_dates_csv": str(TRAIN_DATES_CSV.relative_to(EXP_ROOT)),
            "valid_dates_csv": str(VALID_DATES_CSV.relative_to(EXP_ROOT)),
            "day_weights_csv": str(DAY_WEIGHTS_CSV[arm].relative_to(EXP_ROOT)),
            "regime_csv": str(REGIME_CSV.relative_to(EXP_ROOT)) if arm == "m3" else None,
            "label": summary.get("label_expr", LABEL_EXPR),
            "label_horizon": summary.get("label_horizon", 40),
            "instruments": INSTRUMENTS,
            "model": summary.get("model", "densemble"),
            "frozen_hyperparams": summary.get("frozen_hyperparams", "B6-M (mh_rankic_es_lr010)"),
        },
    }
    (session_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "mlruns_link.json").write_text(
        json.dumps(
            {"train_artifacts": f"backtest/result/{session_name}/run_01/artifacts_root"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "train_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return session_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", required=True, choices=["m0", "m3"])
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--model",
        default="single",
        choices=["single", "densemble"],
        help="single=阶段1 B3-M 单 LGBM 筛选臂; densemble=阶段2 B6-M DoubleEnsemble 确认臂",
    )
    parser.add_argument("--years", type=int, nargs="*", default=None, help="默认 2004..2020")
    parser.add_argument("--epochs", type=int, default=None, help="冒烟用覆盖，正式跑勿用")
    parser.add_argument("--early-stopping-rounds", type=int, default=None, help="冒烟用覆盖")
    parser.add_argument("--session-name", default=None)
    parser.add_argument(
        "--valid-instruments",
        default=None,
        help="冒烟用小池（如 csi300）替换 valid handler 全A池，正式跑勿用",
    )
    parser.add_argument(
        "--label-horizon",
        type=int,
        default=40,
        help="训练标签期限；40=缓存自带 H40。其余从 labels/ 缓存覆盖 CSRankNorm 标签",
    )
    args = parser.parse_args()

    import qlib

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

    from backtest.models.rankic_early_stop import load_valid_dates
    from backtest.models.regime_adapt import RegimeWeightedDEnsembleModel

    years = args.years or list(range(2004, 2021))
    single = args.model == "single"
    kwargs = dict(FROZEN_SINGLE_KWARGS if single else FROZEN_MODEL_KWARGS)
    overridden = False
    if args.epochs is not None:
        kwargs["num_boost_round" if single else "epochs"] = args.epochs
        overridden = True
    if args.early_stopping_rounds is not None:
        kwargs["early_stopping_rounds"] = args.early_stopping_rounds
        overridden = True
    if args.label_horizon not in (1, 2, 3, 5, 10, 20, 40):
        raise ValueError("--label-horizon must be 1, 2, 3, 5, 10, 20 or 40")
    if args.valid_instruments is not None:
        overridden = True
    tag = "regimeadaptfast" if single else "regimeadapt"
    h_tag = "" if args.label_horizon == 40 else f"h{args.label_horizon}"
    session_name = args.session_name or (
        f"{datetime.now():%Y%m%d_%H%M%S}_{tag}_{args.arm}{h_tag}_s{args.seed}"
    )

    t_all = time.time()
    valid_dates = load_valid_dates(str(VALID_DATES_CSV), ("2020-08-03", VALID_END))
    # valid 帧确定性（冻结日集 + 冻结 handler 配置），按臂缓存复用（种子间相同）
    valid_cache = (
        CACHE_ROOT / args.arm / "valid_frame_70.pkl" if args.valid_instruments is None else None
    )
    if valid_cache is not None and valid_cache.exists():
        df_valid = pd.read_pickle(valid_cache)
        print(f"[valid] 命中缓存 {valid_cache.name}: {df_valid.shape[0]} 行", flush=True)
    else:
        df_valid = build_valid_frame(args.arm, valid_dates, instruments=args.valid_instruments)
        if valid_cache is not None:
            df_valid.to_pickle(valid_cache)
    df_train = load_train_matrix(args.arm, years)
    if args.label_horizon != 40:
        lab_path = CACHE_ROOT / "labels" / f"h{args.label_horizon}_csrank.pkl"
        if not lab_path.exists():
            raise FileNotFoundError(
                f"缺少改标签缓存 {lab_path}（先跑 build_regime_alt_labels.py）"
            )
        # 标签缓存在 m0 年度块的行 index 上构建；m0/m3 年度块 index 逐行相同
        # （m3 只多 11 列 regime 特征），故 m3 复用同一缓存是等价的，且能保证
        # m3-Hh 与 m0-Hh 的训练目标数值完全一致、差异只来自特征与样本权重。
        alt = pd.read_pickle(lab_path)
        mapped = alt.reindex(df_train.index)
        n_na = int(mapped.isna().sum())
        hit_rate = 1.0 - n_na / max(1, len(mapped))
        if hit_rate < 0.95:
            raise ValueError(
                f"改标签缓存与 {args.arm} 训练块 index 命中率仅 {hit_rate:.2%}，"
                "低于 95%，可能缓存与特征块不同源，请重建后再跑"
            )
        col = ("label", "LABEL0")
        df_train = df_train.copy()
        df_train[col] = mapped.to_numpy()
        df_train = df_train.loc[mapped.notna().to_numpy()]
        print(
            f"[label] overlay H{args.label_horizon} on {args.arm}  dropna={n_na}  "
            f"hit={hit_rate:.4%}  remain={df_train.shape[0]}",
            flush=True,
        )
        del alt, mapped
        gc.collect()

    from backtest.models.regime_adapt import RegimeSingleLGBMModel

    model_cls = RegimeSingleLGBMModel if single else RegimeWeightedDEnsembleModel
    model = model_cls(
        protocol_id="regime-adapt-v1",
        valid_dates_csv=str(VALID_DATES_CSV),
        day_weights_csv=str(DAY_WEIGHTS_CSV[args.arm]),
        seed=args.seed,
        **kwargs,
    )
    t0 = time.time()
    model.fit_prepared(df_train, df_valid)
    t_fit = time.time() - t0
    print(f"[fit] 完成 {t_fit:.0f}s, 峰值 RSS {rss_gb():.2f} GB", flush=True)

    summary = {
        "arm": args.arm,
        "seed": args.seed,
        "model": args.model,
        "frozen_hyperparams": "B3-M (feature-b2/range) + cs-rank-norm" if single else "B6-M (mh_rankic_es_lr010)",
        "label_horizon": args.label_horizon,
        "label_expr": (
            LABEL_EXPR
            if args.label_horizon == 40
            else f"Ref($close, -{args.label_horizon + 1})/Ref($close, -1) - 1"
        ),
        "hyperparam_override": overridden,
        "train_shape": list(df_train.shape),
        "train_days": int(df_train.index.get_level_values("datetime").nunique()),
        "valid_shape": list(df_valid.shape),
        "valid_days": int(df_valid.index.get_level_values("datetime").nunique()),
        "rankic_evals": model.rankic_evals_result,
        "fit_seconds": round(t_fit, 1),
        "total_seconds": round(time.time() - t_all, 1),
        "peak_rss_gb": round(rss_gb(), 2),
    }
    session_dir = save_session(args.arm, args.seed, session_name, model, summary)
    print(f"[done] session={session_dir.relative_to(EXP_ROOT)}", flush=True)
    print(json.dumps(summary["rankic_evals"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
