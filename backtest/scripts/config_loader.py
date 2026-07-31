"""回测 YAML 加载、校验与日期对齐。"""

from __future__ import annotations

import copy
import warnings
from pathlib import Path
from typing import Any, Optional

import yaml

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = BACKTEST_ROOT / "configs"
RESULT_ROOT = BACKTEST_ROOT / "result"

VALID_MODES = ("train_only", "train_backtest", "backtest_only", "pred_backtest")
DEFAULT_CONFIG_NAME = "csi300_live_parity.yaml"


class ConfigError(ValueError):
    """配置不合法。"""


def resolve_config_path(config: Optional[str] = None) -> Path:
    """解析 --config：绝对/相对路径，或相对 backtest/configs/ 的路径/文件名。

    支持实验规范布局：`backtest/configs/<exp_id>/<name>.yaml`
    （如 `baseline/b0-m/b0_csi300_lgbm_s42.yaml`）。若只给文件名，
    则在 configs/ 下递归查找唯一匹配；多个匹配时报错要求写全路径。
    """
    if not config:
        path = CONFIGS_DIR / DEFAULT_CONFIG_NAME
    else:
        p = Path(config).expanduser()
        candidates: list[Path] = []
        if p.is_file():
            candidates.append(p.resolve())
        for cand in (
            CONFIGS_DIR / config,
            CONFIGS_DIR / f"{config}.yaml",
            CONFIGS_DIR / f"{config}.yml",
        ):
            if cand.is_file():
                candidates.append(cand.resolve())
        if not candidates:
            # 仅文件名：递归查找
            name = p.name if p.suffix else f"{p.name}.yaml"
            alt = name if name.endswith((".yaml", ".yml")) else f"{name}.yaml"
            matches = sorted(CONFIGS_DIR.rglob(alt))
            # 也试 .yml
            if not matches and not alt.endswith(".yml"):
                matches = sorted(CONFIGS_DIR.rglob(alt[:-5] + ".yml"))
            if len(matches) == 1:
                candidates.append(matches[0].resolve())
            elif len(matches) > 1:
                shown = ", ".join(str(m.relative_to(CONFIGS_DIR)) for m in matches[:5])
                raise ConfigError(
                    f"配置名 {config!r} 在 configs/ 下有多份匹配，请写相对路径："
                    f" {shown}"
                )
        if not candidates:
            raise ConfigError(f"配置文件不存在: {config}")
        # 去重后取第一个
        path = candidates[0]
    if not path.is_file():
        raise ConfigError(f"配置文件不存在: {path}")
    return path


def resolve_session_dir(from_session: str) -> Path:
    """解析 from_session：绝对路径，或相对 backtest/result/ 的目录名。"""
    if not from_session:
        raise ConfigError("backtest_only 需要 run.from_session")
    p = Path(from_session).expanduser()
    if p.is_dir():
        return p.resolve()
    cand = RESULT_ROOT / from_session
    if cand.is_dir():
        return cand.resolve()
    raise ConfigError(f"from_session 目录不存在: {from_session}")


def _require(cfg: dict, *keys: str) -> Any:
    cur: Any = cfg
    path = []
    for k in keys:
        path.append(k)
        if not isinstance(cur, dict) or k not in cur:
            raise ConfigError(f"配置缺少字段: {'.'.join(path)}")
        cur = cur[k]
    return cur


def align_dates_from_segments(cfg: dict) -> dict:
    """用 segments.test 对齐 backtest 起止；必要时延长 handler.end_time。

    只改 YAML 的 segments.test 即可；handler.start_time 保持不变（滚动特征需要测试区间之前的历史）。
    """
    cfg = copy.deepcopy(cfg)
    segments = _require(cfg, "segments")
    handler = _require(cfg, "data", "handler")

    test = list(segments.get("test") or [])
    if len(test) != 2:
        raise ConfigError("segments.test 必须是 [start, end]")
    if not test[0] or not test[1]:
        raise ConfigError("segments.test 起止不能为空")
    if str(test[0]) > str(test[1]):
        raise ConfigError(f"测试区间非法: {test[0]} > {test[1]}")

    backtest = cfg.get("backtest")
    if backtest is not None:
        backtest["start_time"] = test[0]
        backtest["end_time"] = test[1]

    h_end = handler.get("end_time")
    if h_end is None or str(test[1]) > str(h_end):
        handler["end_time"] = test[1]

    return cfg


