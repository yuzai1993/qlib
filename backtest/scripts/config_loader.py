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

VALID_MODES = ("train_backtest", "backtest_only")
DEFAULT_CONFIG_NAME = "csi300_lgbm.yaml"


class ConfigError(ValueError):
    """配置不合法。"""


def resolve_config_path(config: Optional[str] = None) -> Path:
    """解析 --config：绝对/相对路径，或相对 backtest/configs/ 的文件名。"""
    if not config:
        path = CONFIGS_DIR / DEFAULT_CONFIG_NAME
    else:
        p = Path(config).expanduser()
        if p.is_file():
            path = p.resolve()
        elif (CONFIGS_DIR / config).is_file():
            path = (CONFIGS_DIR / config).resolve()
        elif (CONFIGS_DIR / f"{config}.yaml").is_file():
            path = (CONFIGS_DIR / f"{config}.yaml").resolve()
        elif (CONFIGS_DIR / f"{config}.yml").is_file():
            path = (CONFIGS_DIR / f"{config}.yml").resolve()
        else:
            raise ConfigError(f"配置文件不存在: {config}")
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


def apply_test_overrides(cfg: dict) -> dict:
    """应用 run.test_start/end：覆盖 segments.test 与 backtest 起止；必要时延长 handler.end_time。

    handler.start_time 保持不变（滚动特征需要测试区间之前的历史）。
    """
    cfg = copy.deepcopy(cfg)
    run = cfg.setdefault("run", {})
    test_start = run.get("test_start")
    test_end = run.get("test_end")

    segments = _require(cfg, "segments")
    backtest = _require(cfg, "backtest")
    handler = _require(cfg, "data", "handler")

    # 基准区间：已有 segments.test，若缺则用 backtest
    test = list(segments.get("test") or [backtest.get("start_time"), backtest.get("end_time")])
    if len(test) != 2:
        raise ConfigError("segments.test 必须是 [start, end]")

    if test_start:
        test[0] = test_start
    if test_end:
        test[1] = test_end

    if not test[0] or not test[1]:
        raise ConfigError("测试区间起止不能为空")
    if str(test[0]) > str(test[1]):
        raise ConfigError(f"测试区间非法: {test[0]} > {test[1]}")

    segments["test"] = test
    backtest["start_time"] = test[0]
    backtest["end_time"] = test[1]

    # 延长 handler.end_time，不收窄 start_time
    h_end = handler.get("end_time")
    if h_end is None or str(test[1]) > str(h_end):
        handler["end_time"] = test[1]

    return cfg


def validate_run_section(cfg: dict) -> dict:
    run = cfg.setdefault("run", {})
    mode = run.get("mode") or "train_backtest"
    if mode not in VALID_MODES:
        raise ConfigError(f"run.mode 非法: {mode}，应为 {VALID_MODES}")
    run["mode"] = mode
    run.setdefault("note", "")
    run.setdefault("n_runs", 1)
    run.setdefault("from_run", 1)
    run.setdefault("from_session", None)
    run.setdefault("test_start", None)
    run.setdefault("test_end", None)

    if mode == "backtest_only":
        if not run.get("from_session"):
            raise ConfigError("backtest_only 需要 run.from_session")
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
    _require(cfg, "strategy", "class")
    _require(cfg, "backtest", "account")
    _require(cfg, "backtest", "exchange_kwargs")
    return cfg


def load_config(config: Optional[str] = None) -> dict:
    """加载 YAML → 校验 → 应用日期覆盖。返回深拷贝后的配置，并附 `_config_path`。"""
    path = resolve_config_path(config)
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ConfigError(f"配置必须是 mapping: {path}")
    cfg = validate_run_section(raw)
    cfg = apply_test_overrides(cfg)
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
    return {
        "model": copy.deepcopy(cfg["model"]),
        "dataset": {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {
                "handler": handler_cfg,
                "segments": segments,
            },
        },
    }


def build_port_analysis_config(cfg: dict) -> dict:
    """组装 PortAnaRecord 配置（不含 model/dataset 运行时对象）。"""
    strategy = cfg["strategy"]
    backtest = copy.deepcopy(cfg["backtest"])
    if "benchmark" not in backtest:
        backtest["benchmark"] = cfg["data"]["benchmark"]
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
            "class": strategy["class"],
            "module_path": strategy.get("module_path", "qlib.contrib.strategy.signal_strategy"),
            "kwargs": {
                "topk": strategy["topk"],
                "n_drop": strategy["n_drop"],
            },
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
        "meta": meta,
        "mlruns_link": link,
        "handler_class": handler,
        "model_path": model_path,
        "session_dir": session_dir,
        "run_dir": run_dir,
    }
