"""Read-only model recipe descriptions and standardized resource purposes."""

from __future__ import annotations

from pathlib import Path


RESOURCE_PURPOSES = {
    "report": ("实验报告", "给人阅读的原始实验报告。"),
    "metrics": ("评估结果", "平台读取的评估指标与配套结果数据。"),
    "original_metrics": ("原运行评估结果", "运行目录中的原始结果，可能已按保留规则清理。"),
    "plan": ("研究方案", "实验设计、协议与研究说明。"),
    "config": ("运行配置", "模型训练或评估使用的参数配置。"),
    "metadata": ("实验元数据", "目录清单、展示说明与人工结论。"),
    "registry_record": ("历史登记", "冻结的历史登记事件及指标来源。"),
}


def project_model_profile(root: Path, record: dict, manifest: dict) -> dict:
    declared = manifest.get("model_profile", {})
    sample = lambda key: {k: declared.get(key, {}).get(k, "").strip() for k in ("pool", "time_range", "filters")}
    directory = manifest.get("directory", "")
    try:
        experiment_id = Path(directory).relative_to("backtest/experiments").as_posix() if directory else ""
    except ValueError:
        experiment_id = ""
    paths = []
    for item in declared.get("model_paths", []):
        path = root / item["path"]
        try:
            available = path.resolve().is_relative_to(root) and path.is_file()
        except (OSError, RuntimeError, ValueError):
            available = False
        paths.append({**item, "available": available})
    artifacts = []
    for resource in record.get("resources", []):
        label, description = RESOURCE_PURPOSES.get(resource["kind"], ("配套产物", "本实验的配套研究资料。"))
        artifacts.append({"resource_id": resource["id"], "name": resource["name"],
                          "kind": resource["kind"], "available": resource["available"],
                          "label": label, "description": description})
    return {"version": record.get("version") or "未晋升", "experiment_id": experiment_id,
            "structure": declared.get("structure", "").strip(), "features": declared.get("features", "").strip(),
            "seeds": list(record.get("seeds") or []),
            "training_sample": sample("training_sample"), "training_target": declared.get("training_target", "").strip(),
            "evaluation_sample": sample("evaluation_sample"), "early_stopping": declared.get("early_stopping", "").strip(),
            "prediction_signal": declared.get("prediction_signal", "").strip(),
            "changes": declared.get("changes", "").strip(), "model_paths": paths, "artifacts": artifacts}
