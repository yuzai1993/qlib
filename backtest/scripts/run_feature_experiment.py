"""
特征消融实验脚本（E0~E4）。

E0: Alpha158 基线
E1: Alpha158 + MOM
E2: Alpha158 + BOLL
E3: Alpha158 + TREND
E4: Alpha158 + MOM + BOLL + TREND

每组用多个 LGB seed 训练，评估：
- SigAnaRecord: IC / ICIR / Rank IC / Rank ICIR
- PortAnaRecord: 组合绩效（含费年化超额、IR、回撤等）
- LGB feature importance（gain）及新特征占比

结果写入 backtest/result/feature_exp/{Ei}/seed_XX/

用法：
  python backtest/scripts/run_feature_experiment.py --exp E0 E1 --seeds 3
  python backtest/scripts/run_feature_experiment.py --exp all --seeds 3
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))

from backtest.features.expressions import GROUP_PREFIXES
from backtest.scripts.summarize_feature_exp import write_comparison

# ─────────────────────────────────────────────
# 与 run_backtest.py 保持一致的数据 / 回测配置
# ─────────────────────────────────────────────
PROVIDER_URI = "~/.qlib/qlib_data/cn_data"
MARKET = "csi300"
BENCHMARK = "SH000300"

RESULT_ROOT = Path(__file__).resolve().parents[1] / "result" / "feature_exp"

DATA_HANDLER_CONFIG = {
    "start_time": "2003-01-02",
    "end_time": "2026-03-10",
    "fit_start_time": "2003-01-02",
    "fit_end_time": "2020-01-10",
    "instruments": MARKET,
}

SEGMENTS = {
    "train": ("2003-01-02", "2020-01-10"),
    "valid": ("2020-01-13", "2023-09-15"),
    "test": ("2023-09-18", "2026-03-10"),
}

LGB_BASE_KWARGS = {
    "loss": "mse",
    "colsample_bytree": 0.8879,
    "learning_rate": 0.2,
    "subsample": 0.8789,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 8,
    "num_leaves": 210,
    "num_threads": 20,
}

PORT_ANALYSIS_CONFIG = {
    "executor": {
        "class": "SimulatorExecutor",
        "module_path": "qlib.backtest.executor",
        "kwargs": {
            "time_per_step": "day",
            "generate_portfolio_metrics": True,
        },
    },
    "strategy": {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy.signal_strategy",
        "kwargs": {
            "topk": 50,
            "n_drop": 5,
        },
    },
    "backtest": {
        "start_time": "2023-09-18",
        "end_time": "2026-03-10",
        "account": 1000000,
        "benchmark": BENCHMARK,
        "exchange_kwargs": {
            "freq": "day",
            "limit_threshold": 0.095,
            "deal_price": "close",
            "open_cost": 0.0005,
            "close_cost": 0.0015,
            "min_cost": 5,
        },
    },
}

# 实验组 → 特征组开关（空 = 纯 Alpha158）
EXP_GROUPS: Dict[str, List[str]] = {
    "E0": [],
    "E1": ["mom"],
    "E2": ["boll"],
    "E3": ["trend"],
    "E4": ["mom", "boll", "trend"],
}

DEFAULT_SEEDS = (0, 1, 2)


def build_task(feature_groups: Sequence[str], seed: int) -> dict:
    """构建单次训练任务配置。"""
    handler_kwargs = dict(DATA_HANDLER_CONFIG)
    if feature_groups:
        handler_cfg = {
            "class": "Alpha158Ext",
            "module_path": "backtest.features.handler",
            "kwargs": {**handler_kwargs, "feature_groups": list(feature_groups)},
        }
    else:
        handler_cfg = {
            "class": "Alpha158",
            "module_path": "qlib.contrib.data.handler",
            "kwargs": handler_kwargs,
        }

    model_kwargs = dict(LGB_BASE_KWARGS)
    model_kwargs["seed"] = int(seed)
    # 同步固定 bagging / feature 抽样种子，保证可复现
    model_kwargs["bagging_seed"] = int(seed)
    model_kwargs["feature_fraction_seed"] = int(seed)

    return {
        "model": {
            "class": "LGBModel",
            "module_path": "qlib.contrib.model.gbdt",
            "kwargs": model_kwargs,
        },
        "dataset": {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {
                "handler": handler_cfg,
                "segments": SEGMENTS,
            },
        },
    }


def extract_port_metrics(analysis_df: pd.DataFrame, report_normal_df: pd.DataFrame) -> dict:
    """从 PortAnaRecord 产物提取组合指标（与 run_backtest.py 对齐）。"""
    metrics = {}

    try:
        section = analysis_df.loc["1day"]["excess_return_with_cost"]
        for key in ["mean", "std", "annualized_return", "information_ratio", "max_drawdown"]:
            if key in section.index:
                metrics[f"excess_with_cost_{key}"] = float(section.loc[key, "risk"])
    except Exception:  # noqa: BLE001
        pass

    try:
        section = analysis_df.loc["1day"]["excess_return_without_cost"]
        for key in ["mean", "std", "annualized_return", "information_ratio", "max_drawdown"]:
            if key in section.index:
                metrics[f"excess_no_cost_{key}"] = float(section.loc[key, "risk"])
    except Exception:  # noqa: BLE001
        pass

    try:
        account = report_normal_df["account"].dropna()
        metrics["portfolio_cum_return"] = float(account.iloc[-1] / account.iloc[0] - 1)
        bench = report_normal_df["bench"].dropna()
        metrics["benchmark_cum_return"] = float((1 + bench).prod() - 1)
        metrics["excess_cum_return"] = metrics["portfolio_cum_return"] - metrics["benchmark_cum_return"]
    except Exception:  # noqa: BLE001
        pass

    return metrics


def extract_sig_metrics(recorder) -> dict:
    """从 recorder 已记录的 metrics 中提取 IC 系列。"""
    mapping = {
        "IC": "IC",
        "ICIR": "ICIR",
        "Rank IC": "Rank_IC",
        "Rank ICIR": "Rank_ICIR",
    }
    out = {}
    try:
        # MLflow / qlib recorder 接口差异兜底
        if hasattr(recorder, "list_metrics"):
            logged = recorder.list_metrics()
        else:
            logged = {}
        for src, dst in mapping.items():
            if src in logged:
                out[dst] = float(logged[src])
    except Exception:  # noqa: BLE001
        pass

    # 若 list_metrics 不可用，直接从 ic/ric pickle 计算
    if "IC" not in out:
        try:
            ic = recorder.load_object("sig_analysis/ic.pkl")
            ric = recorder.load_object("sig_analysis/ric.pkl")
            out["IC"] = float(ic.mean())
            out["ICIR"] = float(ic.mean() / ic.std()) if ic.std() != 0 else float("nan")
            out["Rank_IC"] = float(ric.mean())
            out["Rank_ICIR"] = float(ric.mean() / ric.std()) if ric.std() != 0 else float("nan")
        except Exception:  # noqa: BLE001
            pass
    return out


def _is_new_feature(name: str, feature_groups: Sequence[str]) -> bool:
    prefixes = []
    for g in feature_groups:
        prefixes.extend(GROUP_PREFIXES.get(g, ()))
    return any(str(name).startswith(p) for p in prefixes)


def export_feature_importance(
    model,
    dataset,
    feature_groups: Sequence[str],
    out_path: Path,
) -> dict:
    """导出 LGB gain importance，并统计新特征占比 / Top50 命中数。"""
    from qlib.data.dataset.handler import DataHandlerLP

    # LGBModel 用 x.values 训练，feature_name 多为 Column_i，需映射回真实列名
    feat_df = dataset.prepare("train", col_set="feature", data_key=DataHandlerLP.DK_L)
    if isinstance(feat_df.columns, pd.MultiIndex):
        feature_names = list(feat_df.columns.get_level_values(-1))
    else:
        feature_names = list(feat_df.columns)

    booster = model.model
    gain = booster.feature_importance(importance_type="gain")
    if len(gain) != len(feature_names):
        # 兜底：用 booster 自带名字
        feature_names = list(booster.feature_name())

    imp = pd.Series(gain, index=feature_names, dtype=float).sort_values(ascending=False)
    imp_df = imp.rename("gain").reset_index().rename(columns={"index": "feature"})
    imp_df["rank"] = np.arange(1, len(imp_df) + 1)
    imp_df["is_new"] = imp_df["feature"].map(lambda n: _is_new_feature(n, feature_groups))
    imp_df.to_csv(out_path, index=False)

    total_gain = float(imp_df["gain"].sum())
    new_gain = float(imp_df.loc[imp_df["is_new"], "gain"].sum())
    top50 = imp_df.head(50)
    return {
        "new_feat_importance_share": (new_gain / total_gain) if total_gain > 0 else 0.0,
        "new_feat_in_top50": int(top50["is_new"].sum()),
        "n_features": int(len(imp_df)),
        "n_new_features": int(imp_df["is_new"].sum()),
    }


def run_one(exp_id: str, seed: int) -> dict:
    from qlib.utils import flatten_dict, init_instance_by_config
    from qlib.workflow import R
    from qlib.workflow.record_temp import PortAnaRecord, SigAnaRecord, SignalRecord

    feature_groups = EXP_GROUPS[exp_id]
    run_dir = RESULT_ROOT / exp_id / f"seed_{seed:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "exp": exp_id,
        "seed": seed,
        "feature_groups": ",".join(feature_groups) if feature_groups else "baseline",
        "status": "failed",
    }

    print(f"\n{'='*60}")
    print(f"  {exp_id} seed={seed} groups={feature_groups or ['baseline']}")
    print(f"  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"{'='*60}")

    try:
        task = build_task(feature_groups, seed)
        model = init_instance_by_config(task["model"])
        dataset = init_instance_by_config(task["dataset"])

        exp_train = f"feat_exp_{exp_id}_seed{seed:02d}_train"
        with R.start(experiment_name=exp_train):
            R.log_params(**flatten_dict(task))
            model.fit(dataset)
            R.save_objects(trained_model=model)
            train_rid = R.get_recorder().id

        # 特征重要性
        imp_metrics = export_feature_importance(
            model,
            dataset,
            feature_groups,
            run_dir / "feature_importance.csv",
        )
        result.update(imp_metrics)

        port_cfg = deepcopy(PORT_ANALYSIS_CONFIG)
        port_cfg["strategy"]["kwargs"]["model"] = model
        port_cfg["strategy"]["kwargs"]["dataset"] = dataset

        exp_bt = f"feat_exp_{exp_id}_seed{seed:02d}_backtest"
        with R.start(experiment_name=exp_bt):
            recorder = R.get_recorder(
                recorder_id=train_rid,
                experiment_name=exp_train,
            )
            model = recorder.load_object("trained_model")
            port_cfg["strategy"]["kwargs"]["model"] = model

            recorder = R.get_recorder()
            ba_rid = recorder.id

            sr = SignalRecord(model, dataset, recorder)
            sr.generate()

            sar = SigAnaRecord(recorder)
            sar.generate()

            par = PortAnaRecord(recorder, port_cfg, "day")
            par.generate()

        # 读取指标
        recorder = R.get_recorder(recorder_id=ba_rid, experiment_name=exp_bt)
        sig_metrics = extract_sig_metrics(recorder)
        result.update(sig_metrics)

        report_normal_df = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
        analysis_df = recorder.load_object("portfolio_analysis/port_analysis_1day.pkl")
        port_metrics = extract_port_metrics(analysis_df, report_normal_df)
        result.update(port_metrics)

        report_normal_df.to_csv(run_dir / "report_normal.csv")
        result["status"] = "success"
        result["train_recorder_id"] = train_rid
        result["backtest_recorder_id"] = ba_rid

        print(f"[{exp_id}|seed={seed}] 完成: Rank_IC={result.get('Rank_IC')} "
              f"ann_excess={result.get('excess_with_cost_annualized_return')} "
              f"new_share={result.get('new_feat_importance_share')}")

    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"[{exp_id}|seed={seed}] 出错: {e}")
        traceback.print_exc()

    with open(run_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    return result


def parse_args():
    parser = argparse.ArgumentParser(description="特征消融实验 E0~E4")
    parser.add_argument(
        "--exp",
        nargs="+",
        default=["all"],
        help="实验组：E0 E1 E2 E3 E4 或 all",
    )
    parser.add_argument("--seeds", type=int, default=3, help="每组随机种子数量（从 0 起）")
    parser.add_argument(
        "--seed-list",
        type=int,
        nargs="+",
        default=None,
        help="显式指定 seed 列表，覆盖 --seeds",
    )
    parser.add_argument("--provider-uri", default=PROVIDER_URI)
    parser.add_argument("--skip-summary", action="store_true", help="结束后不生成 comparison.csv")
    return parser.parse_args()


def main():
    import qlib
    from qlib.constant import REG_CN
    from qlib.utils import exists_qlib_data

    args = parse_args()

    if not exists_qlib_data(args.provider_uri):
        raise RuntimeError(
            f"Qlib 数据未找到: {args.provider_uri}\n"
            "请先准备 cn_data，或仅运行 validate_features.py --parse-only 做表达式校验。"
        )

    # 独立 mlruns，避免污染默认目录
    mlruns_dir = RESULT_ROOT / "mlruns"
    mlruns_dir.mkdir(parents=True, exist_ok=True)
    qlib.init(
        provider_uri=args.provider_uri,
        region=REG_CN,
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {
                "uri": str(mlruns_dir),
                "default_exp_name": "feature_experiment",
            },
        },
    )

    if "all" in args.exp:
        exp_ids = list(EXP_GROUPS.keys())
    else:
        exp_ids = args.exp
        unknown = set(exp_ids) - set(EXP_GROUPS)
        if unknown:
            raise ValueError(f"未知实验组: {unknown}, 可选: {list(EXP_GROUPS)}")

    seeds = args.seed_list if args.seed_list is not None else list(range(args.seeds))

    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    all_results = []
    start = datetime.now()

    for exp_id in exp_ids:
        for seed in seeds:
            all_results.append(run_one(exp_id, seed))
            # 增量落盘，防中断
            pd.DataFrame(all_results).to_csv(RESULT_ROOT / "all_runs.csv", index=False)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n全部实验结束，耗时 {elapsed:.1f}s，成功 "
          f"{sum(1 for r in all_results if r.get('status') == 'success')}/{len(all_results)}")

    if not args.skip_summary:
        out = write_comparison(RESULT_ROOT, exp_ids=exp_ids)
        print(f"对比表: {out}")


if __name__ == "__main__":
    main()
