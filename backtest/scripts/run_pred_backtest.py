"""对外部 pred.pkl 执行零训练组合回测。"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.utils import exists_qlib_data
from qlib.workflow import R
from qlib.workflow.record_temp import PortAnaRecord

from config_loader import (
    ConfigError,
    RESULT_ROOT,
    build_port_analysis_config,
    load_config,
    normalize_exchange_kwargs,
    resolve_benchmark_series,
)
from report_utils import build_pred_label, make_session_dir, write_json
from run_backtest import _finalize_session, _save_run_report, extract_metrics
from phase_s_protocol import sha256_file
from universe_filter import filter_pred, parse_universe_filter


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


def load_pred_source(path: Path) -> tuple[Path, pd.DataFrame]:
    resolved = Path(path).expanduser().resolve()
    return resolved, _load_pred(resolved)


def load_configured_pred_label(
    cfg: dict,
    pred: pd.DataFrame,
    *,
    instrument_resolver=None,
    feature_loader=None,
) -> pd.DataFrame:
    """Fetch the configured raw label only, without loading features or a model."""
    label_config = (cfg.get("data", {}).get("handler", {}).get("label"))
    if (
        not isinstance(label_config, (list, tuple))
        or len(label_config) != 2
        or not label_config[0]
        or len(label_config[0]) != len(label_config[1])
    ):
        raise ValueError("configured handler label must contain fields and names")
    fields = list(label_config[0])
    names = list(label_config[1])
    dates = pred.index.get_level_values("datetime")
    instrument_resolver = instrument_resolver or D.instruments
    feature_loader = feature_loader or D.features
    label = feature_loader(
        instrument_resolver(cfg["data"]["instruments"]),
        fields,
        start_time=dates.min(),
        end_time=dates.max(),
        freq="day",
    )
    if label is None or label.empty:
        raise ValueError("configured dataset did not produce a test label")
    label = label.copy()
    label.columns = names
    if isinstance(label.index, pd.MultiIndex) and set(label.index.names) == set(
        pred.index.names
    ):
        label = label.reorder_levels(pred.index.names).sort_index()
    return build_pred_label(pred, label)


def prepare_pred_artifact(
    source_path: Path,
    pred: pd.DataFrame,
    session_dir: Path,
    *,
    copy_name: str,
    skip_copy: bool,
) -> dict:
    """Freeze the source reference and optionally persist a session-local copy."""
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"预测文件不存在: {source}")
    saved: Optional[Path] = None
    if not skip_copy:
        saved = Path(session_dir) / copy_name
        pred.to_pickle(saved)
    return {
        "source_pred": str(source),
        "source_pred_sha256": sha256_file(source),
        "saved_pred": str(saved) if saved is not None else None,
    }


def prepare_signal_and_port_cfg(
    cfg: dict, pred: pd.DataFrame
) -> tuple[dict, pd.DataFrame, Optional[dict]]:
    """组装 PortAna 配置：解析等权基准，并按 YAML 做宇宙过滤。"""
    port_cfg = json.loads(json.dumps(build_port_analysis_config(cfg)))
    port_cfg["backtest"]["exchange_kwargs"] = normalize_exchange_kwargs(
        port_cfg["backtest"].get("exchange_kwargs")
    )
    port_cfg = resolve_benchmark_series(port_cfg)
    out_pred: pd.DataFrame = pred
    filter_stats: Optional[dict] = None
    raw_spec = cfg.get("universe_filter")
    if raw_spec:
        spec = parse_universe_filter(raw_spec)
        filtered, stats = filter_pred(pred, spec)
        if isinstance(filtered, pd.Series):
            filtered = filtered.to_frame("score")
        out_pred = filtered
        filter_stats = stats.as_dict()
    port_cfg["strategy"]["kwargs"]["signal"] = out_pred
    return port_cfg, out_pred, filter_stats


def _jsonable_backtest(backtest: dict, fallback_bench: object) -> dict:
    out = dict(backtest)
    if isinstance(out.get("benchmark"), pd.Series):
        out["benchmark"] = fallback_bench
    return out


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
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
    parser.add_argument(
        "--skip-pred-copy",
        action="store_true",
        help="引用冻结预测而不在 result/MLflow 中重复复制",
    )
    return parser.parse_args(argv)


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

    pred_path, pred = load_pred_source(args.pred)
    note = args.note if args.note is not None else cfg["run"].get("note", "")
    session_dir = make_session_dir(RESULT_ROOT, note=note)
    session_name = session_dir.name
    pred_artifact = prepare_pred_artifact(
        pred_path,
        pred,
        session_dir,
        copy_name=args.pred_copy_name,
        skip_copy=bool(args.skip_pred_copy),
    )

    port_cfg, pred, filter_stats = prepare_signal_and_port_cfg(cfg, pred)
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
        **pred_artifact,
        "backtest": _jsonable_backtest(
            port_cfg["backtest"], cfg.get("data", {}).get("benchmark")
        ),
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
    if filter_stats:
        write_json(run_dir / "universe_filter_stats.json", filter_stats)
        print(f"[universe_filter] 统计已写入 {run_dir / 'universe_filter_stats.json'}", flush=True)

    try:
        with R.start(experiment_name=experiment_name):
            recorder = R.get_recorder()
            recorder_id, experiment_id = recorder.id, recorder.experiment_id
            if pred_artifact["saved_pred"] is not None:
                recorder.save_objects(local_path=pred_artifact["saved_pred"])
            ExternalPredPortAnaRecord(recorder, port_cfg, pred, "day").generate()

        recorder = R.get_recorder(recorder_id=recorder_id, experiment_name=experiment_name)
        report_normal = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
        analysis = recorder.load_object("portfolio_analysis/port_analysis_1day.pkl")
        pred_label = (
            load_configured_pred_label(cfg, pred)
            if bool(cfg["run"].get("generate_figures", False))
            else None
        )
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
            pred_label=pred_label,
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
