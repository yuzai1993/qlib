"""
20次训练+回测实验脚本
基于 examples/workflow_by_code_230915.ipynb 的配置
结果保存至 backtest/result/ 目录
"""

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# 确保能找到 qlib 包
QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))

import qlib
from qlib.constant import REG_CN
from qlib.utils import exists_qlib_data, init_instance_by_config, flatten_dict
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, PortAnaRecord

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
N_RUNS = 1
PROVIDER_URI = "~/.qlib/qlib_data/cn_data"
MARKET = "csi300"
BENCHMARK = "SH000300"

RESULT_DIR = Path(__file__).resolve().parents[1] / "result"
RESULT_DIR.mkdir(parents=True, exist_ok=True)

RUNS_RESULT_FILE = RESULT_DIR / "all_runs_results.csv"
SUMMARY_FILE = RESULT_DIR / "summary.json"

# ─────────────────────────────────────────────
# 数据与任务配置（与 notebook 保持一致）
# ─────────────────────────────────────────────
DATA_HANDLER_CONFIG = {
    "start_time": "2003-01-02",
    "end_time": "2026-03-10",
    "fit_start_time": "2003-01-02",
    "fit_end_time": "2020-01-10",
    "instruments": MARKET,
}

TASK = {
    "model": {
        "class": "LGBModel",
        "module_path": "qlib.contrib.model.gbdt",
        "kwargs": {
            "loss": "mse",
            "colsample_bytree": 0.8879,
            "learning_rate": 0.2,
            "subsample": 0.8789,
            "lambda_l1": 205.6999,
            "lambda_l2": 580.9768,
            "max_depth": 8,
            "num_leaves": 210,
            "num_threads": 20,
        },
    },
    "dataset": {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": {
                "class": "Alpha158",
                "module_path": "qlib.contrib.data.handler",
                "kwargs": DATA_HANDLER_CONFIG,
            },
            "segments": {
                "train": ("2003-01-02", "2020-01-10"),
                "valid": ("2020-01-13", "2023-09-15"),
                "test": ("2023-09-18", "2026-03-10"),
            },
        },
    },
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
            # model/dataset 在运行时注入
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


# ─────────────────────────────────────────────
# 工具函数：从 analysis_df 提取关键指标
# ─────────────────────────────────────────────
def extract_metrics(analysis_df: pd.DataFrame, report_normal_df: pd.DataFrame) -> dict:
    """
    从 port_analysis 和 report_normal 中提取常用回测指标。
    analysis_df 的层级索引结构: (freq, metric_group, metric)

    注意：report_normal_df["return"] 是持仓视角收益率（分子加回了当日手续费），
    累乘后会高估真实收益。portfolio_cum_return 应直接用 account 首尾值计算。
    benchmark_cum_return 同理用 bench 列累乘（bench 是基准日收益率，可直接累乘）。
    """
    metrics = {}

    try:
        # excess_return_with_cost 对应扣费后超额收益
        section = analysis_df.loc["1day"]["excess_return_with_cost"]
        for key in ["mean", "std", "annualized_return", "information_ratio", "max_drawdown"]:
            if key in section.index:
                metrics[f"excess_with_cost_{key}"] = float(section.loc[key, "risk"])
    except Exception:
        pass

    try:
        section = analysis_df.loc["1day"]["excess_return_without_cost"]
        for key in ["mean", "std", "annualized_return", "information_ratio", "max_drawdown"]:
            if key in section.index:
                metrics[f"excess_no_cost_{key}"] = float(section.loc[key, "risk"])
    except Exception:
        pass

    try:
        # portfolio_cum_return: 用 account 首尾值计算，避免 return 列高估问题
        # account 列是每日账户总价值（持仓市值 + 现金），首尾相除即为真实累计收益率
        account = report_normal_df["account"].dropna()
        metrics["portfolio_cum_return"] = float(account.iloc[-1] / account.iloc[0] - 1)

        # benchmark_cum_return: bench 列是基准日收益率，可以直接累乘
        bench = report_normal_df["bench"].dropna()
        metrics["benchmark_cum_return"] = float((1 + bench).prod() - 1)

        metrics["excess_cum_return"] = metrics["portfolio_cum_return"] - metrics["benchmark_cum_return"]
    except Exception:
        pass

    return metrics


