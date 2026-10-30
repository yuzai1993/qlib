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
            candidate = (self.root / path).resolve()
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

    def resource(self, resource_id: str) -> Path | None:
        if not self._resources:
            self.snapshot()
        rel = self._resources.get(resource_id)
        if rel is None:
            return None
        path = self._safe_path(rel)
        return path if path and path.is_file() else None

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
        registry = self._safe_path("backtest/experiments/registry.jsonl")
        try:
            if registry is None or not registry.is_file():
                raise FileNotFoundError("指定研究根下没有 registry.jsonl")
            if registry.stat().st_size > _LIMIT:
                raise ValueError("registry 超过 16 MiB 限制")
            for line_no, line in enumerate(registry.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or not isinstance(row.get("exp_id"), str):
                        raise ValueError("实验记录必须有字符串 exp_id")
                    rows.append(row)
                except (ValueError, TypeError) as exc:
                    warnings.append(f"registry 第 {line_no} 行无效：{exc}")
        except (OSError, ValueError, UnicodeError) as exc:
            warnings.append(f"研究索引不可读：{exc}")

        by_id = {}
        for row in rows:
            kind = (
                "model"
                if row.get("phase_m_protocol") == "v1"
                else ("strategy" if str(row.get("report_kind", "")).startswith("phase_m_v1_b") else None)
            )
            if kind:
                if row["exp_id"] in by_id:
                    warnings.append(f"重复 exp_id，采用最后记录：{row['exp_id']}")
                by_id[row["exp_id"]] = self._record(row, kind)
        records = list(by_id.values())
        promotions: dict[str, list[dict]] = {}
        for record in records:
            target = by_id.get(record["promoted_to"])
            if not (
                target and target["kind"] == record["kind"] and target["is_baseline"] and target["version"]
            ):
                record["promoted_to"] = ""
            source = by_id.get(record["promoted_from"])
            if record["is_baseline"] and record["version"] and source and source["kind"] == record["kind"]:
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
        for kind in ("model", "strategy"):
            baseline = [r for r in records if r["kind"] == kind and r["is_baseline"]]
            explicit = [r for r in baseline if r.pop("_explicit_current", False)]
            current = max(explicit or baseline, key=lambda r: _version(r["version"]), default=None)
            if current:
                current["current"] = True
        for record in records:
            record.pop("_explicit_current", None)
        records.sort(
            key=lambda r: (
                r["kind"] != "model",
                not r["is_baseline"],
                _version(r["version"]) if r["is_baseline"] else (),
                r["date"],
                r["id"],
            )
        )
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
            "metric_definitions": {
                kind: [{"key": k, "label": label, "format": fmt} for k, label, fmt in defs]
                for kind, defs in (("model", MODEL_METRICS), ("strategy", STRATEGY_METRICS))
            },
            "sources": {
                "root": str(self.root),
                "registry": str(self.registry),
                "modified_at": modified_at,
                "registry_rows": len(rows),
            },
            "warnings": warnings,
        }

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
        ) or _window(row.get("eval_window"))
        if window is None and cell.get("start") and cell.get("end"):
            window = _window([cell["start"], cell["end"]])
        if window is None:
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", str(row.get("eval_protocol", "")))
            window = dates[-2:] if len(dates) >= 2 else WINDOW.copy()
            warnings.append("评估窗口据 registry 协议或 EXPERIMENT_STANDARD 历史标准补足")
        scope_text = str(row.get("comparison_scope", ""))
        scope = "standard"
        if pool != "all" or "pool" in scope_text.lower() or "paired_same" in scope_text:
            scope = "pool_diagnostic"
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
        if any(not r["comparable"] for r in records):
            raise ValueError("运行中、缺失结果、种子不完整或诊断记录不能参加正式比较")
        if len({r["comparison_key"] for r in records}) != 1:
            raise ValueError("研究轨道、评估池、窗口、聚合或评估协议不同，不能混合比较")
        return {"records": records, "comparison_key": records[0]["comparison_key"]}
