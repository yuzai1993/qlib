"""对外部 pred.pkl 执行零训练组合回测。"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qlib
from qlib.constant import REG_CN
from qlib.utils import exists_qlib_data
from qlib.workflow import R
from qlib.workflow.record_temp import PortAnaRecord

from config_loader import (
    ConfigError,
    RESULT_ROOT,
    build_port_analysis_config,
    load_config,
    normalize_exchange_kwargs,
)
from report_utils import make_session_dir, write_json
from run_backtest import _finalize_session, _save_run_report, extract_metrics


class ExternalPredPortAnaRecord(PortAnaRecord):
    """跳过 SignalRecord 依赖，并在内存中提供外部预测。"""

    depend_cls = None

    def __init__(self, recorder, config: dict, pred: pd.DataFrame, *args, **kwargs):
        super().__init__(recorder, config, *args, **kwargs)
        self._external_pred = pred

    def load(self, name: str, parents: bool = True):
        if name == "pred.pkl":
            return self._external_pred
        return super().load(name, parents=parents)


def _load_pred(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"预测文件不存在: {path}")
    pred = pd.read_pickle(path)
    if isinstance(pred, pd.Series):
        pred = pred.to_frame("score")
    if not isinstance(pred, pd.DataFrame) or pred.shape[1] != 1:
        raise ValueError("预测文件必须是 Series 或单列 DataFrame")
    if not isinstance(pred.index, pd.MultiIndex) or set(("datetime", "instrument")) - set(pred.index.names):
        raise ValueError("预测索引必须是带 datetime、instrument 的 MultiIndex")
    return pred.rename(columns={pred.columns[0]: "score"}).sort_index()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="直接对外部预测执行 TopkDropout 回测，不训练模型")
    parser.add_argument("--pred", required=True, type=Path, help="外部 pred.pkl（MultiIndex datetime/instrument）")
    parser.add_argument(
        "--config",
        default="csi500_ensemble_cum_h10.yaml",
        help="YAML 路径或 backtest/configs/ 下文件名",
    )
    parser.add_argument("--note", default=None, help="覆盖结果目录说明")
    parser.add_argument(
        "--pred-copy-name",
        default="external_pred.pkl",
        help="保存至结果目录的预测文件名",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if cfg["run"]["mode"] != "pred_backtest":
        print("配置错误: run_pred_backtest.py 要求 run.mode=pred_backtest", file=sys.stderr)
        raise SystemExit(2)

    provider_uri = cfg["data"]["provider_uri"]
    if not exists_qlib_data(provider_uri):
        raise RuntimeError(f"Qlib 数据未找到: {provider_uri}")
    qlib.init(provider_uri=provider_uri, region=REG_CN if cfg["data"].get("region", "cn") == "cn" else cfg["data"]["region"])

    pred = _load_pred(args.pred)
    note = args.note if args.note is not None else cfg["run"].get("note", "")
    session_dir = make_session_dir(RESULT_ROOT, note=note)
    session_name = session_dir.name
    pred_copy = session_dir / args.pred_copy_name
    pred.to_pickle(pred_copy)

    port_cfg = json.loads(json.dumps(build_port_analysis_config(cfg)))
    port_cfg["backtest"]["exchange_kwargs"] = normalize_exchange_kwargs(
        port_cfg["backtest"].get("exchange_kwargs")
    )
    port_cfg["strategy"]["kwargs"]["signal"] = pred
    run_dir = session_dir / "run_01"
    run_dir.mkdir()
    result: dict = {"run": 1, "status": "failed"}
    experiment_name = f"backtest_{session_name}_run01"
    start_time = datetime.now()

    meta = {
        "session_name": session_name,
        "note": note,
        "mode": "pred_backtest",
        "created_at": start_time.isoformat(timespec="seconds"),
        "config_path": cfg["_config_path"],
        "source_pred": str(args.pred.resolve()),
        "saved_pred": str(pred_copy),
        "backtest": port_cfg["backtest"],
        "strategy": {
            "class": port_cfg["strategy"]["class"],
            **{
                key: value
                for key, value in port_cfg["strategy"]["kwargs"].items()
                if key != "signal"
            },
        },
    }
    write_json(session_dir / "meta.json", meta)

    try:
        with R.start(experiment_name=experiment_name):
            recorder = R.get_recorder()
            recorder_id, experiment_id = recorder.id, recorder.experiment_id
            recorder.save_objects(local_path=str(pred_copy))
            ExternalPredPortAnaRecord(recorder, port_cfg, pred, "day").generate()

        recorder = R.get_recorder(recorder_id=recorder_id, experiment_name=experiment_name)
        report_normal = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
        analysis = recorder.load_object("portfolio_analysis/port_analysis_1day.pkl")
        result.update(extract_metrics(analysis, report_normal))
        result.update(
            status="success",
            backtest_recorder_id=recorder_id,
            backtest_experiment_id=experiment_id,
            backtest_experiment_name=experiment_name,
        )
        link = {
            "backtest_experiment_name": experiment_name,
            "backtest_experiment_id": experiment_id,
            "backtest_recorder_id": recorder_id,
            "backtest_artifacts": f"mlruns/{experiment_id}/{recorder_id}",
            "source_pred": str(args.pred.resolve()),
        }
        _save_run_report(
            run_dir=run_dir,
            session_name=session_name,
            run_idx=1,
            note=note,
            result=result,
            mlruns_link=link,
            report_normal_df=report_normal,
            analysis_df=analysis,
            pred_label=None,
            generate_figures=bool(cfg["run"].get("generate_figures", False)),
        )
    except Exception as exc:
        result.update(error=str(exc), traceback=traceback.format_exc())
        write_json(run_dir / "metrics.json", {k: v for k, v in result.items() if k != "traceback"})
        traceback.print_exc()

    meta["runs"] = [
        {
            "run": 1,
            "status": result["status"],
            "backtest_recorder_id": result.get("backtest_recorder_id"),
        }
    ]
    write_json(session_dir / "meta.json", meta)
    summary = _finalize_session(session_dir, session_name, note, 1, [result], start_time)
    print(f"结果目录: {session_dir}")
    print(f"成功: {summary['success_runs']} / 1")
    if result["status"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
