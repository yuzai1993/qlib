"""Read-only Phase M v1 catalog; never imports Qlib or deserializes model artifacts.

Registry entries identify experiments and promotions. Source JSON owns the numeric
results. Historical omissions can use the published protocol and registry seed
declarations, but contradictory evidence always prevents official comparison.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtest.scripts.experiment_registry import decode_registry_line
from backtest.scripts.research_layout import research_path_identity, relocate_path, resolve_research_path
from backtest.scripts.experiment_archive import (
    EXPERIMENT_TYPES, _load_object_with_revision, _validate_model_profile,
    classify_experiment, scan_experiment_archives,
)
from backtest.scripts.baseline_references import load_historical_baseline_sources
from backtest.scripts.research_labels import strategy_display_version, strategy_reference_text
from .model_profiles import project_model_profile
from .research_groups import baseline_results, training_target_results
from .strategy_groups import authenticated_group_protocol, collapse_strategy_groups, strategy_parameter_group
from .strategy_history import strategy_history


SEEDS = [42, 1000, 2000, 3000, 4000]
WINDOW = ["2020-08-03", "2026-07-31"]
MODEL_METRICS = [
    ("net_ann", "扣费净年化（算术）", "pct"),
    ("net_ann_vol", "扣费波动（HAC）", "pct"),
    ("net_sharpe", "扣费夏普", "number"),
    ("ann", "非扣费年化", "pct"),
    ("turnover", "日换手", "pct"),
    ("rank_ic_mean", "全局 h5 RankIC", "rankic"),
]
STRATEGY_METRICS = [
    ("annualized_return", "扣费累乘年化（CAGR）", "pct"),
    ("sharpe_ratio", "扣费夏普", "number"),
    ("alpha", "Alpha", "pct"),
    ("beta", "Beta", "number"),
    ("benchmark_cumulative_return", "基准涨幅", "pct"),
    ("max_drawdown", "最大回撤", "pct"),
    ("calmar_ratio", "Calmar", "number"),
    ("annualized_volatility", "年化波动", "pct"),
    ("annualized_one_way_turnover", "年化单边换手（倍）", "number"),
    ("annualized_return_arith", "算术年化（审计）", "pct"),
]
CSI1000_STRATEGY_METRICS = [
    (key, "扣费年化（历史原口径）" if key == "annualized_return" else label, fmt)
    for key, label, fmt in STRATEGY_METRICS
]
CSI1000_MODEL_METRICS = [
    ("ic_mean", "IC", "rankic"), ("rank_ic_mean", "RankIC", "rankic"),
    ("icir", "ICIR", "number"), ("rank_icir", "RankICIR", "number"),
]
_LIMIT = 16 * 1024 * 1024
_ALLOWED_SUFFIXES = {
    ".json",
    ".html",
    ".htm",
    ".md",
    ".txt",
    ".py",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".png",
    ".jpg",
    ".jpeg",
}
_SOURCE_DIRS = {"experiments", "result", "configs"}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _metrics(cell: Any, kind: str, *, sliced: bool = False) -> dict:
    definitions = MODEL_METRICS if kind == "model" else STRATEGY_METRICS
    return {key: _number(_dict(cell).get(key)) for key, _, _ in definitions if not (sliced and key == "rank_ic_mean")}


def _seed_ids(value: Any) -> list[int]:
    if isinstance(value, dict):
        value = list(value)
    if not isinstance(value, (list, tuple)):
        return []
    found = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("seed")
        if isinstance(item, bool):
            continue
        if str(item).isdigit():
            found.append(int(item))
    return sorted(found)


def _version(value: Any) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", str(value)))


def _canonical(value: Any) -> Any:
    """JSON numbers such as 5 and 5.0 describe the same research protocol."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return str(value)


def _primary(block: Any) -> dict:
    return _dict(_dict(_dict(block).get("3")).get("5"))


def _window(value: Any) -> list[str] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        dates = [str(v)[:10] for v in value]
        if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in dates):
            return dates
    return None