def validate_run_section(cfg: dict) -> dict:
    run = cfg.setdefault("run", {})
    mode = run.get("mode") or "train_only"
    if mode not in VALID_MODES:
        raise ConfigError(f"run.mode 非法: {mode}，应为 {VALID_MODES}")
    run["mode"] = mode
    run.setdefault("note", "")
    run.setdefault("n_runs", 1)
    run.setdefault("from_run", 1)
    run.setdefault("from_session", None)
    run["generate_figures"] = bool(run.get("generate_figures", False))
    # 兼容旧 YAML：忽略已废弃的 test_start/test_end
    run.pop("test_start", None)
    run.pop("test_end", None)

    if mode == "backtest_only":
        tracked_path = (cfg.get("parity") or {}).get("model_path")
        if not run.get("from_session") and not tracked_path:
            raise ConfigError(
                "backtest_only 需要 model source: run.from_session "
                "或 parity.model_path"
            )
        n_runs = int(run.get("n_runs") or 1)
        if n_runs > 1:
            warnings.warn(f"backtest_only 忽略 n_runs={n_runs}，强制为 1", UserWarning)
        run["n_runs"] = 1

    # 必填块
    _require(cfg, "data", "provider_uri")
    _require(cfg, "data", "instruments")
    _require(cfg, "data", "benchmark")
    _require(cfg, "data", "handler", "class")
    _require(cfg, "data", "handler", "module_path")
    _require(cfg, "segments", "train")
    _require(cfg, "segments", "valid")
    _require(cfg, "segments", "test")
    _require(cfg, "model", "class")
    if mode != "train_only":
        _require(cfg, "strategy", "class")
        _require(cfg, "backtest", "account")
        _require(cfg, "backtest", "exchange_kwargs")
    return cfg


def load_config(config: Optional[str] = None) -> dict:
    """加载 YAML → 校验 → 按 segments.test 对齐日期。返回配置，并附 `_config_path`。"""
    path = resolve_config_path(config)
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ConfigError(f"配置必须是 mapping: {path}")
    cfg = validate_run_section(raw)
    cfg = align_dates_from_segments(cfg)
    cfg["_config_path"] = str(path)
    return cfg


def build_handler_kwargs(cfg: dict, handler_class: Optional[str] = None) -> dict:
    """组装 DatasetH 的 handler 配置。"""
    h = copy.deepcopy(cfg["data"]["handler"])
    cls = handler_class or h["class"]
    module_path = h.pop("module_path", "qlib.contrib.data.handler")
    h.pop("class", None)
    # instruments 可在 handler 外的 data 段
    if "instruments" not in h:
        h["instruments"] = cfg["data"]["instruments"]
    return {
        "class": cls,
        "module_path": module_path,
        "kwargs": h,
    }


def build_task(cfg: dict, handler_class: Optional[str] = None) -> dict:
    """组装 qlib TASK（model + dataset）。"""
    handler_cfg = build_handler_kwargs(cfg, handler_class=handler_class)
    segments = {
        k: tuple(v) if isinstance(v, (list, tuple)) else v
        for k, v in cfg["segments"].items()
    }
    dataset_cfg = copy.deepcopy(
        cfg.get("dataset")
        or {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {},
        }
    )
    dataset_cfg.setdefault("class", "DatasetH")
    dataset_cfg.setdefault("module_path", "qlib.data.dataset")
    dataset_kwargs = dataset_cfg.setdefault("kwargs", {})
    # Handler and fixed segments always come from the validated experiment config.
    dataset_kwargs["handler"] = handler_cfg
    dataset_kwargs["segments"] = segments
    return {
        "model": copy.deepcopy(cfg["model"]),
        "dataset": dataset_cfg,
    }


def normalize_exchange_kwargs(exchange_kwargs: Optional[dict]) -> dict:
    """规范化 exchange_kwargs：表达式限价必须是 tuple（JSON 往返后会变成 list）。"""
    if not exchange_kwargs:
        return {}
    out = copy.deepcopy(exchange_kwargs)
    lt = out.get("limit_threshold")
    if isinstance(lt, list) and len(lt) == 2:
        out["limit_threshold"] = tuple(lt)
    return out


