"""live 配置加载：base（paper_trading）+ live 覆盖的显式合并。

YAML 无原生 extends；这里读 ``base_config`` 指向的 paper 配置作为基底，
live 配置中的同名段做递归覆盖（dict 深合并，标量/列表直接替换）。
"""

from pathlib import Path

import yaml


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_live_config(config_path, project_root=None) -> dict:
    """加载 live 配置并合并 base 配置。

    Args:
        config_path: live yaml 路径
        project_root: 仓库根（解析 base_config 相对路径用）；默认从本文件推断
    """
    config_path = Path(config_path)
    if project_root is None:
        project_root = Path(__file__).resolve().parents[2]
    project_root = Path(project_root)

    with open(config_path) as f:
        live_cfg = yaml.safe_load(f)

    base_rel = live_cfg.pop("base_config", None)
    if base_rel:
        base_path = project_root / base_rel
        with open(base_path) as f:
            base_cfg = yaml.safe_load(f)
        merged = _deep_merge(base_cfg, live_cfg)
    else:
        merged = live_cfg

    merged["_config_path"] = str(config_path)
    merged["_config_id"] = config_path.stem
    return merged
