"""Preregister and finalize the B2-S local neighborhood experiment."""

from __future__ import annotations

import argparse
import copy
import html
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from phase_s_protocol import sha256_file  # noqa: E402
from config_loader import load_config  # noqa: E402
from run_strategy_neighborhood import effective_config_sha256  # noqa: E402
from strategy_neighborhood_protocol import score_valid_candidates  # noqa: E402

EXP_ID = "strategy-neighborhood/b2-s-local-v1"
DEFAULT_ROOT = REPO_ROOT / "backtest/experiments/strategy-neighborhood/20260802_b2s_local"
DEFAULT_REGISTRY = REPO_ROOT / "backtest/experiments/registry.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "backtest/experiments/strategy_neighborhood_report.html"
DEFAULT_CONFIGS = REPO_ROOT / "backtest/configs/strategy-neighborhood/b2-s-local"
METRICS = (
    ("excess_with_cost_information_ratio", "扣费超额IR", False),
    ("excess_with_cost_annualized_return", "扣费超额年化", True),
    ("excess_with_cost_max_drawdown", "扣费最大回撤", True),
    ("annualized_one_way_turnover", "年化单边换手", False),
)


def _path_text(path: Path) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    resolved = candidate.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(resolved)


def _hash_if_file(path: Path) -> str | None:
    return sha256_file(path) if Path(path).is_file() else None


_PORTABLE_PATH_KEYS = {
    "config",
    "config_path",
    "manifest_path",
    "model_path",
    "path",
    "result_dir",
    "source_pred",
}