# ─────────────────────────────────────────────
# 单次训练+回测
# ─────────────────────────────────────────────
def run_single(run_idx: int) -> dict:
    print(f"\n{'='*60}")
    print(f"  Run {run_idx}/{N_RUNS}  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"{'='*60}")

    result = {"run": run_idx, "status": "failed"}

    try:
        # ---------- 训练 ----------
        model = init_instance_by_config(TASK["model"])
        dataset = init_instance_by_config(TASK["dataset"])

        with R.start(experiment_name=f"train_model_run{run_idx:02d}"):
            R.log_params(**flatten_dict(TASK))
            model.fit(dataset)
            R.save_objects(trained_model=model)
            train_rid = R.get_recorder().id

        print(f"[Run {run_idx}] 训练完成，recorder_id={train_rid}")

        # ---------- 回测 ----------
        # 将 model/dataset 注入 strategy kwargs
        port_cfg = json.loads(json.dumps(PORT_ANALYSIS_CONFIG))  # deep copy
        port_cfg["strategy"]["kwargs"]["model"] = model
        port_cfg["strategy"]["kwargs"]["dataset"] = dataset

        with R.start(experiment_name=f"backtest_analysis_run{run_idx:02d}"):
            # 重新加载已保存的模型（与 notebook 流程一致）
            recorder = R.get_recorder(
                recorder_id=train_rid,
                experiment_name=f"train_model_run{run_idx:02d}",
            )
            model = recorder.load_object("trained_model")
            port_cfg["strategy"]["kwargs"]["model"] = model

            recorder = R.get_recorder()
            ba_rid = recorder.id

            sr = SignalRecord(model, dataset, recorder)
            sr.generate()

            par = PortAnaRecord(recorder, port_cfg, "day")
            par.generate()

        print(f"[Run {run_idx}] 回测完成，recorder_id={ba_rid}")

        # ---------- 读取结果 ----------
        recorder = R.get_recorder(
            recorder_id=ba_rid,
            experiment_name=f"backtest_analysis_run{run_idx:02d}",
        )
        report_normal_df = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
        analysis_df = recorder.load_object("portfolio_analysis/port_analysis_1day.pkl")

        metrics = extract_metrics(analysis_df, report_normal_df)
        result.update(metrics)
        result["status"] = "success"
        result["train_recorder_id"] = train_rid
        result["backtest_recorder_id"] = ba_rid

        # 将本次 report_normal_df 单独保存
        run_report_path = RESULT_DIR / f"run_{run_idx:02d}_report_normal.csv"
        report_normal_df.to_csv(run_report_path)
        print(f"[Run {run_idx}] 单次报告已保存至 {run_report_path}")
        print(f"[Run {run_idx}] 主要指标: {metrics}")

    except Exception as e:
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"[Run {run_idx}] 出错: {e}")
        traceback.print_exc()

    return result


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
def main():
    # 初始化 qlib
    if not exists_qlib_data(PROVIDER_URI):
        raise RuntimeError(
            f"Qlib 数据未找到: {PROVIDER_URI}\n"
            "请先执行: python scripts/get_data.py qlib_data_cn --target_dir ~/.qlib/qlib_data/cn_data"
        )
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)

    all_results = []
    start_time = datetime.now()

    for i in range(1, N_RUNS + 1):
        result = run_single(i)
        all_results.append(result)

        # 每次运行后立即保存（防止中途中断丢数据）
        df = pd.DataFrame(all_results)
        df.to_csv(RUNS_RESULT_FILE, index=False)
        print(f"[Run {i}] 结果已追加写入 {RUNS_RESULT_FILE}")

    # ── 计算汇总统计 ──
    df = pd.DataFrame(all_results)
    success_df = df[df["status"] == "success"]

    metric_cols = [c for c in df.columns if c not in ("run", "status", "error", "traceback", "train_recorder_id", "backtest_recorder_id")]

    summary = {
        "total_runs": N_RUNS,
        "success_runs": int(len(success_df)),
        "failed_runs": int(N_RUNS - len(success_df)),
        "elapsed_seconds": (datetime.now() - start_time).total_seconds(),
        "metrics_mean": {},
        "metrics_std": {},
        "metrics_per_run": df[["run", "status"] + metric_cols].to_dict(orient="records"),
    }

    for col in metric_cols:
        vals = pd.to_numeric(success_df[col], errors="coerce").dropna()
        if not vals.empty:
            summary["metrics_mean"][col] = float(vals.mean())
            summary["metrics_std"][col] = float(vals.std())

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print("\n" + "=" * 60)
    print("  全部实验完成")
    print("=" * 60)
    print(f"  成功: {summary['success_runs']} / {N_RUNS}")
    print(f"  总耗时: {summary['elapsed_seconds']:.1f} 秒")
    print(f"\n  关键指标均值:")
    for k, v in summary["metrics_mean"].items():
        std = summary["metrics_std"].get(k, float("nan"))
        print(f"    {k}: {v:.6f}  ±  {std:.6f}")
    print(f"\n  详细结果: {RUNS_RESULT_FILE}")
    print(f"  汇总统计: {SUMMARY_FILE}")


if __name__ == "__main__":
    main()
