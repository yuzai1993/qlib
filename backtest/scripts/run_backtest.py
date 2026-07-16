"""
训练+回测 / 免重训回测

用法:
  python backtest/scripts/run_backtest.py
  python backtest/scripts/run_backtest.py --config csi300_lgbm_bt_only_2006_top10.yaml
  python backtest/scripts/run_backtest.py --config csi300_lgbm_bt_only.example.yaml

配置见 backtest/configs/；结果写入 backtest/result/YYYYMMDD_HHMMSS[_note]/。
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import traceback
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qlib
from qlib.constant import REG_CN
from qlib.utils import exists_qlib_data, init_instance_by_config, flatten_dict
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, PortAnaRecord

from config_loader import (
    ConfigError,
    RESULT_ROOT,
    build_port_analysis_config,
    build_task,
    load_config,
    load_session_model_info,
    normalize_exchange_kwargs,
    resolve_session_dir,
)
from report_utils import (
    build_pred_label,
    generate_run_figures,
    make_session_dir,
    write_index_html,
    write_json,
    write_run_html,
)

RESULT_ROOT.mkdir(parents=True, exist_ok=True)


def extract_metrics(analysis_df: pd.DataFrame, report_normal_df: pd.DataFrame) -> dict:
    """从 port_analysis / report_normal 提取常用回测指标。

    含：超额（相对基准）、组合绝对收益、基准绝对收益。
    跨指数对比时必须看绝对收益，因基准不同。
    """
    from qlib.contrib.evaluate import risk_analysis

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

    # 绝对收益：组合日收益 / 基准日收益（与 excess 同用 risk_analysis product 模式）
    try:
        port_ret = report_normal_df["return"].dropna()
        if len(port_ret) > 1:
            ra = risk_analysis(port_ret, freq="day")
            for key in ["mean", "std", "annualized_return", "information_ratio", "max_drawdown"]:
                metrics[f"portfolio_{key}"] = float(ra.loc[key, "risk"])
    except Exception:
        pass

    try:
        bench_ret = report_normal_df["bench"].dropna()
        if len(bench_ret) > 1:
            ra = risk_analysis(bench_ret, freq="day")
            for key in ["mean", "std", "annualized_return", "information_ratio", "max_drawdown"]:
                metrics[f"benchmark_{key}"] = float(ra.loc[key, "risk"])
    except Exception:
        pass

    return metrics


def _save_run_report(
    *,
    run_dir: Path,
    session_name: str,
    run_idx: int,
    note: str,
    result: dict,
    mlruns_link: dict,
    report_normal_df: pd.DataFrame,
    analysis_df: pd.DataFrame,
    pred_label: pd.DataFrame | None,
    generate_figures: bool = False,
) -> None:
    figures_dir = run_dir / "figures"
    write_json(run_dir / "mlruns_link.json", mlruns_link)
    write_json(run_dir / "metrics.json", {k: v for k, v in result.items() if k != "traceback"})
    report_normal_df.to_csv(run_dir / "report_normal.csv")

    figure_files: dict = {}
    if generate_figures:
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
    else:
        print(f"[Run {run_idx}] 跳过出图（run.generate_figures=false）")

    write_json(run_dir / "figures_manifest.json", figure_files)
    write_run_html(
        run_dir / "report.html",
        title=f"{session_name} / run_{run_idx:02d}",
        metrics={k: v for k, v in result.items() if k not in ("traceback",)},
        figure_files=figure_files,
        mlruns_link=mlruns_link,
        note=note,
    )


def run_train_backtest_once(
    run_idx: int,
    n_runs: int,
    session_dir: Path,
    session_name: str,
    note: str,
    task: dict,
    port_analysis_config: dict,
    generate_figures: bool = False,
) -> dict:
    print(f"\n{'='*60}")
    print(f"  Run {run_idx}/{n_runs}  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"{'='*60}")

    run_dir = session_dir / f"run_{run_idx:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    train_exp = f"train_{session_name}_run{run_idx:02d}"
    backtest_exp = f"backtest_{session_name}_run{run_idx:02d}"
    result = {"run": run_idx, "status": "failed"}

    try:
        model = init_instance_by_config(task["model"])
        dataset = init_instance_by_config(task["dataset"])

        with R.start(experiment_name=train_exp):
            R.log_params(**flatten_dict(task))
            model.fit(dataset)
            R.save_objects(trained_model=model)
            train_rec = R.get_recorder()
            train_rid = train_rec.id
            train_eid = train_rec.experiment_id

        print(f"[Run {run_idx}] 训练完成，experiment_id={train_eid}, recorder_id={train_rid}")

        port_cfg = json.loads(json.dumps(port_analysis_config))
        # json 往返会把 limit_threshold tuple 变成 list，需再规范化
        port_cfg["backtest"]["exchange_kwargs"] = normalize_exchange_kwargs(
            port_cfg["backtest"].get("exchange_kwargs")
        )
        port_cfg["strategy"]["kwargs"]["model"] = model
        port_cfg["strategy"]["kwargs"]["dataset"] = dataset

        with R.start(experiment_name=backtest_exp):
            recorder = R.get_recorder(recorder_id=train_rid, experiment_name=train_exp)
            model = recorder.load_object("trained_model")
            port_cfg["strategy"]["kwargs"]["model"] = model

            recorder = R.get_recorder()
            ba_rid = recorder.id
            ba_eid = recorder.experiment_id

            SignalRecord(model, dataset, recorder).generate()
            PortAnaRecord(recorder, port_cfg, "day").generate()

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
        _save_run_report(
            run_dir=run_dir,
            session_name=session_name,
            run_idx=run_idx,
            note=note,
            result=result,
            mlruns_link=mlruns_link,
            report_normal_df=report_normal_df,
            analysis_df=analysis_df,
            pred_label=pred_label,
            generate_figures=generate_figures,
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


def run_backtest_only_once(
    session_dir: Path,
    session_name: str,
    note: str,
    cfg: dict,
    source_info: dict,
) -> dict:
    """加载已有模型，只跑信号+回测。"""
    run_idx = 1
    print(f"\n{'='*60}")
    print(f"  Backtest-only  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"{'='*60}")

    run_dir = session_dir / f"run_{run_idx:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    handler_class = source_info.get("handler_class") or cfg["data"]["handler"]["class"]
    if not source_info.get("handler_class"):
        warnings.warn(
            f"源 session 无 handler，使用 YAML: {handler_class}",
            UserWarning,
        )

    task = build_task(cfg, handler_class=handler_class)
    port_analysis_config = build_port_analysis_config(cfg)
    backtest_exp = f"backtest_{session_name}_run{run_idx:02d}"
    result = {"run": run_idx, "status": "failed"}

    src_link = source_info["mlruns_link"]
    train_exp = src_link.get("train_experiment_name")
    train_rid = src_link.get("train_recorder_id")
    train_eid = src_link.get("train_experiment_id")

    try:
        with open(source_info["model_path"], "rb") as f:
            model = pickle.load(f)
        print(f"[Run {run_idx}] 已加载模型: {source_info['model_path']}")
        print(f"[Run {run_idx}] Handler: {handler_class}")

        dataset = init_instance_by_config(task["dataset"])

        port_cfg = json.loads(json.dumps(port_analysis_config))
        port_cfg["backtest"]["exchange_kwargs"] = normalize_exchange_kwargs(
            port_cfg["backtest"].get("exchange_kwargs")
        )
        port_cfg["strategy"]["kwargs"]["model"] = model
        port_cfg["strategy"]["kwargs"]["dataset"] = dataset

        with R.start(experiment_name=backtest_exp):
            recorder = R.get_recorder()
            ba_rid = recorder.id
            ba_eid = recorder.experiment_id
            SignalRecord(model, dataset, recorder).generate()
            PortAnaRecord(recorder, port_cfg, "day").generate()

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
            "train_artifacts": src_link.get("train_artifacts"),
            "backtest_experiment_name": backtest_exp,
            "backtest_experiment_id": ba_eid,
            "backtest_recorder_id": ba_rid,
            "backtest_artifacts": f"mlruns/{ba_eid}/{ba_rid}",
            "source_session": str(source_info["session_dir"]),
        }
        _save_run_report(
            run_dir=run_dir,
            session_name=session_name,
            run_idx=run_idx,
            note=note,
            result=result,
            mlruns_link=mlruns_link,
            report_normal_df=report_normal_df,
            analysis_df=analysis_df,
            pred_label=pred_label,
            generate_figures=bool(cfg["run"].get("generate_figures", False)),
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


def _finalize_session(
    session_dir: Path,
    session_name: str,
    note: str,
    n_runs: int,
    all_results: list,
    start_time: datetime,
) -> dict:
    df = pd.DataFrame(all_results)
    df.to_csv(session_dir / "all_runs_results.csv", index=False)

    success_df = df[df["status"] == "success"] if "status" in df.columns else df
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
        "metrics_per_run": df[["run", "status"] + metric_cols].to_dict(orient="records") if len(df) else [],
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
    return summary


def parse_args():
    p = argparse.ArgumentParser(description="训练+回测 / 免重训回测（配置见 YAML）")
    p.add_argument(
        "--config",
        type=str,
        default=None,
        help="YAML 路径或 backtest/configs/ 下文件名（默认 csi300_lgbm_bt_only_2006_top10.yaml）",
    )
    return p.parse_args()


def main():
    args = parse_args()
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(2)

    run = cfg["run"]
    mode = run["mode"]
    note = run.get("note") or ""
    n_runs = max(1, int(run.get("n_runs") or 1))
    provider_uri = cfg["data"]["provider_uri"]
    handler_class = cfg["data"]["handler"]["class"]

    if not exists_qlib_data(provider_uri):
        raise RuntimeError(
            f"Qlib 数据未找到: {provider_uri}\n"
            "请先执行: python scripts/get_data.py qlib_data_cn --target_dir ~/.qlib/qlib_data/cn_data"
        )

    region = cfg["data"].get("region", "cn")
    if region == "cn":
        qlib.init(provider_uri=provider_uri, region=REG_CN)
    else:
        qlib.init(provider_uri=provider_uri, region=region)

    session_dir = make_session_dir(RESULT_ROOT, note=note)
    session_name = session_dir.name
    print(f"结果目录: {session_dir}")
    print(f"配置: {cfg['_config_path']}")
    print(f"模式: {mode}")

    port_analysis_config = build_port_analysis_config(cfg)
    task = build_task(cfg)

    meta = {
        "session_name": session_name,
        "note": note,
        "mode": mode,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "n_runs": n_runs if mode == "train_backtest" else 1,
        "config_path": cfg["_config_path"],
        "provider_uri": provider_uri,
        "market": cfg["data"]["instruments"],
        "benchmark": cfg["data"]["benchmark"],
        "handler": handler_class,
        "segments": cfg["segments"],
        "backtest": port_analysis_config["backtest"],
        "strategy": {
            "class": port_analysis_config["strategy"]["class"],
            "topk": port_analysis_config["strategy"]["kwargs"]["topk"],
            "n_drop": port_analysis_config["strategy"]["kwargs"]["n_drop"],
        },
        "generate_figures": bool(run.get("generate_figures", False)),
        "overrides": {
            "segments_test": cfg["segments"]["test"],
        },
        "runs": [],
    }

    source_info = None
    if mode == "backtest_only":
        source_dir = resolve_session_dir(run["from_session"])
        source_info = load_session_model_info(source_dir, from_run=int(run.get("from_run") or 1))
        # meta.handler 记录实际使用的 class
        meta["handler"] = source_info.get("handler_class") or handler_class
        meta["source_session"] = str(source_dir)
        meta["source_run"] = int(run.get("from_run") or 1)
        n_runs = 1

    write_json(session_dir / "meta.json", meta)

    all_results = []
    start_time = datetime.now()

    if mode == "backtest_only":
        assert source_info is not None
        result = run_backtest_only_once(session_dir, session_name, note, cfg, source_info)
        all_results.append(result)
    else:
        for i in range(1, n_runs + 1):
            result = run_train_backtest_once(
                i,
                n_runs,
                session_dir,
                session_name,
                note,
                task,
                port_analysis_config,
                generate_figures=bool(run.get("generate_figures", False)),
            )
            all_results.append(result)
            print(f"[Run {i}] 结果已追加写入 {session_dir / 'all_runs_results.csv'}")

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

    summary = _finalize_session(session_dir, session_name, note, n_runs, all_results, start_time)

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