def build_port_analysis_config(cfg: dict) -> dict:
    """组装 PortAnaRecord 配置（不含 model/dataset 运行时对象）。"""
    strategy = cfg["strategy"]
    backtest = copy.deepcopy(cfg["backtest"])
    if "benchmark" not in backtest:
        backtest["benchmark"] = cfg["data"]["benchmark"]
    if "exchange_kwargs" in backtest:
        backtest["exchange_kwargs"] = normalize_exchange_kwargs(backtest["exchange_kwargs"])

    cls = strategy["class"]
    kwargs: dict[str, Any] = dict(strategy.get("kwargs") or {})
    # SoftTopk 无 n_drop；TopkDropout* 透传 n_drop / hold_thresh 等
    if "topk" in strategy and "topk" not in kwargs:
        kwargs["topk"] = strategy["topk"]
    is_soft = "SoftTopk" in cls
    is_topk_dropout = cls.startswith("TopkDropout") or (
        "TopkDropout" in cls and not is_soft
    )
    if is_topk_dropout and "n_drop" in strategy and "n_drop" not in kwargs:
        kwargs["n_drop"] = strategy["n_drop"]
    if is_topk_dropout and "hold_thresh" in strategy and "hold_thresh" not in kwargs:
        kwargs["hold_thresh"] = strategy["hold_thresh"]
    # 兼容旧 YAML：n_drop 写在 strategy 顶层时，非 SoftTopk 仍透传
    if not is_soft and "n_drop" in strategy and "n_drop" not in kwargs:
        kwargs["n_drop"] = strategy["n_drop"]

    return {
        "executor": {
            "class": "SimulatorExecutor",
            "module_path": "qlib.backtest.executor",
            "kwargs": {
                "time_per_step": "day",
                "generate_portfolio_metrics": True,
            },
        },
        "strategy": {
            "class": cls,
            "module_path": strategy.get("module_path", "qlib.contrib.strategy.signal_strategy"),
            "kwargs": kwargs,
        },
        "backtest": backtest,
    }


def load_session_model_info(session_dir: Path, from_run: int = 1) -> dict:
    """从结果 session 读取 meta + mlruns_link，返回加载模型所需信息。"""
    import json

    session_dir = Path(session_dir)
    meta_path = session_dir / "meta.json"
    run_dir = session_dir / f"run_{int(from_run):02d}"
    link_path = run_dir / "mlruns_link.json"
    if not meta_path.is_file():
        raise ConfigError(f"缺少 meta.json: {meta_path}")
    if not run_dir.is_dir():
        raise ConfigError(f"from_run 目录不存在: {run_dir}")
    if not link_path.is_file():
        raise ConfigError(f"缺少 mlruns_link.json: {link_path}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    link = json.loads(link_path.read_text(encoding="utf-8"))
    artifacts_rel = link.get("train_artifacts")
    if not artifacts_rel:
        raise ConfigError(f"mlruns_link 缺少 train_artifacts: {link_path}")

    # train_artifacts 指向 recorder 根目录；模型在 artifacts/trained_model
    qlib_root = BACKTEST_ROOT.parent
    artifacts_dir = (qlib_root / artifacts_rel).resolve()
    model_path = artifacts_dir / "artifacts" / "trained_model"
    if not model_path.is_file():
        # 兼容若路径已含 artifacts
        alt = artifacts_dir / "trained_model"
        if alt.is_file():
            model_path = alt
        else:
            raise ConfigError(f"trained_model 不存在: {model_path}")

    handler = meta.get("handler")
    if not handler:
        warnings.warn(
            f"源 session meta 缺少 handler，将回退用当前 YAML 的 data.handler.class ({session_dir.name})",
            UserWarning,
        )

    return {
        "source_kind": "session",
        "meta": meta,
        "mlruns_link": link,
        "handler_class": handler,
        "model_path": model_path,
        "model_sha256": None,
        "session_dir": session_dir,
        "run_dir": run_dir,
    }


def resolve_backtest_model_source(
    cfg: dict,
    *,
    project_root: Optional[Path] = None,
) -> dict:
    """Resolve a tracked parity model first, with legacy session fallback."""
    parity = cfg.get("parity") or {}
    tracked_path = parity.get("model_path")
    if tracked_path:
        expected_sha256 = parity.get("model_sha256")
        if not expected_sha256:
            raise ConfigError(
                "parity.model_sha256 is required with parity.model_path"
            )
        root = (project_root or BACKTEST_ROOT.parent).resolve()
        candidate = (root / tracked_path).resolve()
        if not candidate.is_relative_to(root):
            raise ConfigError(
                f"parity.model_path must stay inside project root: {tracked_path}"
            )
        if not candidate.is_file():
            raise ConfigError(f"tracked model does not exist: {candidate}")
        return {
            "source_kind": "tracked",
            "handler_class": cfg["data"]["handler"]["class"],
            "model_path": candidate,
            "model_sha256": expected_sha256,
            "train_experiment_name": parity.get("model_experiment_name"),
            "train_experiment_id": parity.get("model_experiment_id"),
            "train_recorder_id": parity.get("model_recorder_id"),
            "train_artifacts": tracked_path,
            "session_dir": None,
        }

    run = cfg["run"]
    source_dir = resolve_session_dir(run.get("from_session"))
    return load_session_model_info(
        source_dir,
        from_run=int(run.get("from_run") or 1),
    )
