"""Editorial study views reference authoritative records without changing them."""


def training_target_results(record: dict, descriptor: dict, by_id: dict, rows: dict) -> dict:
    """Project a reviewed, fixed group; never substitute an unrelated parent cell."""
    result = {
        "kind": "training_target_comparison", "status": "unavailable", "title": "Top3×H5 · 训练目标对比",
        "note": "训练H表示预测未来多少日收益；评估固定Top3、持有5日。"
                "各组五种子信号分别合成后评估，数值来自各自已登记结果；此处为研究汇总。",
        "records": [], "warnings": [],
    }
    control = by_id.get(descriptor["control_ref"])
    key = control.get("comparison_key") if control else None
    selected = []
    horizons = set()
    for ref in descriptor["refs"]:
        child = by_id.get(ref)
        horizon = rows.get(ref, {}).get("train_label_horizon")
        if (not child or child.get("kind") != "model" or child.get("track") != record.get("track")
                or not child.get("official") or not child.get("comparable")
                or child.get("source_status") != "available" or child.get("scope") != "standard"
                or not key or child.get("comparison_key") != key
                or type(horizon) is not int or horizon <= 0 or horizon in horizons):
            result["warnings"].append(f"训练目标对照来源缺失、协议不一致或标签H无效：{ref}；暂停汇总展示。")
            return result
        horizons.add(horizon)
        label = f"H{horizon}" + ("（对照）" if ref == descriptor["control_ref"] else "")
        selected.append((horizon, {
            "id": ref, "kind": "model", "track": child["track"], "name": label, "version": "",
            "directory": child.get("directory", ""), "metrics": dict(child["metrics"]),
            "window": dict(child["window"]), "source": child["source"],
        }))
    result.update(status="available", records=[item for _, item in sorted(selected)])
    return result


def baseline_results(record: dict, baseline: dict | None, *, reason: str = "") -> dict:
    """Keep display comparisons separate from registry/promotion authority."""
    declared = record["comparison_baseline"]
    initial = declared.get("initial", False)
    version = declared.get("version", "")
    label = ("BT " if record["kind"] == "strategy" else "") + version if version else "未登记"
    if initial:
        label = "初始基线，无前版"
    empty = {key: None for key in record["metrics"]}
    control = baseline or {"id": declared.get("ref", ""), "metrics": empty, "window": {}, "seeds": []}

    def projection(source, role, name):
        return {"id": source["id"], "kind": record["kind"], "track": record["track"], "role": role,
                "name": name, "version": "", "directory": source.get("directory", ""),
                "metrics": dict(source["metrics"]), "window": dict(source.get("window", {})),
                "seeds": list(source.get("seeds", [])), "source": source.get("source", "")}

    notes = ["Baseline为本次实验当时的对照。"]
    warnings = []
    if initial:
        notes = ["初始基线无前版；Baseline行保持空白。"]
    elif reason:
        warnings.append(reason)
    if len(record.get("seeds", [])) == 1:
        seed = record["seeds"][0]
        notes.append(f"使用同种子{seed}对照；不代表五种子合成结果。")
    first, second = control.get("window", {}), record.get("window", {})
    if first == second and second.get("start"):
        notes.append(f"指标窗口：{second['start']}～{second['end']}。")
    elif first.get("start") and second.get("start"):
        notes.append(f"Baseline窗口：{first['start']}～{first['end']}；"
                     f"实验组窗口：{second['start']}～{second['end']}。")
        warnings.append("两组评估窗口不同，指标仅并列参考。")
    if baseline and record.get("comparison_key") != baseline.get("comparison_key"):
        warnings.append("账户、费用、样本池或评估协议存在差异，指标仅并列参考。")
    if record.get("scope") == "capital_cost_diagnostic":
        warnings.append("资金与费率复算仅作诊断，不用于策略晋升。")
    experiment = projection(record, "experiment", "实验组 · " + record["name"])
    if record.get("status") in {"failed", "invalid", "error", "registry_invalid"}:
        experiment["metrics"] = empty
    return {"kind": "baseline_comparison", "status": "available", "title": "实验组与Baseline",
            "note": "".join(notes), "warnings": warnings,
            "records": [projection(control, "baseline", f"Baseline · {label}"), experiment]}
