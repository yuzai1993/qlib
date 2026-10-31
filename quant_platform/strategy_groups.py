"""Read-only parameter-study projections over intact strategy source records."""

from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
import re
import yaml

from backtest.scripts.experiment_archive import _safe_path


def authenticated_group_protocol(root, record: dict, manifest: dict, axes: list[str], source_path) -> dict | None:
    """Compare preserved runtime evidence when old registry data-version fields differ.

    Only declared strategy knobs may vary. Account, cost, dates, signal inputs,
    filters and all other configuration remain in the signature. Old absolute
    benchmark/ST spellings are replaced with authenticated file/input hashes.
    A present but unauthenticated config fails closed instead of using defaults.
    """
    try:
        config_path = _safe_path(root, manifest['directory'] + '/configs/config.yaml')
        if not config_path.exists():
            return None
        if not config_path.is_file() or config_path.stat().st_size > 1024 * 1024:
            raise ValueError('runtime config must be a bounded regular file')
        source = source_path(record.get('source'))
        if source is None or not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('missing bounded source')
        provenance = json.loads(source.read_text()).get('provenance', {})
        hashes = {}
        for key in ('source_pred_sha256', 'st_daily_sha256', 'model_manifest_sha256', 'forward_pred_sha256'):
            digest = provenance.get(key)
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError('missing authenticated input hash')
            hashes[key] = digest
        config_bytes = config_path.read_bytes()
        if (len(config_bytes) > 1024 * 1024
                or hashlib.sha256(config_bytes).hexdigest() != provenance.get('config_sha256')):
            raise ValueError('runtime config hash mismatch')
        config = yaml.safe_load(config_bytes)
        if not isinstance(config, dict):
            raise ValueError('runtime config must be an object')
        strategy = config['strategy']
        kwargs = strategy.pop('kwargs', {})
        if not isinstance(strategy, dict) or not isinstance(kwargs, dict):
            raise ValueError('invalid strategy configuration')
        if any(key in strategy and strategy[key] != value for key, value in kwargs.items()):
            raise ValueError('ambiguous duplicate strategy knobs')
        strategy.update(kwargs)
        for axis in axes:
            strategy.pop(axis, None)
        benchmark_path = _safe_path(root, manifest['directory'] + '/data/bench_ew_all.csv')
        if not benchmark_path.is_file() or benchmark_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('missing bounded benchmark copy')
        benchmark_digest = hashlib.sha256(benchmark_path.read_bytes()).hexdigest()
        if provenance.get('benchmark_sha256', benchmark_digest) != benchmark_digest:
            raise ValueError('benchmark copy hash mismatch')
        window = record.get('window', {})
        with benchmark_path.open(newline='') as stream:
            reader = csv.reader(stream)
            header = next(reader)
            benchmark_rows = [entry for entry in reader if entry and window.get('start', '') <= entry[0] <= window.get('end', '')]
        if not benchmark_rows or any(len(entry) != len(header) for entry in benchmark_rows):
            raise ValueError('missing benchmark evidence for the core metric window')
        # Core rows describe the historical main window. Forward diagnostic
        # series keep their own file hashes/results and are never pooled here.
        benchmark_window_digest = hashlib.sha256(json.dumps([header, benchmark_rows]).encode()).hexdigest()
        for section in ('backtest', 'data'):
            if 'benchmark' not in config.get(section, {}):
                continue
            benchmark = config[section]['benchmark']
            if (not isinstance(benchmark, dict) or set(benchmark) != {'equal_weight_csv'}
                    or not isinstance(benchmark['equal_weight_csv'], str)):
                raise ValueError('unsupported benchmark declaration')
            config[section]['benchmark'] = {'core_window_rows_sha256': benchmark_window_digest}
        if 'st_daily' in config.get('universe_filter', {}):
            config['universe_filter']['st_daily'] = {'sha256': hashes['st_daily_sha256']}
        authenticated = {'config': config, 'inputs': hashes,
                         'evaluation_mode': provenance.get('evaluation_mode'),
                         'selection_cutoff': provenance.get('selection_cutoff'),
                         'requested_end': provenance.get('requested_end')}
        return {'status': 'available', 'signature': hashlib.sha256(
            json.dumps(authenticated, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError,
            RecursionError, StopIteration, csv.Error):
        return {'status': 'invalid'}


def strategy_parameter_group(manifest: dict, by_id: dict, metric_keys: list[str], *, runtime_protocols=None) -> dict:
    descriptor = manifest["experiment_group"]
    members = descriptor["members"]
    control_ref = descriptor["control_ref"]
    labels = {member["ref"]: member["label"] for member in members}
    refs = [member["ref"] for member in members]
    control = by_id.get(control_ref)
    selected = [by_id.get(ref) for ref in refs]
    warnings = []
    for ref, child in zip(refs, selected):
        if (not child or child.get("kind") != "strategy" or child.get("track") != manifest["track"]
                or child.get("model_summary", {}).get("ref") != manifest["model_ref"]
                or child.get("status") != "completed" or child.get("source_status") != "available"):
            warnings.append(f"参数实验来源缺失、未完成或模型/股票范围不符：{ref}；保留原实验展示。")
    if not warnings and control:
        protocol_keys = ("window", "seeds", "scope", "pool", "protocol_id")
        runtime_protocols = runtime_protocols or {}
        control_evidence = runtime_protocols.get(control_ref)
        for ref, child in zip(refs, selected):
            evidence = runtime_protocols.get(ref)
            # Strong available evidence also detects contradictions hidden by
            # historical comparison-key defaults, even when those keys match.
            if control_evidence is not None or evidence is not None:
                same_key = bool(control_evidence and evidence and control_evidence.get('status') == 'available'
                                and evidence.get('status') == 'available'
                                and control_evidence['signature'] == evidence['signature'])
            else:
                same_key = bool(child.get("comparison_key") and child.get("comparison_key") == control.get("comparison_key"))
            if not same_key or any(child.get(key) != control.get(key) for key in protocol_keys):
                warnings.append(f"参数实验账户、费用、样本或评估协议不一致：{ref}；暂停汇总比较。")
    available = not warnings
    analysis = deepcopy(manifest.get("analysis", {}))
    core = {"kind": "strategy_parameter_comparison", "status": "available" if available else "unavailable",
            "title": "参数组与Baseline", "note": descriptor["fixed_summary"], "records": [], "warnings": warnings}
    if available:
        for ref in [control_ref, *(ref for ref in refs if ref != control_ref)]:
            child = by_id[ref]
            role = "baseline" if ref == control_ref else "experiment"
            core["records"].append({
                "id": ref, "kind": "strategy", "track": child["track"], "role": role,
                "label": labels[ref], "name": ("Baseline · " if role == "baseline" else "实验组 · ") + labels[ref],
                "version": child.get('version', ''), "directory": child.get("directory", ""),
                **{key: deepcopy(child[key]) for key in ("metrics", "window", "seeds", "source")},
            })
    return {
        "id": manifest["exp_id"], "entity_type": "experiment_group", "kind": "strategy", "track": manifest["track"],
        "name": manifest["title"], "version": "", "current": False, "is_baseline": False,
        "official": False, "comparable": False, "registered": False,
        "status": "completed" if available else "unavailable", "source_status": "available" if available else "unavailable",
        "date": max((child.get("date", "") for child in selected if child), default=""),
        "direction": control.get("direction", "") if control else "", "baseline_ref": "",
        "seeds": [], "comparison_key": None, "scope": "strategy_parameter_study", "protocol_id": "editorial:strategy_group",
        "window": {}, "pool": control.get("pool", "all") if control else "all",
        "metrics": {key: None for key in metric_keys}, "years": {}, "regimes": {},
        "protocol": descriptor["fixed_summary"], "hypothesis": "", "note": "", "source": "", "original_source": "",
        "conclusion": analysis.get("conclusion", ""), "analysis": analysis,
        "analysis_revision": manifest["metadata_revision"], "change_summary": manifest.get("change_summary", ""),
        "comparison_baseline": {"ref": control_ref, "version": control.get('version', '') if control and control.get('is_baseline') else '', "initial": False},
        "promoted_to": "", "promoted_from": "", "promoted_version": "",
        "experiment_type": "strategy_backtest", "experiment_type_label": "策略回测",
        "model_summary": deepcopy(control.get("model_summary", {})) if control else {"ref": manifest["model_ref"], "version": "", "name": ""},
        "strategy_summary": deepcopy(manifest.get("strategy_summary", {"name": manifest["title"], "description": ""})),
        "group_summary": {"control_ref": control_ref, "control_label": labels[control_ref], "member_refs": refs,
                          "variant_count": len(refs), "axes": list(descriptor["axes"]), "fixed_summary": descriptor["fixed_summary"]},
        "core_results": core, "resources": [], "warnings": warnings,
    }


def collapse_strategy_groups(records: list[dict], groups: list[dict]) -> list[dict]:
    """Only complete, unambiguous projections can replace source rows in history."""
    counts = Counter(ref for group in groups if group["core_results"]["status"] == "available"
                     for ref in group["group_summary"]["member_refs"])
    hidden = set()
    for group in groups:
        refs = group["group_summary"]["member_refs"]
        if any(counts[ref] > 1 for ref in refs):
            warning = "源实验同时属于多个参数汇总；暂停折叠并保留原实验。"
            group["warnings"].append(warning)
            group.update(status="unavailable", source_status="unavailable")
            group["core_results"].update(status="unavailable", records=[])
        elif group["core_results"]["status"] == "available":
            hidden.update(refs)
    return [record for record in records if record["id"] not in hidden] + groups
