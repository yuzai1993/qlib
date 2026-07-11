"""
训练+回测实验脚本
基于 examples/workflow_by_code_230915.ipynb 的配置

用法:
  python backtest/scripts/run_backtest.py
  python backtest/scripts/run_backtest.py --note "157维+ProcessInf"
  python backtest/scripts/run_backtest.py --note "baseline" --n-runs 3

结果写入 backtest/result/YYYYMMDD_HHMMSS[_note]/，含 HTML 报告与 PNG 图，
并在 meta / mlruns_link 中记录与根目录 mlruns/ 的对应关系。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

# 确保能找到 qlib 包与同目录 report_utils
QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qlib
from qlib.constant import REG_CN
from qlib.utils import exists_qlib_data, init_instance_by_config, flatten_dict
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, PortAnaRecord

from report_utils import (
    build_pred_label,
    generate_run_figures,
    make_session_dir,
    write_index_html,
    write_json,
    write_run_html,
)

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
PROVIDER_URI = "~/.qlib/qlib_data/cn_data"
MARKET = "csi300"
BENCHMARK = "SH000300"

RESULT_ROOT = Path(__file__).resolve().parents[1] / "result"
RESULT_ROOT.mkdir(parents=True, exist_ok=True)

DATA_HANDLER_CONFIG = {
    "start_time": "2003-01-02",
    "end_time": "2026-03-10",
    "fit_start_time": "2003-01-02",
    "fit_end_time": "2020-01-10",
    "instruments": MARKET,
    # ProcessInf 处理除法产生的 inf（替换为当日截面均值）。
    # Alpha158 为 PTYPE_A，infer_processors 同时作用于训练(DK_L)与推理(DK_I)，两边口径一致。
    "infer_processors": [{"class": "ProcessInf"}],
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
                "class": "Alpha158NoVWAP",
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


def extract_metrics(analysis_df: pd.DataFrame, report_normal_df: pd.DataFrame) -> dict:
    """从 port_analysis / report_normal 提取常用回测指标。"""
    metrics = {}

    def _excess_section(key: str):
        if isinstance(analysis_df.index, pd.MultiIndex) and "1day" in analysis_df.index.get_level_values(0):
            return analysis_df.loc["1day"][key]
        return analysis_df.loc[key]

    try:
        section = _excess_section("excess_return_with_cost")
        for key in ["mean", "std", "annualized_return", "information_ratio", "max_drawdown"]:
            if key in section.index:
                metrics[f"excess_with_cost_{key}"] = float(section.loc[key, "risk"])
    except Exception:
        pass

    try:
        section = _excess_section("excess_return_without_cost")
        for key in ["mean", "std", "annualized_return", "information_ratio", "max_drawdown"]:
            if key in section.index:
                metrics[f"excess_no_cost_{key}"] = float(section.loc[key, "risk"])
    except Exception:
        pass

    try:
        account = report_normal_df["account"].dropna()
        metrics["portfolio_cum_return"] = float(account.iloc[-1] / account.iloc[0] - 1)
        bench = report_normal_df["bench"].dropna()
        metrics["benchmark_cum_return"] = float((1 + bench).prod() - 1)
        metrics["excess_cum_return"] = metrics["portfolio_cum_return"] - metrics["benchmark_cum_return"]
    except Exception:
        pass

    return metrics


def run_single(run_idx: int, n_runs: int, session_dir: Path, session_name: str, note: str) -> dict:
    print(f"\n{'='*60}")
    print(f"  Run {run_idx}/{n_runs}  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"{'='*60}")

    run_dir = session_dir / f"run_{run_idx:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = run_dir / "figures"

    # 实验名带 session，便于与结果目录对应
    train_exp = f"train_{session_name}_run{run_idx:02d}"
    backtest_exp = f"backtest_{session_name}_run{run_idx:02d}"

    result = {"run": run_idx, "status": "failed"}

    try:
        model = init_instance_by_config(TASK["model"])
        dataset = init_instance_by_config(TASK["dataset"])

        with R.start(experiment_name=train_exp):
            R.log_params(**flatten_dict(TASK))
            model.fit(dataset)
            R.save_objects(trained_model=model)
            train_rec = R.get_recorder()
            train_rid = train_rec.id
            train_eid = train_rec.experiment_id

        print(f"[Run {run_idx}] 训练完成，experiment_id={train_eid}, recorder_id={train_rid}")

        port_cfg = json.loads(json.dumps(PORT_ANALYSIS_CONFIG))
        port_cfg["strategy"]["kwargs"]["model"] = model
        port_cfg["strategy"]["kwargs"]["dataset"] = dataset

        with R.start(experiment_name=backtest_exp):
            recorder = R.get_recorder(recorder_id=train_rid, experiment_name=train_exp)
            model = recorder.load_object("trained_model")
            port_cfg["strategy"]["kwargs"]["model"] = model

            recorder = R.get_recorder()
            ba_rid = recorder.id
            ba_eid = recorder.experiment_id

            sr = SignalRecord(model, dataset, recorder)
            sr.generate()

            par = PortAnaRecord(recorder, port_cfg, "day")
            par.generate()

        print(f"[Run {run_idx}] 回测完成，experiment_id={ba_eid}, recorder_id={ba_rid}")

        recorder = R.get_recorder(recorder_id=ba_rid, experiment_name=backtest_exp)
        report_normal_df = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
        analysis_df = recorder.load_object("portfolio_analysis/port_analysis_1day.pkl")
        pred = recorder.load_object("pred.pkl")
        label = recorder.load_object("label.pkl")
        pred_label = build_pred_label(pred, label)

        metrics = extract_metrics(analysis_df, report_normal_df)
        result.update(metrics)
        result["status"] = "success"
        result["train_recorder_id"] = train_rid
        result["backtest_recorder_id"] = ba_rid
        result["train_experiment_id"] = train_eid
        result["backtest_experiment_id"] = ba_eid
        result["train_experiment_name"] = train_exp
        result["backtest_experiment_name"] = backtest_exp

        mlruns_link = {
            "train_experiment_name": train_exp,
            "train_experiment_id": train_eid,
            "train_recorder_id": train_rid,
            "train_artifacts": f"mlruns/{train_eid}/{train_rid}",
            "backtest_experiment_name": backtest_exp,
            "backtest_experiment_id": ba_eid,
            "backtest_recorder_id": ba_rid,
            "backtest_artifacts": f"mlruns/{ba_eid}/{ba_rid}",
        }
        write_json(run_dir / "mlruns_link.json", mlruns_link)
        write_json(run_dir / "metrics.json", {k: v for k, v in result.items() if k != "traceback"})

        report_csv = run_dir / "report_normal.csv"
        report_normal_df.to_csv(report_csv)

        print(f"[Run {run_idx}] 生成分析图...")
        try:
            figure_files = generate_run_figures(
                report_normal_df=report_normal_df,
                analysis_df=analysis_df,
                pred_label=pred_label,
                figures_dir=figures_dir,
            )
        except Exception as fig_err:
            print(f"[Run {run_idx}] 出图失败（继续写 HTML）: {fig_err}")
            traceback.print_exc()
            figure_files = {}

        write_json(run_dir / "figures_manifest.json", figure_files)
        write_run_html(
            run_dir / "report.html",
            title=f"{session_name} / run_{run_idx:02d}",
            metrics={k: v for k, v in result.items() if k not in ("traceback",)},
            figure_files=figure_files,
            mlruns_link=mlruns_link,
            note=note,
        )

        print(f"[Run {run_idx}] 报告已保存至 {run_dir}")
        print(f"[Run {run_idx}] 主要指标: {metrics}")

    except Exception as e:
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        write_json(run_dir / "metrics.json", {k: v for k, v in result.items() if k != "traceback"})
        print(f"[Run {run_idx}] 出错: {e}")
        traceback.print_exc()

    return result


def parse_args():
    p = argparse.ArgumentParser(description="训练+回测，结果归档到带时间戳的目录")
    p.add_argument("--note", type=str, default="", help="本次回测说明，写入目录名与 HTML")
    p.add_argument("--n-runs", type=int, default=1, help="训练+回测重复次数（默认 1）")
    return p.parse_args()


def main():
    args = parse_args()
    n_runs = max(1, int(args.n_runs))
    note = args.note or ""

    if not exists_qlib_data(PROVIDER_URI):
        raise RuntimeError(
            f"Qlib 数据未找到: {PROVIDER_URI}\n"
            "请先执行: python scripts/get_data.py qlib_data_cn --target_dir ~/.qlib/qlib_data/cn_data"
        )
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)

    session_dir = make_session_dir(RESULT_ROOT, note=note)
    session_name = session_dir.name
    print(f"结果目录: {session_dir}")

    meta = {
        "session_name": session_name,
        "note": note,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "n_runs": n_runs,
        "provider_uri": PROVIDER_URI,
        "market": MARKET,
        "benchmark": BENCHMARK,
        "handler": TASK["dataset"]["kwargs"]["handler"]["class"],
        "segments": TASK["dataset"]["kwargs"]["segments"],
        "backtest": PORT_ANALYSIS_CONFIG["backtest"],
        "strategy": {
            "class": PORT_ANALYSIS_CONFIG["strategy"]["class"],
            "topk": PORT_ANALYSIS_CONFIG["strategy"]["kwargs"]["topk"],
            "n_drop": PORT_ANALYSIS_CONFIG["strategy"]["kwargs"]["n_drop"],
        },
        "runs": [],
    }
    write_json(session_dir / "meta.json", meta)

    all_results = []
    start_time = datetime.now()

    for i in range(1, n_runs + 1):
        result = run_single(i, n_runs, session_dir, session_name, note)
        all_results.append(result)

        df = pd.DataFrame(all_results)
        df.to_csv(session_dir / "all_runs_results.csv", index=False)

        meta["runs"] = [
            {
                "run": r.get("run"),
                "status": r.get("status"),
                "train_experiment_name": r.get("train_experiment_name"),
                "train_experiment_id": r.get("train_experiment_id"),
                "train_recorder_id": r.get("train_recorder_id"),
                "backtest_experiment_name": r.get("backtest_experiment_name"),
                "backtest_experiment_id": r.get("backtest_experiment_id"),
                "backtest_recorder_id": r.get("backtest_recorder_id"),
            }
            for r in all_results
        ]
        write_json(session_dir / "meta.json", meta)
        print(f"[Run {i}] 结果已追加写入 {session_dir / 'all_runs_results.csv'}")

    df = pd.DataFrame(all_results)
    success_df = df[df["status"] == "success"]
    skip_cols = {
        "run", "status", "error", "traceback",
        "train_recorder_id", "backtest_recorder_id",
        "train_experiment_id", "backtest_experiment_id",
        "train_experiment_name", "backtest_experiment_name",
    }
    metric_cols = [c for c in df.columns if c not in skip_cols]

    summary = {
        "session_name": session_name,
        "note": note,
        "total_runs": n_runs,
        "success_runs": int(len(success_df)),
        "failed_runs": int(n_runs - len(success_df)),
        "elapsed_seconds": (datetime.now() - start_time).total_seconds(),
        "metrics_mean": {},
        "metrics_std": {},
        "metrics_per_run": df[["run", "status"] + metric_cols].to_dict(orient="records"),
    }
    for col in metric_cols:
        vals = pd.to_numeric(success_df[col], errors="coerce").dropna()
        if not vals.empty:
            summary["metrics_mean"][col] = float(vals.mean())
            summary["metrics_std"][col] = float(vals.std()) if len(vals) > 1 else 0.0

    write_json(session_dir / "summary.json", summary)

    index_runs = []
    for r in all_results:
        idx = int(r["run"])
        row = dict(r)
        if r.get("status") == "success":
            row["report_href"] = f"run_{idx:02d}/report.html"
        index_runs.append(row)

    write_index_html(
        session_dir / "index.html",
        session_name=session_name,
        note=note,
        summary=summary,
        runs=index_runs,
    )

    print("\n" + "=" * 60)
    print("  全部实验完成")
    print("=" * 60)
    print(f"  成功: {summary['success_runs']} / {n_runs}")
    print(f"  总耗时: {summary['elapsed_seconds']:.1f} 秒")
    print(f"  结果目录: {session_dir}")
    print(f"  汇总 HTML: {session_dir / 'index.html'}")
    print(f"\n  关键指标均值:")
    for k, v in summary["metrics_mean"].items():
        std = summary["metrics_std"].get(k, float("nan"))
        print(f"    {k}: {v:.6f}  ±  {std:.6f}")


if __name__ == "__main__":
    main()