class ResearchCatalog:
    """Refreshable registry index with opaque, individually approved resources."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.registry = self.root / "backtest/experiments/registry.jsonl"
        self._resources: dict[str, str] = {}
        self._registry_detail_checks: dict[str, str] = {}
        self._resource_hashes: dict[str, str] = {}

    def _safe_path(self, rel: Any) -> Path | None:
        if not isinstance(rel, str) or not rel or "\\" in rel or "\x00" in rel:
            return None
        path = Path(rel)
        if path.is_absolute() or ".." in path.parts:
            return None
        if len(path.parts) < 3 or path.parts[0] != "backtest" or path.parts[1] not in _SOURCE_DIRS:
            return None
        if path.suffix.lower() not in _ALLOWED_SUFFIXES and path.name != "registry.jsonl":
            return None
        try:
            candidate = resolve_research_path(path, self.root)
            candidate.relative_to(self.root / "backtest" / path.parts[1])
            # Also reject a symlinked allowed directory that escapes the root.
            candidate.relative_to(self.root)
            return candidate
        except (OSError, RuntimeError, ValueError):
            return None

    def _resource(self, rel: Any, kind: str) -> dict | None:
        if not isinstance(rel, str) or not rel:
            return None
        resource_id = hashlib.sha256(rel.encode()).hexdigest()[:24]
        self._resources[resource_id] = rel
        path = self._safe_path(rel)
        return {"id": resource_id, "name": Path(rel).name, "available": bool(path and path.is_file()), "kind": kind}

    def _detail_identity(self, rel: str) -> str:
        """Keep pre-typed detail URLs without collapsing earlier audit versions."""
        parts = Path(rel).parts
        if len(parts) > 3 and parts[:2] == ("backtest", "experiments") and parts[2] in {"models", "strategies"}:
            previous = Path(*parts[:2], *parts[3:]).as_posix()
            if relocate_path(previous, self.root) != previous and self._safe_path(previous) == self._safe_path(rel):
                return previous
        return rel

    def resource(self, resource_id: str) -> Path | None:
        if not self._resources:
            self.snapshot()
        rel = self._resources.get(resource_id)
        if rel is None:
            return None
        path = self._safe_path(rel)
        detail_index = self._registry_detail_checks.get(resource_id)
        if detail_index is not None:
            try:
                decode_registry_line(detail_index, self.registry)
            except (OSError, ValueError, TypeError, UnicodeError):
                return None
        if path and path.is_file():
            try:
                if path.stat().st_size > _LIMIT:
                    return None
                expected = self._resource_hashes.get(resource_id)
                if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    return None
                return path
            except OSError:
                pass
        return None

    def _load(self, rel: Any, warnings: list[str]) -> tuple[dict, str]:
        path = self._safe_path(rel)
        if path is None:
            warnings.append("指标源路径缺失或不在允许范围内")
            return {}, "missing" if not rel else "unsafe"
        try:
            if not path.is_file():
                warnings.append("指标源缺失（原产物可能已清理），未使用 registry 数字替代")
                return {}, "missing"
            if path.stat().st_size > _LIMIT:
                raise ValueError("JSON 超过 16 MiB 限制")
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("JSON 顶层必须是对象")
            return doc, "available"
        except (OSError, ValueError, UnicodeError) as exc:
            warnings.append(f"指标源读取失败：{type(exc).__name__}: {exc}")
            return {}, "invalid"

    def snapshot(self) -> dict:
        warnings: list[str] = []
        rows = []
        self._resources = {}
        self._registry_detail_checks = {}
        self._resource_hashes = {}
        discovery = scan_experiment_archives(self.root)
        manifests = {m["exp_id"]: m for m in discovery["experiments"]}
        typed = discovery["mode"] == "typed"
        for error in discovery["errors"]:
            warnings.append(f"实验目录无效：{error.get('path', '')}：{error.get('message', '')}")
        detail_rows: dict[int, tuple[str, str]] = {}
        invalid_ids: set[str] = set()
        details_valid = True
        registry = self._safe_path("backtest/experiments/registry.jsonl")
        try:
            if registry is None or not registry.is_file():
                raise FileNotFoundError("指定研究根下没有 registry.jsonl")
            if registry.stat().st_size > _LIMIT:
                raise ValueError("registry 超过 16 MiB 限制")
            for line_no, line in enumerate(registry.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                index = None
                try:
                    index = json.loads(line)
                    if not isinstance(index, dict) or not isinstance(index.get("exp_id"), str):
                        raise ValueError("实验记录必须有字符串 exp_id")
                    row = decode_registry_line(line, registry)
                    if "_registry_schema" in index:
                        rel = (registry.relative_to(self.root).parent / index["detail_path"]).as_posix()
                        if self._safe_path(rel) is None:
                            raise ValueError("登记详情路径不在允许范围内")
                        rel = self._detail_identity(rel)
                        detail_rows[id(row)] = (rel, line)
                    rows.append(row)
                    invalid_ids.discard(row["exp_id"])
                except (OSError, ValueError, TypeError, UnicodeError) as exc:
                    if isinstance(index, dict) and any(
                        key in index for key in ("_registry_schema", "detail_path", "detail_sha256")
                    ):
                        details_valid = False
                        if isinstance(index.get("exp_id"), str):
                            invalid_ids.add(index["exp_id"])
                    warnings.append(f"registry 第 {line_no} 行无效：{exc}")
        except (OSError, ValueError, UnicodeError) as exc:
            warnings.append(f"研究索引不可读：{exc}")

        by_id = {}
        declared_baselines = {}
        for row in rows:
            # A broken newer event must never make an older event look current.
            if row["exp_id"] in invalid_ids:
                continue
            kind = (
                "model"
                if row.get("phase_m_protocol") == "v1"
                else ("strategy" if str(row.get("report_kind", "")).startswith("phase_m_v1_b") else None)
            )
            manifest = manifests.get(row["exp_id"])
            classification = classify_experiment(row)
            if kind or typed:
                if kind:
                    record = self._record(row, kind)
                    record.update(track="all_a", protocol_id=f"phase_m_v1:{kind}", registered=True)
                else:
                    placeholder = manifest or {
                        **classification, "exp_id": row["exp_id"], "title": row["exp_id"],
                        "directory": "", "metadata_path": "",
                    }
                    # Manifest assets may be inspected, but registry identity
                    # must be classified independently before they are merged.
                    placeholder = {**placeholder, "kind": classification["kind"], "track": classification["track"]}
                    record = self._archive_record(placeholder, row, detail_rows.get(id(row)))
                # First choose the intended current target from registry
                # declarations. Asset failures must not promote an older row.
                if record["is_baseline"]:
                    declared_baselines[row["exp_id"]] = {
                        key: record[key] for key in ("id", "track", "kind", "protocol_id", "version", "_explicit_current")
                    }
                else:
                    declared_baselines.pop(row["exp_id"], None)
                if typed and manifest is None:
                    message = f"已登记实验缺少有效实验目录：{row['exp_id']}"
                    warnings.append(message)
                    record["warnings"].append(message)
                    record.update(official=False, comparable=False, is_baseline=False, _explicit_current=False)
                elif manifest:
                    if manifest["kind"] != classification["kind"] or manifest["track"] != classification["track"]:
                        message = f"实验目录与登记股票范围/类别不符：{row['exp_id']}"
                        warnings.append(message)
                        record["warnings"].append(message)
                        record.update(official=False, comparable=False, is_baseline=False, _explicit_current=False)
                    self._archive_resources(record, manifest)
                if record["source_status"] in {"invalid", "unsafe"}:
                    record.update(official=False, comparable=False, is_baseline=False, _explicit_current=False)
                detail = detail_rows.get(id(row))
                if detail:
                    rel, line = detail
                    resource = self._resource(rel, "registry_record")
                    duplicate = any(r["kind"] == "registry_record" and r["available"]
                                    and self._safe_path(self._resources.get(r["id"])) == self._safe_path(rel)
                                    for r in record["resources"])
                    if resource and not duplicate and resource["id"] not in {r["id"] for r in record["resources"]}:
                        record["resources"].append(resource)
                    if resource:
                        self._registry_detail_checks[resource["id"]] = line
                by_id[row["exp_id"]] = record
        if typed:
            for eid, manifest in manifests.items():
                if eid not in by_id:
                    record = self._archive_record(manifest)
                    if eid in invalid_ids:
                        record["status"] = "registry_invalid"
                        record["warnings"].append("登记详情校验失败，档案仅供只读查看")
                    self._archive_resources(record, manifest)
                    by_id[eid] = record
        records = list(by_id.values())
        promotions: dict[str, list[dict]] = {}
        for record in records:
            target = by_id.get(record["promoted_to"])
            if not (
                target and self._same_protocol(target, record) and target["is_baseline"] and target["version"]
            ):
                record["promoted_to"] = ""
            source = by_id.get(record["promoted_from"])
            if record["is_baseline"] and record["version"] and source and self._same_protocol(source, record):
                promotions.setdefault(source["id"], []).append(record)
            else:
                record["promoted_from"] = ""
        for record in records:
            target = by_id.get(record["promoted_to"])
            if target is None:
                target = max(promotions.get(record["id"], []), key=lambda r: _version(r["version"]), default=None)
            record["promoted_version"] = record["version"] if record["is_baseline"] else ""
            if target:
                record["promoted_to"] = target["id"]
                if not record["is_baseline"]:
                    record["promoted_version"] = target["version"]
        groups = {(r["track"], r["kind"], r["protocol_id"]) for r in records}
        for track, kind, protocol in groups:
            baseline = [r for r in declared_baselines.values()
                        if (r["track"], r["kind"], r["protocol_id"]) == (track, kind, protocol)]
            explicit = [r for r in baseline if r["_explicit_current"]]
            intended = max(explicit or baseline, key=lambda r: _version(r["version"]), default=None)
            current = by_id.get(intended["id"]) if intended else None
            if current and current["is_baseline"] and details_valid:
                current["current"] = True
        if not details_valid:
            warnings.append("研究登记详情校验失败，暂停当前基线标识；修复详情后自动恢复")
        for record in records:
            record.pop("_explicit_current", None)
            manifest = manifests.get(record["id"], {})
            record["analysis_revision"] = manifest.get("metadata_revision", "")
            if manifest.get("title"):
                record["name"] = manifest["title"]
            category = manifest.get("experiment_type")
            if category not in EXPERIMENT_TYPES:
                category = "strategy_backtest" if record["kind"] == "strategy" else "uncategorized"
            record["experiment_type"] = category
            record["experiment_type_label"] = EXPERIMENT_TYPES[category]
            analysis = _dict(manifest.get("analysis"))
            record["analysis"] = {
                key: analysis[key]
                for key in ("conclusion", "evidence", "limitations", "review_status", "updated_at")
                if key in analysis
            }
        latest_rows = {row["exp_id"]: row for row in rows if row["exp_id"] not in invalid_ids}
        # Consolidated baseline receipts supply historical controls and model
        # descriptions only; they are never active experiments or promotions.
        historical = {}
        historical_sources = {}
        try:
            for asset in load_historical_baseline_sources(self.root):
                source = asset["row"]
                ref = source["exp_id"]
                if ref in by_id or ref in historical:
                    raise ValueError(f"历史基线身份重复：{ref}")
                projected = self._record(source, "model")
                projected.update(track="all_a", protocol_id="phase_m_v1:model", current=False)
                if projected["source_status"] != "available":
                    raise ValueError(f"历史基线指标源缺失：{ref}")
                historical[ref] = projected
                historical_sources[ref] = asset
        except (OSError, ValueError) as exc:
            historical = {}
            historical_sources = {}
            warnings.append(f"历史基线来源不可用：{exc}")
        comparison_records = {**historical, **by_id}
        for record in records:
            manifest = manifests.get(record["id"], {})
            record["change_summary"] = manifest.get("change_summary", "").strip()
            # Promotions historically rewrote some baseline_ref values to self
            # or a later version. Reviewed metadata preserves the original
            # comparison for display without changing scientific authority.
            declared = manifest.get("comparison_baseline")
            raw_ref = record.get("baseline_ref")
            ref = declared["ref"] if declared is not None else raw_ref if isinstance(raw_ref, str) else ""
            ref = ref.strip()
            if ref == "self":
                ref = ""
            target = comparison_records.get(ref)
            valid_target = bool(target and target["kind"] == record["kind"] and target["track"] == record["track"])
            version = declared["version"].strip() if declared is not None else target["version"] if valid_target else ""
            if target and (not valid_target or version and target["version"] and version != target["version"]):
                version = ""
                if declared is not None or record["kind"] == "model":
                    label = "模型" if record["kind"] == "model" else "策略"
                    warning = f"{label}历史对照版本与引用不符：{record['id']}"
                    record["warnings"].append(warning)
                    warnings.append(warning)
            record["comparison_baseline"] = {
                "ref": ref, "version": version, "initial": bool(declared and declared.get("initial"))
            }
            if record["kind"] == "model":
                record["model_profile"] = project_model_profile(self.root, record, manifest)
        for record in records:
            manifest = manifests.get(record["id"], {})
            group = manifest.get("training_target_comparison")
            if group is not None:
                record["core_results"] = self._training_target_results(record, manifest, group, by_id, latest_rows)
            else:
                record["core_results"] = self._baseline_results(record, manifest, comparison_records)
        for record in records:
            if record["kind"] != "strategy":
                continue
            manifest = manifests.get(record["id"], {})
            declared = latest_rows.get(record["id"], {}).get("model_ref")
            editorial_ref = manifest.get("model_ref", "")
            if declared not in (None, ""):
                ref = declared if isinstance(declared, str) else ""
            else:
                ref = editorial_ref
            if isinstance(declared, str) and declared and editorial_ref and editorial_ref != declared:
                warning = f"实验模型引用与登记不符：{record['id']}；展示采用登记来源 {declared}"
                record["warnings"].append(warning)
                warnings.append(warning)
            model = comparison_records.get(ref)
            model_summary = {"ref": ref, "version": "", "name": ""}
            if model and model["kind"] == "model":
                short_name = manifests.get(ref, {}).get("short_name")
                name = short_name.strip() if isinstance(short_name, str) and short_name.strip() else model["name"]
                name = re.sub(r"\s*(?:（\s*v\d+(?:\.\d+)*\s*）|\(\s*v\d+(?:\.\d+)*\s*\))\s*$",
                              "", name, flags=re.IGNORECASE).strip()
                model_summary.update(version=model["version"], name=name)
            record["model_summary"] = model_summary
            summary = _dict(manifest.get("strategy_summary"))
            record["strategy_summary"] = {"name": summary.get("name", record["name"]),
                                          "description": summary.get("description", "")}
        benchmark_records = [record for record in records if record["is_baseline"]]
        for ref, record in historical.items():
            benchmark_records.append(self._historical_benchmark(record, historical_sources[ref]))
        benchmark_by_id = {record["id"]: record for record in benchmark_records}
        for record in records:
            outputs = manifests.get(record["id"], {}).get("benchmark_outputs")
            if outputs is None:
                continue
            record["benchmark_outputs"] = []
            for output in outputs:
                target = benchmark_by_id.get(output["ref"])
                available = bool(target and target["kind"] == record["kind"]
                                 and target["track"] == record["track"] and target["version"] == output["version"]
                                 and target["source_status"] == "available")
                record["benchmark_outputs"].append({**output, "available": available})
                if not available:
                    record["warnings"].append(f"产出Benchmark引用不可用或版本不匹配：{output['ref']}")
        records.sort(
            key=lambda r: (
                r["kind"] != "model",
                not r["is_baseline"],
                _version(r["version"]) if r["is_baseline"] else (),
                r["date"],
                r["id"],
            )
        )
        parameter_groups = []
        for manifest in discovery["groups"]:
            descriptor = manifest["experiment_group"]
            runtime_protocols = {
                member["ref"]: authenticated_group_protocol(
                    self.root, by_id[member["ref"]], manifests[member["ref"]], descriptor["axes"], self._safe_path)
                for member in descriptor["members"] if member["ref"] in by_id and member["ref"] in manifests
            }
            group = strategy_parameter_group(manifest, by_id, [key for key, _, _ in STRATEGY_METRICS],
                                             runtime_protocols=runtime_protocols, member_manifests=manifests)
            self._archive_resources(group, manifest)
            # README is a conventional local entry point even when its manifest
            # does not repeat it in the explicit inventory.
            readme = self._resource((Path(manifest["directory"]) / "README.md").as_posix(), "plan")
            if readme and readme["available"] and readme["id"] not in {r["id"] for r in group["resources"]}:
                group["resources"].append(readme)
            resource_ids = {resource["id"] for resource in group["resources"]}
            for member in descriptor["members"]:
                for resource in by_id.get(member["ref"], {}).get("resources", []):
                    if resource["id"] in resource_ids:
                        continue
                    # Keep the original opaque ID and its registered safety/
                    # digest checks, while only the group summary is a primary
                    # report button in the history list.
                    kind = "variant_" + resource["kind"] if resource["kind"] in {"report", "metrics", "config"} else resource["kind"]
                    group["resources"].append({**resource, "kind": kind,
                                               "name": member["label"] + " · " + resource["name"]})
                    resource_ids.add(resource["id"])
            parameter_groups.append(group)
        experiment_records = collapse_strategy_groups(records, parameter_groups)
        # Display labels are projected only after registry selection, historical
        # comparison validation and group construction have used frozen versions.
        for record in [*records, *benchmark_records, *parameter_groups]:
            if record["kind"] != "strategy":
                continue
            context = {"kind": record["kind"], "track": record["track"]}
            record["display_version"] = strategy_display_version(record.get("version"), **context)
            record["display_name"] = strategy_reference_text(record.get("name"), **context)
            record["display_promoted_version"] = strategy_display_version(record.get("promoted_version"), **context)
            declared = record.get("comparison_baseline")
            if isinstance(declared, dict):
                declared["display_version"] = strategy_display_version(declared.get("version"), **context)
            for child in record.get("core_results", {}).get("records", []):
                child["display_version"] = strategy_display_version(child.get("version"), **context)
                child["display_name"] = strategy_reference_text(child.get("name"), **context)
        experiment_records = [
            {**record, "strategy_history": strategy_history(record, by_id, benchmark_by_id)}
            if record["kind"] == "strategy" else record for record in experiment_records
        ]
        mtimes = []
        for rel in ["backtest/experiments/registry.jsonl", *self._resources.values()]:
            path = self._safe_path(rel)
            try:
                if path and path.is_file():
                    mtimes.append(path.stat().st_mtime)
            except OSError:
                pass
        modified_at = datetime.fromtimestamp(max(mtimes), timezone.utc).isoformat() if mtimes else None
        return {
            "records": records,
            "experiment_records": experiment_records,
            "benchmarks": benchmark_records,
            "metric_definitions": {
                kind: [{"key": k, "label": label, "format": fmt} for k, label, fmt in defs]
                for kind, defs in (("model", MODEL_METRICS), ("strategy", STRATEGY_METRICS))
            },
            "metric_definitions_by_track": {
                track: {kind: [{"key": k, "label": label, "format": fmt} for k, label, fmt in defs]
                        for kind, defs in (("model", CSI1000_MODEL_METRICS if track == "csi1000" else MODEL_METRICS),
                                           ("strategy", CSI1000_STRATEGY_METRICS if track == "csi1000" else STRATEGY_METRICS))}
                for track in ("all_a", "csi1000")
            },
            "sources": {
                "root": str(self.root),
                "registry": str(self.registry),
                "modified_at": modified_at,
                "registry_rows": len(rows),
                "discovery": {"mode": discovery["mode"], "count": len(manifests),
                              "group_count": len(parameter_groups), "errors": discovery["errors"]},
            },
            "warnings": warnings,
        }

    @staticmethod
    def _same_protocol(first: dict, second: dict) -> bool:
        return all(first.get(key) == second.get(key) for key in ("track", "kind", "protocol_id"))

    def _historical_benchmark(self, record: dict, source: dict) -> dict:
        owner, reference = source["manifest"], source["reference"]
        benchmark = {**record, "resources": list(record["resources"]), "warnings": list(record["warnings"]),
                     "current": False, "benchmark_only": True, "registered": False,
                     "directory": owner["directory"], "analysis": {}, "analysis_revision": "",
                     "promoted_version": record["version"], "promoted_to": "", "promoted_from": ""}
        benchmark.pop("_explicit_current", None)
        profile = {}
        resources = [(reference, "registry_record")]
        declared = reference.get("profile")
        if declared:
            relative = (Path(owner["directory"]) / declared["path"]).as_posix()
            resources.append((declared, "metadata"))
            try:
                path = self._safe_path(relative)
                if path is None:
                    raise ValueError("历史模型介绍路径无效")
                profile, digest = _load_object_with_revision(path, max_bytes=1024 * 1024,
                                                             label="historical model profile")
                if digest != declared["sha256"]:
                    raise ValueError("历史模型介绍SHA256不符")
                if any(profile.get(key) != record[key] for key in ("kind", "track")):
                    raise ValueError("历史模型介绍类别或股票范围不符")
                if profile.get("exp_id") != record["id"]:
                    raise ValueError("历史模型介绍身份不符")
                _validate_model_profile(profile.get("model_profile", {}))
                benchmark["metadata_path"] = relative
            except (OSError, ValueError) as exc:
                profile = {}
                benchmark["warnings"].append(f"历史模型介绍不可用：{exc}")
        seen = {resource["id"] for resource in benchmark["resources"]}
        for asset, kind in resources:
            relative = (Path(owner["directory"]) / asset["path"]).as_posix()
            resource = self._resource(relative, kind)
            if resource and resource["id"] not in seen:
                self._resource_hashes[resource["id"]] = asset["sha256"]
                resource["available"] = self.resource(resource["id"]) is not None
                benchmark["resources"].append(resource)
                seen.add(resource["id"])
        category = profile.get("experiment_type")
        benchmark["experiment_type"] = (category if isinstance(category, str) and category in EXPERIMENT_TYPES
                                        else "uncategorized")
        benchmark["experiment_type_label"] = EXPERIMENT_TYPES.get(benchmark["experiment_type"], "未分类")
        benchmark["analysis"] = _dict(profile.get("analysis"))
        benchmark["model_profile"] = project_model_profile(self.root, benchmark,
                                                            {**profile, "directory": owner["directory"]})
        return benchmark

    def _baseline_results(self, record: dict, manifest: dict, by_id: dict) -> dict:
        declared = record["comparison_baseline"]
        baseline = by_id.get(declared["ref"])
        expected_version = _dict(manifest.get("comparison_baseline")).get("version") or declared["version"]
        reason = ""
        if declared.get("initial"):
            baseline = None
        elif (not baseline or baseline["id"] == record["id"]
              or baseline["kind"] != record["kind"] or baseline["track"] != record["track"]
              or expected_version and expected_version != baseline["version"]
              or baseline.get("status") in {"failed", "invalid", "error", "registry_invalid"}
              or baseline.get("source_status") != "available"):
            baseline = None
            reason = "当时Baseline缺失、来源无效或引用不匹配，保留空值对照行。"
        elif record["seeds"] != baseline["seeds"]:
            # A single-model probe must use the control's corresponding seed,
            # never its five-seed blend or an average of seed metrics.
            if record["kind"] == "model" and len(record["seeds"]) == 1:
                seed = record["seeds"][0]
                doc, status = self._load(baseline["source"], [])
                pool = _dict(_dict(doc.get("pools")).get(baseline["pool"]))
                block = _dict(_dict(pool.get("seeds")).get(str(seed)))
                cell = _primary(block.get("head"))
                if status == "available" and cell:
                    metrics = _metrics(cell, "model")
                    metrics["rank_ic_mean"] = _number(_dict(block.get("h5")).get("rank_ic_mean"))
                    baseline = {**baseline, "metrics": metrics, "seeds": [seed]}
                else:
                    baseline = None
            else:
                baseline = None
            if baseline is None:
                reason = "Baseline缺少匹配种子的源指标，不使用五种子合成值替代。"
        return baseline_results(record, baseline, reason=reason)

    def _training_target_results(self, record, manifest, descriptor, by_id, rows):
        # Consolidated experiments remain source receipts only. They never
        # re-enter the active registry, baseline discovery or promotion graph.
        study_records, study_rows = dict(by_id), dict(rows)
        errors = []
        for source in descriptor.get("archived_records", []):
            relative = (Path(manifest["directory"]) / source["path"]).as_posix()
            path = self._safe_path(relative)
            try:
                if path is None:
                    raise ValueError("归并来源路径无效")
                with path.open("rb") as stream:
                    payload = stream.read(_LIMIT + 1)
                if len(payload) > _LIMIT or hashlib.sha256(payload).hexdigest() != source["sha256"]:
                    raise ValueError("归并来源SHA256校验失败")
                row = json.loads(payload)
                if not isinstance(row, dict):
                    raise ValueError("归并来源不是登记对象")
                ref = row.get("exp_id")
                classification = classify_experiment(row)
                if (ref not in descriptor["refs"] or ref in study_rows or ref in study_records
                        or classification["kind"] != "model" or classification["track"] != record["track"]):
                    raise ValueError("归并来源身份冲突")
                projected = self._record(row, "model")
                projected.update(track=record["track"], directory=manifest["directory"])
                study_rows[ref], study_records[ref] = row, projected
            except (OSError, ValueError, UnicodeError) as exc:
                errors.append(f"训练目标归并来源不可用：{source['path']}（{exc}）")
        result = training_target_results(record, descriptor, study_records, study_rows)
        if errors:
            result.update(status="unavailable", records=[])
            result["warnings"].extend(errors)
        return result

    def _archive_ref(self, manifest: dict, value: Any) -> str | None:
        """Legacy inventory paths are rooted; new manifest resources are local."""
        raw = value.get("path") if isinstance(value, dict) else value
        if not isinstance(raw, str) or not raw:
            return None
        if raw.startswith("backtest/") and manifest.get("inventory_kind") == "asset_snapshot":
            return raw
        return (Path(manifest["directory"]) / raw).as_posix()

    def _archive_resources(self, record: dict, manifest: dict) -> None:
        record.update(activity=manifest.get("activity", ""), role=manifest.get("role", ""),
                      directory=manifest["directory"], metadata_path=manifest["metadata_path"])
        refs = [(manifest["metadata_path"], "metadata", None)]
        source = manifest.get("metrics_source")
        if isinstance(source, dict) and source.get("path"):
            rel = self._archive_ref(manifest, source)
            digest = source.get("sha256")
            refs.append((rel, "metrics", digest))
            if digest:
                source_path = self._safe_path(rel)
                for resource in record["resources"]:
                    if source_path and self._safe_path(self._resources.get(resource["id"])) == source_path:
                        self._resource_hashes[resource["id"]] = digest
                        resource["available"] = self.resource(resource["id"]) is not None
                if source_path and self._safe_path(record.get("source")) == source_path:
                    resource = self._resource(rel, "metrics")
                    self._resource_hashes[resource["id"]] = digest
                    if self.resource(resource["id"]) is None:
                        record.update(source_status="invalid", official=False, comparable=False,
                                      is_baseline=False, _explicit_current=False, years={}, regimes={})
                        record["metrics"] = {key: None for key in record["metrics"]}
                        record["warnings"].append("指标源 SHA256 校验失败或文件缺失")
        for key, kind in (("configs", "config"), ("reports", "report"), ("docs", "plan"),
                          ("notes", "plan"), ("data", "metrics"), ("metrics", "metrics"),
                          ("records", "registry_record"), ("historical_baselines", "registry_record")):
            values = manifest.get(key, [])
            if not isinstance(values, list):
                values = [values]
            refs.extend((self._archive_ref(manifest, value), kind,
                         value.get("sha256") if isinstance(value, dict) else None) for value in values)
        seen = {resource["id"] for resource in record["resources"]}
        for rel, kind, digest in refs:
            if kind == "registry_record" and rel:
                # Keep a declared legacy URL usable even when its display
                # identity is normalized to an earlier registry spelling.
                original = self._resource(rel, kind)
                if original and isinstance(digest, str):
                    self._resource_hashes[original["id"]] = digest
                try:
                    rel = self._detail_identity(rel)
                except ValueError:
                    record["warnings"].append("登记详情的来源路径映射存在歧义")
                    continue
            resource = self._resource(rel, kind)
            if not resource:
                continue
            if isinstance(digest, str):
                self._resource_hashes[resource["id"]] = digest
                resource["available"] = self.resource(resource["id"]) is not None
            if resource["id"] not in seen:
                record["resources"].append(resource)
                seen.add(resource["id"])
        # Register all migration URLs, but show each available file once per
        # purpose. Missing non-report evidence remains visible for auditing.
        available_files = set()
        resources = []
        for resource in record["resources"]:
            path = self.resource(resource["id"]) if resource["available"] else None
            if resource["kind"] == "report" and path is None:
                continue
            if path is not None:
                identity = (resource["kind"], path)
                if identity in available_files:
                    continue
                available_files.add(identity)
            resources.append(resource)
        record["resources"] = resources

    def _archive_record(self, manifest: dict, row: dict | None = None, detail: tuple | None = None) -> dict:
        """Adapt archived evidence without granting it Phase M v1 comparison rights."""
        registered = row is not None
        row = row or {}
        kind, track = manifest["kind"], manifest["track"]
        warnings = []
        declared_source = manifest.get("metrics_source")
        source = self._archive_ref(manifest, declared_source) if declared_source else None
        if not source and registered:
            source = row.get("portable_eval_output", row.get("eval_output"))
        if not source and kind == "model":
            candidates = [self._archive_ref(manifest, p) for p in manifest.get("data", [])]
            source = next((p for p in candidates if p and "test_1d" in p), None)
        doc, source_status = self._load(source, warnings)
        expected_hash = _dict(declared_source).get("sha256")
        if source_status == "available" and expected_hash:
            path = self._safe_path(source)
            if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                doc, source_status = {}, "invalid"
                warnings.append("指标源 SHA256 校验失败")
        # A verified registry detail is an explicit historical summary source.
        # A declared portable source that is broken cannot fall back to it.
        summary_allowed = bool(detail and not declared_source and "portable_eval_output" not in row)
        summary = row if summary_allowed else {}
        definitions = CSI1000_MODEL_METRICS if kind == "model" else (
            CSI1000_STRATEGY_METRICS if track == "csi1000" else STRATEGY_METRICS)
        strategy_cell = {}

        def cells(value: Any) -> dict:
            return {key: _number(_dict(value).get(key, {}).get("mean")
                                 if isinstance(_dict(value).get(key), dict) else _dict(value).get(key))
                    for key, _, _ in definitions}

        pool_metrics, years, regimes, neighborhood = {}, {}, {}, {}
        used_summary = False
        if kind == "model":
            for pool in ("csi1000", "csi300", "csi500", "all"):
                block = _dict(_dict(doc.get("pools")).get(pool))
                cell = block.get("ensemble") or block.get("seed_mean")
                if not cell:
                    cell = _dict(_dict(summary.get("metrics_by_eval_label")).get("eval_1d")).get(pool)
                    cell = cell or _dict(summary.get("metrics_summary")).get(pool)
                    used_summary = used_summary or bool(cell)
                if cell:
                    pool_metrics[pool] = cells(cell)
            pool = str(row.get("eval_pool") or ("csi1000" if track == "csi1000" else "all"))
            metrics = pool_metrics.get(pool, cells({}))
        else:
            block = _dict(doc.get("ensemble")) or _dict(doc.get("metrics")) or doc
            if not block or not any(k in block for k in ("full_period", "full", "annualized_return")):
                block = summary if summary.get("full_period") else (
                    _dict(summary.get("metrics")) or _dict(summary.get("metrics_summary")))
                if track == "csi1000" and isinstance(block.get("csi1000_full"), dict):
                    # B3/B4 promotions saved absolute full-history metrics under
                    # these short names. Pool-only ir/ann summaries use an excess
                    # return basis and must never share this conversion.
                    block = dict(block["csi1000_full"])
                    for short, key in (("ann", "annualized_return"), ("sharpe", "sharpe_ratio"),
                                       ("benchmark_cum", "benchmark_cumulative_return"),
                                       ("mdd", "max_drawdown"), ("calmar", "calmar_ratio"),
                                       ("vol", "annualized_volatility"),
                                       ("turnover", "annualized_one_way_turnover")):
                        if key not in block and short in block:
                            block[key] = block[short]
                used_summary = bool(block)
            strategy_cell = block.get("full_period") or block.get("full") or block
            metrics = cells(strategy_cell)
            years = {str(k): cells(v) for k, v in _dict(block.get("years")).items()}
            regimes = {str(k): cells(v) for k, v in block.items()
                       if k in {"pre_202409", "post_202409", "regimes"} and isinstance(v, dict)}
            neighborhood = _dict(doc.get("neighborhood"))
            if not neighborhood and summary.get("neighborhood"):
                neighborhood = _dict(summary["neighborhood"])
                used_summary = True
            pool = str(row.get("pool") or "csi1000")
        if used_summary and summary_allowed:
            source_status = "historical_summary"
            source = detail[0]
            warnings.append("历史摘要来自已校验登记详情 JSON；不是完整指标源，不参加正式比较")
        if kind == "strategy" and track == "csi1000":
            annualized_label = "扣费年化（历史原口径）"
            # The modern metrics protocol publishes both CAGR and its arithmetic
            # audit value. A missing audit field says nothing about old formulas.
            if _number(_dict(strategy_cell).get("annualized_return_arith")) is not None:
                annualized_label = "扣费累乘年化（CAGR）"
            basis = _dict(manifest.get("annualized_return_basis"))
            if source_status in {"available", "historical_summary"} and basis.get("kind") in ("arithmetic", "cagr"):
                source_path = self._safe_path(source)
                basis_path = self._safe_path(self._archive_ref(manifest, basis))
                try:
                    digest = (hashlib.sha256(source_path.read_bytes()).hexdigest()
                              if source_path and basis_path == source_path else None)
                except OSError:
                    digest = None
                if digest and digest == basis.get("source_sha256"):
                    annualized_label = ("扣费年化（历史算术口径）" if basis["kind"] == "arithmetic"
                                        else "扣费累乘年化（CAGR）")
            definitions = [(key, annualized_label if key == "annualized_return" else label, fmt)
                           for key, label, fmt in definitions]
        window = _window(doc.get("effective_eval_segment")) or _window(doc.get("backtest_window"))
        window = window or _window(row.get("backtest_window")) or _window(row.get("eval_window"))
        protocol = str(row.get("eval_protocol") or manifest.get("protocol_id") or "历史档案，协议未核验")
        baseline_match = re.fullmatch(r"baseline/b(\d+)-(m|s)(?:-on-b\d+-m)?", manifest["exp_id"])
        is_baseline = bool(registered and baseline_match and track == "csi1000"
                           and baseline_match.group(2) == ("m" if kind == "model" else "s"))
        record = {
            "id": manifest["exp_id"], "kind": kind, "track": track,
            "protocol_id": str(manifest.get("protocol_id") or f"archive:{track}:{kind}"),
            "registered": registered, "name": str(row.get("display_name") or manifest["title"]),
            "version": f"B{baseline_match.group(1)}" if is_baseline else "",
            "current": False, "is_baseline": is_baseline, "_explicit_current": False,
            "status": str(row.get("state") or "unknown") if registered else "unregistered",
            "source_status": source_status, "date": str(row.get("date") or ""),
            "direction": str(row.get("direction") or ""), "baseline_ref": row.get("baseline_ref"),
            "seeds": _seed_ids(row.get("seeds")), "official": False, "comparable": False,
            "comparison_key": None, "scope": "historical_archive" if registered else "unregistered",
            "window": {"start": window[0], "end": window[1]} if window else {"start": "", "end": ""},
            "pool": pool, "metrics": metrics, "pool_metrics": pool_metrics, "years": years, "regimes": regimes,
            "neighborhood": neighborhood, "protocol": protocol,
            "hypothesis": str(row.get("hypothesis") or ""), "conclusion": str(row.get("conclusion") or ""),
            "note": str(row.get("note") or ""),
            "promoted_to": row["promoted_to"] if isinstance(row.get("promoted_to"), str) else "",
            "promoted_from": row["promoted_from"] if isinstance(row.get("promoted_from"), str) else "",
            "warnings": warnings, "resources": [], "source": str(source or ""),
            "original_source": str(row.get("eval_output") or ""),
            "metric_definitions": [{"key": k, "label": label, "format": fmt} for k, label, fmt in definitions],
        }
        if not registered and track == "all_a" and source and source_status == "available":
            # New all-A draft sources may already use the standard report shape.
            adapted = self._record({"exp_id": manifest["exp_id"], "eval_output": source}, kind)
            for key in ("metrics", "years", "regimes", "window", "pool", "resources"):
                record[key] = adapted[key]
            record["metric_definitions"] = [{"key": k, "label": label, "format": fmt}
                                            for k, label, fmt in (MODEL_METRICS if kind == "model" else STRATEGY_METRICS)]
        resource = self._resource(source, "registry_record" if source_status == "historical_summary" else "metrics")
        if resource:
            if expected_hash:
                self._resource_hashes[resource["id"]] = expected_hash
                resource["available"] = self.resource(resource["id"]) is not None
            if resource["id"] not in {r["id"] for r in record["resources"]}:
                record["resources"].append(resource)
        return record

    def _record(self, row: dict, kind: str) -> dict:
        warnings: list[str] = []
        # Registry-owned portable summaries survive cleanup of the original result.
        # An explicitly declared but broken portable source must remain visible.
        eval_source = row.get("portable_eval_output", row.get("eval_output"))
        doc, source_status = self._load(eval_source, warnings)
        pool = str(row.get("eval_pool") or doc.get("pool") or "all")
        pool_doc = _dict(_dict(doc.get("pools")).get(pool))
        official_block = _dict(pool_doc.get("ensemble") if kind == "model" else doc.get("ensemble"))
        block = official_block
        fallback = False
        if not block and kind == "model" and pool_doc.get("seed_mean"):
            block = _dict(pool_doc["seed_mean"])
            fallback = True
            warnings.append("历史 fallback 来源为 seed_mean，非官方合成信号，不参加正式比较")
        if not block and kind == "strategy" and doc.get("full"):
            block = {"full_period": doc["full"], "years": doc.get("years"), "regimes": doc.get("regimes")}
            fallback = True
            warnings.append("延长窗口诊断采用 summary.full，非标准 ensemble 摘要")

        declared = _seed_ids(row.get("seeds"))
        evidence = []
        if kind == "model":
            if "seeds" in pool_doc:
                evidence.append(_seed_ids(pool_doc["seeds"]))
            if "sessions" in doc:
                evidence.append(_seed_ids(doc["sessions"]))
        else:
            if doc.get("seeds"):
                evidence.append(_seed_ids(doc["seeds"]))
            if "source_preds" in block:
                paths = block["source_preds"] if isinstance(block["source_preds"], list) else []
                source_seeds = sorted(
                    int(m.group(1)) for p in paths if (m := re.search(r"(?:^|[_/])s(\d+)(?:[_./]|$)", str(p)))
                )
                evidence.append(source_seeds if len(paths) == len(source_seeds) else [])
        complete = declared == SEEDS and all(s == SEEDS for s in evidence)
        if not evidence and declared == SEEDS and official_block:
            warnings.append("种子完整性据 registry 声明，源 JSON 未留独立清单")
        if not complete:
            warnings.append("五种子不完整或 registry 与源 JSON 种子证据不一致；仅作诊断")
        actual_seeds = evidence[0] if evidence else declared

        cell = _primary(block.get("head")) if kind == "model" else _dict(block.get("full_period"))
        metrics = _metrics(cell, kind)
        if kind == "model":
            metrics["rank_ic_mean"] = _number(_dict(block.get("h5")).get("rank_ic_mean"))
            years = {
                str(k): _metrics(_primary(v), kind, sliced=True) for k, v in _dict(block.get("head_years")).items()
            }
            regimes = {
                str(k): _metrics(_primary(v), kind, sliced=True) for k, v in _dict(block.get("head_regimes")).items()
            }
        else:
            years = {str(k): _metrics(v, kind) for k, v in _dict(block.get("years")).items()}
            regimes = {str(k): _metrics(v, kind) for k, v in _dict(block.get("regimes")).items()}
        if not cell and source_status == "available":
            warnings.append(
                "源 JSON 没有指定池 top3×h5 主格" if kind == "model" else "源 JSON 没有 ensemble.full_period"
            )

        window = _window(
            doc.get("effective_eval_segment") if kind == "model" else doc.get("backtest_window")
        )
        if kind == "model":
            window = window or _window(row.get("eval_window"))
        if window is None and cell.get("start") and cell.get("end"):
            window = _window([cell["start"], cell["end"]])
        if window is None and kind == "strategy":
            # The core metrics come from ensemble.full_period. Its own NAV
            # bounds take precedence over protocols that also name a forward
            # diagnostic period; those dates do not describe the main table.
            curve = block.get("nav_curve")
            if (
                isinstance(curve, list) and curve
                and isinstance(curve[0], (list, tuple)) and curve[0]
                and isinstance(curve[-1], (list, tuple)) and curve[-1]
            ):
                window = _window([curve[0][0], curve[-1][0]])
            window = window or _window(row.get("eval_window"))
        if window is None:
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", str(row.get("eval_protocol", "")))
            window = dates[-2:] if len(dates) >= 2 else WINDOW.copy()
            warnings.append("评估窗口据 registry 协议或 EXPERIMENT_STANDARD 历史标准补足")
        scope_text = str(row.get("comparison_scope", ""))
        capital_cost_diagnostic = kind == "strategy" and (
            bool(doc.get("diagnostic_periods"))
            or _dict(doc.get("provenance")).get("evaluation_mode")
            == "frozen_parameter_diagnostic_not_selection"
            or "capital_cost_diagnostic" in str(row.get("report_kind", ""))
            or scope_text == "capital_cost_diagnostic"
        )
        scope = "standard"
        if pool != "all" or "pool" in scope_text.lower() or "paired_same" in scope_text:
            scope = "pool_diagnostic"
        elif capital_cost_diagnostic:
            scope = "capital_cost_diagnostic"
            warnings.append("资金与费率复算仅作诊断；主指标对应主表窗口，新增期间结果另见原始报告")
        elif window != WINDOW or "extended_window" in str(row.get("report_kind", "")):
            scope = "extended_window_diagnostic"
        elif not complete:
            scope = "single_seed_diagnostic" if len(actual_seeds) == 1 else "seed_incomplete"
        elif fallback:
            scope = "legacy_fallback"
        elif row.get("report_kind") == "phase_m_v1_backtest":
            scope = "legacy_backtest"
        state = str(row.get("state") or "unknown")
        official = bool(official_block and complete and cell and not fallback)
        required = (
            ("net_ann", "net_ann_vol", "net_sharpe") if kind == "model" else ("annualized_return", "sharpe_ratio")
        )
        comparable = (
            official
            and scope == "standard"
            and state in {"completed", "promoted"}
            and all(metrics[k] is not None for k in required)
        )
        try:
            comparison_key = self._comparison_key(row, doc, block, kind, pool, window, warnings)
        except ValueError as exc:
            comparison_key = None
            comparable = official = False
            warnings.append(f"评估协议无效：{exc}")

        version = row.get("baseline_version") if kind == "model" else row.get("bt_version")
        is_baseline = bool(version) if kind == "model" else row.get("report_kind") == "phase_m_v1_bt_baseline"
        if is_baseline and not version:
            match = re.search(r"(v\d+)$", row["exp_id"])
            version = match.group(1) if match else ""
        resources = []
        refs = [
            (row.get("detail_report"), "report"),
            (eval_source, "metrics"),
            (row.get("eval_output") if eval_source != row.get("eval_output") else None, "original_metrics"),
            (row.get("paired_control_eval_output"), "paired_metrics"),
            (row.get("plan"), "plan"),
        ]
        configs = row.get("configs")
        if isinstance(configs, list):
            refs.extend((p, "config") for p in configs)
        session = block.get("session_dir")
        if isinstance(session, str):
            refs.append((f"{session}/run_01/report.html", "report"))
        seen = set()
        for rel, resource_kind in refs:
            resource = self._resource(rel, resource_kind)
            if resource and resource["id"] not in seen:
                resources.append(resource)
                seen.add(resource["id"])
        return {
            "id": row["exp_id"],
            "kind": kind,
            "name": str(row.get("display_name") or row.get("arm") or row["exp_id"]),
            "version": str(version or ""),
            "current": False,
            "is_baseline": is_baseline,
            "_explicit_current": any(row.get(k) is True for k in ("current_bt", "current_baseline", "current")),
            "status": state,
            "source_status": source_status,
            "date": str(row.get("date") or ""),
            "direction": str(row.get("direction") or ""),
            "baseline_ref": str(row["baseline_ref"]) if row.get("baseline_ref") is not None else None,
            "seeds": actual_seeds,
            "official": official,
            "comparable": comparable,
            "comparison_key": comparison_key,
            "scope": scope,
            "window": {"start": window[0], "end": window[1]},
            "pool": pool,
            "metrics": metrics,
            "years": years,
            "regimes": regimes,
            "protocol": str(
                row.get("eval_protocol") or "Phase M v1 · top3×h5 · 头部算术年化"
                if kind == "model"
                else row.get("eval_protocol") or "Phase M v1 · 执行层 CAGR"
            ),
            "hypothesis": str(row.get("hypothesis") or ""),
            "conclusion": str(row.get("conclusion") or ""),
            "note": str(row.get("note") or row.get("conclusion") or ""),
            "promoted_to": row["promoted_to"] if isinstance(row.get("promoted_to"), str) else "",
            "promoted_from": row["promoted_from"] if isinstance(row.get("promoted_from"), str) else "",
            "warnings": warnings,
            "resources": resources,
            "source": str(eval_source or ""),
            "original_source": str(row.get("eval_output") or ""),
        }

    def _comparison_key(
        self, row: dict, doc: dict, block: dict, kind: str, pool: str, window: list[str], warnings: list[str]
    ) -> str:
        """Normalize historical omissions to the published Phase M v1 protocol.

        Strategy parameters are deliberately excluded: these are what strategy
        experiments change. Costs, benchmark, account, filters and data vintage
        remain part of the comparison contract.
        """
        filters = _dict(doc.get("filters") if kind == "model" else doc.get("universe_filter"))
        if not filters and kind == "strategy":
            filters = _dict(block.get("universe_filter"))
        canonical_filters = {
            "st_filter": filters.get("st_filter", "daily"),
            "min_amount": filters.get("min_amount", 10000000),
            "min_listing_days": filters.get("min_listing_days", 60),
        }
        if kind == "strategy":
            canonical_filters["min_recent_trading_days"] = filters.get("min_recent_trading_days", 60)
            # The execution reports store the actual ST source in st_daily;
            # st_filter is only a summary in some historical reports. An
            # explicit null/empty source disables filtering and is not missing.
            canonical_filters["st_daily"] = filters.get("st_daily", "scripts/data_collector/tushare/st_daily.csv")
            if "st_daily" not in filters:
                warnings.append("历史 st_daily 元数据缺失，按 EXPERIMENT_STANDARD 日频名单补足")
            if canonical_filters["st_daily"] is not None and not isinstance(canonical_filters["st_daily"], str):
                raise ValueError("ST 日频名单路径必须是字符串或 null")
        if not filters:
            warnings.append("历史过滤元数据缺失，按 EXPERIMENT_STANDARD 的本轨道协议标识")
        method = doc.get("official_signal") if kind == "model" else block.get("method")
        if method is not None and method not in ("daily_zscore_mean", "seed_zscore_mean"):
            raise ValueError("合成信号方式必须是 daily_zscore_mean / seed_zscore_mean")
        method = "daily_zscore_mean"
        for key, value in canonical_filters.items():
            if key not in {"st_filter", "st_daily"}:
                number = _number(value)
                if number is None or number < 0:
                    raise ValueError(f"过滤参数 {key} 不是有限非负数")
                canonical_filters[key] = number
        if not isinstance(canonical_filters["st_filter"], str):
            raise ValueError("ST 过滤方式不是字符串")
        protocol: dict[str, Any] = {
            "track": "phase_m_v1",
            "kind": kind,
            "pool": pool,
            "window": window,
            "aggregation": method,
            "filters": canonical_filters,
            "data_version": str(doc.get("data_version") or row.get("data_version") or "2026-08-21"),
            "normalization_pool": str(row.get("signal_normalization_pool") or "all"),
        }
        if kind == "model":
            labels = doc.get("eval_labels", {})
            if not isinstance(labels, dict):
                raise ValueError("eval_labels 必须是标签映射")
            label = labels.get("h5", "Ref($close, -6)/Ref($close, -1) - 1")
            head_universe = doc.get("head_universe", "t+1 可成交（剔除次日涨停封板与零成交量）")
            if not isinstance(label, str) or not label.strip():
                raise ValueError("eval_labels.h5 必须是非空标签表达式")
            if not isinstance(head_universe, str) or not head_universe.strip():
                raise ValueError("head_universe 必须是非空头部可成交范围")
            if "h5" not in labels or "head_universe" not in doc:
                warnings.append("历史 h5 标签或头部可成交范围元数据缺失，按 EXPERIMENT_STANDARD 补足")
            protocol.update(
                {
                    "primary": "top3_h5",
                    "eval_label_h5": re.sub(r"\s+", "", label),
                    "head_universe": head_universe.strip(),
                    "annualization": "arithmetic_238_hac",
                    "cost_round_trip": doc.get("cost_round_trip", 0.00092),
                    "label_window_cutoff": _dict(doc.get("label_window_cutoff")).get("h5", "2026-07-23"),
                }
            )
        else:
            fees = _dict(doc.get("fees"))
            protocol.update(
                {
                    "annualization": "cagr_250",
                    "account": doc.get("account", 1000000),
                    "fees": {
                        k: fees.get(k, v)
                        for k, v in {
                            "open_cost": 0.00021,
                            "close_cost": 0.00071,
                            "min_cost": 5,
                            "trade_unit": 100,
                        }.items()
                    },
                    "benchmark": _dict(doc.get("benchmark"))
                    or {"equal_weight_csv": "backtest/configs/regime-adapt/bench_ew_all.csv"},
                }
            )
            if not fees:
                warnings.append("历史费用元数据缺失，按 EXPERIMENT_STANDARD 本轨道费率标识")
        numbers = (
            {"cost_round_trip": protocol["cost_round_trip"]}
            if kind == "model"
            else {**protocol["fees"], "account": protocol["account"]}
        )
        for key, value in numbers.items():
            number = _number(value)
            if number is None or number < 0:
                raise ValueError(f"费用/账户参数 {key} 不是有限非负数")
            if kind == "model" or key == "account":
                protocol[key] = number
            else:
                protocol["fees"][key] = number
        if kind == "strategy":
            def benchmark_identity(value: Any) -> Any:
                if isinstance(value, dict):
                    return {key: benchmark_identity(child) for key, child in value.items()}
                if isinstance(value, list):
                    return [benchmark_identity(child) for child in value]
                return research_path_identity(value, self.root) if isinstance(value, str) else value

            # Relocating an explicitly registered benchmark file must not
            # create a different evaluation protocol. Keep original source
            # documents and resource paths unchanged outside this hash input.
            protocol["benchmark"] = benchmark_identity(protocol["benchmark"])
        # Stringifying preserves a stable, inspectable key without emitting
        # non-finite numeric values in the public JSON response.
        encoded = json.dumps(_canonical(protocol), sort_keys=True, ensure_ascii=False, allow_nan=False)
        return f"phase_m_v1:{kind}:" + hashlib.sha256(encoded.encode()).hexdigest()[:20]

    def compare(self, ids: list[str]) -> dict:
        if not ids or len(ids) > 50 or len(set(ids)) != len(ids):
            raise ValueError("请选择 1–50 条互不重复的同口径实验")
        by_id = {r["id"]: r for r in self.snapshot()["records"]}
        if any(eid not in by_id for eid in ids):
            raise ValueError("包含未知实验")
        records = [by_id[eid] for eid in ids]
        if len({(r["track"], r["kind"], r["protocol_id"]) for r in records}) != 1:
            raise ValueError("股票范围或评估协议不同，不能混合比较")
        if any(not r["comparable"] for r in records):
            raise ValueError("运行中、缺失结果、种子不完整或诊断记录不能参加正式比较")
        if len({r["comparison_key"] for r in records}) != 1:
            raise ValueError("股票范围、评估池、窗口、聚合或评估协议不同，不能混合比较")
        return {"records": records, "comparison_key": records[0]["comparison_key"]}