def _portable_artifact_paths(value: Any, key: str | None = None) -> Any:
    """Rewrite repository-owned artifact paths without embedding a worktree root."""
    if isinstance(value, dict):
        return {
            item_key: _portable_artifact_paths(item_value, item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_portable_artifact_paths(item, key) for item in value]
    if isinstance(value, str) and key in _PORTABLE_PATH_KEYS:
        return _path_text(Path(value))
    return value


def build_preregistered_row(
    protocol: dict[str, Any],
    prediction_manifest: dict[str, Any],
    *,
    protocol_path: Path,
    prediction_manifest_path: Path,
    configs_dir: Path,
) -> dict[str, Any]:
    grid = protocol.get("strategy_grid") or []
    if protocol.get("exp_id") != EXP_ID or len(grid) != 540:
        raise ValueError("protocol identity or candidate count is invalid")
    expected = {
        ("csi1000", "valid"),
        ("csi1000", "test"),
        ("csi300", "test"),
        ("csi500", "test"),
    }
    used = [
        copy.deepcopy(entry)
        for entry in prediction_manifest.get("predictions") or []
        if (entry.get("pool"), entry.get("segment")) in expected
        and entry.get("model_ref") == "b6-m"
    ]
    identities = {(entry.get("pool"), entry.get("segment")) for entry in used}
    if len(used) != 4 or identities != expected:
        raise ValueError("prediction artifact matrix must contain exactly four used artifacts")
    used = _portable_artifact_paths(used)
    model = _portable_artifact_paths(
        (prediction_manifest.get("models") or {}).get("b6-m") or {}
    )
    return {
        "exp_id": EXP_ID,
        "direction": "strategy-neighborhood-b2-s",
        "phase": "S",
        "date": str(date.today()),
        "state": "preregistered",
        "conclusion": "preregistered",
        "hypothesis": (
            "B2-S 附近存在对 topk、替换数、最低持有期与资金使用率不敏感的稳健平台；"
            "按 valid 轴向邻域 IR 下分位选择可避免单点尖峰过拟合。"
        ),
        "baseline_ref": protocol["baseline_ref"],
        "frozen_model_ref": protocol["frozen_model_ref"],
        "model_ref": protocol["model_ref"],
        "model_manifest": model.get("manifest_path"),
        "model_path": model.get("model_path"),
        "model_sha256": model.get("model_sha256"),
        "protocol_path": _path_text(protocol_path),
        "protocol_sha256": _hash_if_file(protocol_path),
        "prediction_manifest": _path_text(prediction_manifest_path),
        "prediction_manifest_sha256": _hash_if_file(prediction_manifest_path),
        "prediction_artifacts": used,
        "configs_dir": _path_text(configs_dir),
        "candidate_count": len(grid),
        "selection_pool": protocol["selection_pool"],
        "selection_segment": protocol["selection_segment"],
        "selection_metric": "axial_neighbor_excess_with_cost_ir_p25",
        "selection_rule": protocol["selection_rule"],
        "test_pools": protocol["test_pools"],
        "test_segment": protocol["test_segment"],
        "test_policy": protocol["test_policy"],
        "account": protocol["account"],
        "data_version": prediction_manifest.get("data_version"),
        "metrics_summary": {},
        "result_dirs": [],
        "cleanup_retention_eligible": False,
        "note": "540 组网格与稳健选择规则已冻结；test 尚未打开。",
    }


def build_complete_row(
    preregistered: dict[str, Any],
    protocol: dict[str, Any],
    valid: dict[str, Any],
    test: dict[str, Any],
    *,
    valid_path: Path,
    test_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    if preregistered.get("state") != "preregistered":
        raise ValueError("finalization requires a preregistered row")
    winner = valid.get("winner") or {}
    valid_rows = valid.get("all_rows") or []
    grid = protocol.get("strategy_grid") or []
    if valid.get("state") != "valid_complete" or len(valid_rows) != 540:
        raise ValueError("valid grid is incomplete")
    expected_ids = [str(item["candidate_id"]) for item in grid]
    actual_ids = [str(item.get("candidate_id")) for item in valid_rows]
    if len(set(actual_ids)) != len(actual_ids) or set(actual_ids) != set(expected_ids):
        raise ValueError("valid candidate IDs must exactly match protocol grid")
    _, recomputed_winner = score_valid_candidates(valid_rows, grid)
    if winner.get("candidate_id") != recomputed_winner.get("candidate_id"):
        raise ValueError("stored winner differs from recomputed valid winner")
    if not math.isclose(
        float(winner.get("neighbor_ir_p25")),
        float(recomputed_winner.get("neighbor_ir_p25")),
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("stored winner score differs from recomputed valid winner")
    protocol_sha = preregistered.get("protocol_sha256")
    manifest_sha = preregistered.get("prediction_manifest_sha256")
    base_config_sha = protocol.get("base_config_sha256")
    expected_run_contract = {
        "protocol_sha256": protocol_sha,
        "prediction_manifest_sha256": manifest_sha,
        "base_config_sha256": base_config_sha,
    }
    if valid.get("protocol_sha256") != protocol_sha:
        raise ValueError("valid protocol SHA differs from preregistration")
    if valid.get("run_contract") != expected_run_contract:
        raise ValueError("valid run contract differs from preregistration")
    if test.get("run_contract") != expected_run_contract:
        raise ValueError("test run contract differs from preregistration")
    base_config_text = protocol.get("base_config")
    if not base_config_text or not base_config_sha:
        raise ValueError("protocol lacks frozen base config identity")
    base_config_path = Path(str(base_config_text)).expanduser()
    if not base_config_path.is_absolute():
        base_config_path = REPO_ROOT / base_config_path
    if not base_config_path.is_file() or sha256_file(base_config_path) != base_config_sha:
        raise ValueError("frozen base config SHA differs from protocol")
    base_config = load_config(str(base_config_path))
    artifacts = preregistered.get("prediction_artifacts") or []
    artifact_map = {
        (item.get("model_ref"), item.get("pool"), item.get("segment")): item
        for item in artifacts
    }
    expected_artifacts = {
        ("b6-m", "csi1000", "valid"),
        ("b6-m", "csi1000", "test"),
        ("b6-m", "csi300", "test"),
        ("b6-m", "csi500", "test"),
    }
    if len(artifacts) != 4 or set(artifact_map) != expected_artifacts:
        raise ValueError("prediction artifact contract is incomplete or duplicated")
    valid_prediction_sha = artifact_map[("b6-m", "csi1000", "valid")].get(
        "prediction_sha256"
    )
    grid_by_id = {str(item["candidate_id"]): item for item in grid}
    for row in valid_rows:
        candidate = grid_by_id[str(row["candidate_id"])]
        if row.get("status") != "success":
            raise ValueError("valid grid contains unsuccessful row")
        if row.get("source_pred_sha256") != valid_prediction_sha:
            raise ValueError("valid row prediction identity differs")
        expected_config_sha = effective_config_sha256(
            base_config, candidate, pool="csi1000", segment="valid"
        )
        if row.get("effective_config_sha256") != expected_config_sha:
            raise ValueError("valid row effective config identity differs")
    if test.get("state") != "test_complete":
        raise ValueError("test results are incomplete")
    if test.get("winner", {}).get("candidate_id") != winner.get("candidate_id"):
        raise ValueError("test winner differs from frozen valid winner")
    pools = test.get("pools") or {}
    if set(pools) != {"csi1000", "csi300", "csi500"}:
        raise ValueError("winner test pools are incomplete")
    if any(row.get("status") != "success" for row in pools.values()):
        raise ValueError("winner test contains unsuccessful pool")
    for pool, row in pools.items():
        if row.get("candidate_id") != winner.get("candidate_id"):
            raise ValueError(f"{pool} test candidate differs from valid winner")
        expected_prediction_sha = artifact_map[("b6-m", pool, "test")].get(
            "prediction_sha256"
        )
        if row.get("source_pred_sha256") != expected_prediction_sha:
            raise ValueError(f"{pool} test prediction identity differs")
        expected_config_sha = effective_config_sha256(
            base_config, grid_by_id[str(winner["candidate_id"])], pool=pool, segment="test"
        )
        if row.get("effective_config_sha256") != expected_config_sha:
            raise ValueError(f"{pool} test effective config identity differs")
    selected = next(
        item
        for item in protocol["strategy_grid"]
        if item["candidate_id"] == winner["candidate_id"]
    )
    metrics_summary = {
        pool: {
            "ir": row["excess_with_cost_information_ratio"],
            "ann": row["excess_with_cost_annualized_return"],
            "mdd": row["excess_with_cost_max_drawdown"],
            "turnover": row["annualized_one_way_turnover"],
        }
        for pool, row in pools.items()
    }
    portable_pools = _portable_artifact_paths(pools)
    result_dirs = [
        row["result_dir"] for row in portable_pools.values() if row.get("result_dir")
    ]
    out = copy.deepcopy(preregistered)
    out.update(
        state="test_complete",
        conclusion="candidate_complete",
        selected_candidate_id=winner["candidate_id"],
        selected_strategy=selected,
        valid_winner_metrics={
            "neighbor_ir_p25": winner["neighbor_ir_p25"],
            "ir": winner["excess_with_cost_information_ratio"],
            "ann": winner["excess_with_cost_annualized_return"],
            "mdd": winner["excess_with_cost_max_drawdown"],
            "turnover": winner["annualized_one_way_turnover"],
        },
        valid_result_path=_path_text(valid_path),
        valid_result_sha256=sha256_file(valid_path),
        test_result_path=_path_text(test_path),
        test_result_sha256=sha256_file(test_path),
        detailed_report=_path_text(report_path),
        metrics_summary=metrics_summary,
        test_results=portable_pools,
        result_dirs=result_dirs,
        note=(
            "仅用 CSI1000 valid 的轴向邻域 IR 25% 分位冻结胜者；test 三池仅运行该胜者，"
            "B2-S baseline test 复用既有登记结果；不自动提升 baseline。"
        ),
    )
    return out


def load_registry(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if path.is_file() else []


def upsert_registry_transition(
    registry: Path,
    row: dict[str, Any],
    *,
    expected_previous_state: Optional[str],
) -> None:
    lines = registry.read_text(encoding="utf-8").splitlines(keepends=True) if registry.is_file() else []
    parsed = [(index, json.loads(line)) for index, line in enumerate(lines) if line.strip()]
    matches = [
        (index, current)
        for index, current in parsed
        if current.get("exp_id") == row.get("exp_id")
    ]
    if len(matches) > 1:
        raise ValueError(f"duplicate registry exp_id: {row.get('exp_id')}")
    serialized = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    if matches:
        index, previous = matches[0]
        if previous.get("state") != expected_previous_state:
            raise ValueError(
                f"expected previous state {expected_previous_state!r}, found {previous.get('state')!r}"
            )
        lines[index] = serialized
    else:
        if expected_previous_state is not None:
            raise ValueError("expected previous state but experiment row is absent")
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.append(serialized)
    registry.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry.with_name(registry.name + ".tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    temporary.replace(registry)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _fmt(value: Any, percent: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number):
        return "—"
    return f"{number * 100:.2f}%" if percent else f"{number:.3f}"


def _metric_headers(include_neighbor: bool = False) -> str:
    prefix = "<th>邻域IR P25</th>" if include_neighbor else ""
    return prefix + "".join(f"<th>{label}</th>" for _, label, _ in METRICS)


def _metric_cells(row: dict[str, Any], include_neighbor: bool = False) -> str:
    prefix = (
        f'<td class="num">{_fmt(row.get("neighbor_ir_p25"))}</td>'
        if include_neighbor else ""
    )
    return prefix + "".join(
        f'<td class="num">{_fmt(row.get(key), percent)}</td>'
        for key, _, percent in METRICS
    )


def build_html(
    baseline: dict[str, Any],
    protocol: dict[str, Any],
    valid: dict[str, Any],
    test: dict[str, Any],
) -> str:
    baseline_rows = []
    for pool in ("csi1000", "csi300", "csi500"):
        row = (baseline.get("test_results") or {}).get(pool) or {}
        baseline_rows.append(
            f"<tr><td>{_esc(baseline.get('baseline_ref'))}</td>"
            f"<td>{_esc(baseline.get('frozen_model_ref'))}</td>"
            f"<td>{_esc((baseline.get('strategy') or {}).get('candidate_id'))}</td>"
            f"<td>{pool.upper()}</td>{_metric_cells(row)}</tr>"
        )
    winner = valid.get("winner") or {}
    winner_rows = []
    for pool in ("csi1000", "csi300", "csi500"):
        row = (test.get("pools") or {}).get(pool) or {}
        winner_rows.append(
            f"<tr><td>{_esc(winner.get('candidate_id'))}</td><td>{pool.upper()}</td>"
            f"{_metric_cells(row)}</tr>"
        )
    ranked = sorted(
        valid.get("all_rows") or [],
        key=lambda row: (
            -(float(row["neighbor_ir_p25"]) if row.get("neighbor_ir_p25") is not None else -math.inf),
            str(row.get("candidate_id")),
        ),
    )[:50]
    ranked_rows = "".join(
        f"<tr><td>{index}</td><td>{_esc(row.get('candidate_id'))}</td>"
        f"{_metric_cells(row, include_neighbor=True)}</tr>"
        for index, row in enumerate(ranked, 1)
    )
    css = """
body{font-family:-apple-system,'PingFang SC',sans-serif;max-width:1500px;margin:24px auto;color:#172033;background:#f7f8fa}
h1{font-size:24px}h2{margin-top:32px;border-bottom:2px solid #355f9d;padding-bottom:7px}.note{color:#596579;font-size:13px}
table{border-collapse:collapse;width:100%;background:#fff;margin:10px 0 24px;font-size:12px}th,td{border:1px solid #dfe4eb;padding:6px 8px}th{background:#edf2f8}td.num{text-align:right;font-variant-numeric:tabular-nums}table.baseline tbody{background:#fff7dc}
"""
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>B2-S 邻域超参实验</title><style>{css}</style></head><body>"
        '<h1>B2-S 邻域超参实验</h1>'
        f'<p class="note">生成时间：{datetime.now().isoformat(timespec="seconds")}；'
        '仅 CSI1000 valid 选型，test 不参与参数选择。</p>'
        '<section id="baseline"><h2>当前策略 Baseline</h2><table class="baseline">'
        '<thead><tr><th>Baseline</th><th>冻结模型</th><th>策略</th><th>测试池</th>'
        f"{_metric_headers()}</tr></thead><tbody>{''.join(baseline_rows)}</tbody></table></section>"
        '<section id="winner"><h2>冻结胜者 Test</h2><table class="winner">'
        f"<thead><tr><th>候选</th><th>测试池</th>{_metric_headers()}</tr></thead>"
        f"<tbody>{''.join(winner_rows)}</tbody></table></section>"
        '<section id="valid-ranking"><h2>Valid 稳健排名 Top 50</h2>'
        f'<p class="note">总候选：{len(protocol.get("strategy_grid") or [])}；主排序为轴向邻域 IR 25% 分位。</p>'
        '<table class="valid-ranking"><thead><tr><th>排名</th><th>候选</th>'
        f"{_metric_headers(include_neighbor=True)}</tr></thead><tbody>{ranked_rows}</tbody></table></section>"
        "</body></html>"
    )


def _unique_row(rows: Sequence[dict[str, Any]], exp_id: str) -> dict[str, Any]:
    matches = [row for row in rows if row.get("exp_id") == exp_id]
    if len(matches) != 1:
        raise ValueError(f"registry requires exactly one {exp_id} row")
    return matches[0]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preregister", "finalize"))
    parser.add_argument("--protocol", type=Path, default=DEFAULT_ROOT / "protocol.json")
    parser.add_argument("--prediction-manifest", type=Path, default=DEFAULT_ROOT / "prediction_manifest.json")
    parser.add_argument("--valid-results", type=Path, default=DEFAULT_ROOT / "valid_results.json")
    parser.add_argument("--test-results", type=Path, default=DEFAULT_ROOT / "test_results.json")
    parser.add_argument("--configs-dir", type=Path, default=DEFAULT_CONFIGS)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    manifest = json.loads(args.prediction_manifest.read_text(encoding="utf-8"))
    if args.mode == "preregister":
        row = build_preregistered_row(
            protocol,
            manifest,
            protocol_path=args.protocol,
            prediction_manifest_path=args.prediction_manifest,
            configs_dir=args.configs_dir,
        )
        upsert_registry_transition(args.registry, row, expected_previous_state=None)
        print(f"preregistered: {EXP_ID}")
        return
    rows = load_registry(args.registry)
    preregistered = _unique_row(rows, EXP_ID)
    baseline = _unique_row(rows, "baseline/b2-s-on-b6-m")
    valid = json.loads(args.valid_results.read_text(encoding="utf-8"))
    test = json.loads(args.test_results.read_text(encoding="utf-8"))
    report = build_html(baseline, protocol, valid, test)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    complete = build_complete_row(
        preregistered,
        protocol,
        valid,
        test,
        valid_path=args.valid_results,
        test_path=args.test_results,
        report_path=args.output,
    )
    upsert_registry_transition(
        args.registry, complete, expected_previous_state="preregistered"
    )
    print(f"finalized: {EXP_ID} -> {args.output}")


if __name__ == "__main__":
    main()
