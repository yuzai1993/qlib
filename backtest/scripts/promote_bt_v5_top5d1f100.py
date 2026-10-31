"""Replay the approved BT v5 promotion without training or changing live state.

Read historical artifacts from --source-root; write only to --repo-root.
The checked-in summaries and NAV curves keep reports usable without mlruns/result.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

import sys

import pandas as pd

if __package__:
    from .research_layout import resolve_research_path as _resolve_research_path
else:
    from research_layout import resolve_research_path as _resolve_research_path

_RESEARCH_ROOT = Path(__file__).resolve().parents[2]

if __package__:
    from .experiment_registry import (
        load_registry as _load_registry_rows,
        write_registry as _write_registry_rows,
    )
else:
    from experiment_registry import (
        load_registry as _load_registry_rows,
        write_registry as _write_registry_rows,
    )

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backtest.scripts.strategy_stability_metrics import load_regime_labels, summarize_stability  # noqa: E402

CANDIDATE = "regime-adapt/m0h20es-all-top5d1f100-bt"
BASELINE = "baseline/phase-m-v1-bt-v5"
MODEL = "regime-adapt/m0-h20-t5h5-es-v1"
SEEDS = [42, 1000, 2000, 3000, 4000]
SUMMARY_DIR = Path("backtest/experiments/phase_m_v1_bt_metrics")
LABELS = Path("backtest/experiments/strategies/baseline/phase-m-v1-bt-v5/configs/monthly_regime_labels_eval_window_v1.csv")
LIMITATION = (
    "历史选型窗口：2020-08-03~2026-07-31 已用于模型早停及策略比较，"
    "不是独立样本外检验。100万历史成本口径；30万当前费用与新增期间结果另作诊断，"
    "不用于调参，不代表实盘已切换。"
)


def fingerprint(path: Path) -> dict:
    actual_path = _resolve_research_path(path, _RESEARCH_ROOT)
    digest = hashlib.sha256()
    with actual_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": actual_path.stat().st_size}


def validate_candidate(source_root: Path, candidate: dict) -> dict:
    if candidate.get("seeds") != SEEDS:
        raise ValueError("BT v5 requires the five approved seeds")
    source_doc = json.loads((_resolve_research_path(source_root / candidate["eval_output"], _RESEARCH_ROOT)).read_text())
    ensemble = source_doc.get("ensemble")
    if not ensemble:
        raise ValueError("official metrics must come from ensemble")
    meta_path = source_root / ensemble["session_dir"] / "meta.json"
    meta = json.loads(_resolve_research_path(meta_path, _RESEARCH_ROOT).read_text())
    expected = {"class": "TopkDropoutStrategy", "topk": 5, "n_drop": 1,
                "hold_thresh": 1, "force_sell_rank": 100, "risk_degree": 0.9,
                "only_tradable": False, "forbid_all_trade_at_limit": False}
    if any(meta.get("strategy", {}).get(k) != v for k, v in expected.items()):
        raise ValueError("candidate strategy differs from the approved BT v5")
    bt = meta["backtest"]
    if (bt.get("account"), bt.get("start_time"), bt.get("end_time")) != (
        1000000, "2020-08-03", "2026-07-31"
    ):
        raise ValueError("candidate must retain the original account and historical window")
    fees = {"deal_price": "close", "limit_threshold": "market_cn", "open_cost": 0.00021,
            "close_cost": 0.00071, "min_cost": 5.0, "trade_unit": 100}
    if any(bt.get("exchange_kwargs", {}).get(k) != v for k, v in fees.items()):
        raise ValueError("candidate historical cost basis changed")
    pred = Path(meta["source_pred"])
    if not pred.is_absolute():
        pred = source_root / pred
    pred_fingerprint = fingerprint(pred)
    if pred_fingerprint["sha256"] != meta.get("source_pred_sha256"):
        raise ValueError("candidate ensemble prediction hash mismatch")
    return {"run_meta": fingerprint(meta_path), "prediction": pred_fingerprint,
            "backtest": bt, "strategy": meta["strategy"]}


def snapshot_document(source_root: Path, rec: dict, labels: pd.Series, labels_info: dict) -> tuple[Path, dict]:
    rel = Path(rec["eval_output"])
    path = source_root / rel
    doc = json.loads(_resolve_research_path(path, _RESEARCH_ROOT).read_text())
    ensemble = doc.get("ensemble")
    if not ensemble:
        raise ValueError(f"missing official ensemble: {path}")
    csv = source_root / ensemble["session_dir"] / "run_01/report_normal.csv"
    returns = pd.read_csv(csv, index_col=0, parse_dates=True)
    fresh = summarize_stability(returns, regime_labels=labels)
    for key, expected in ensemble["full_period"].items():
        actual = fresh["full_period"].get(key)
        if expected is not None and isinstance(expected, (int, float)):
            if actual is None or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-11):
                raise ValueError(f"saved metrics disagree with original returns: {path}: {key}")
    labeled_days = sum(r["n_days"] for r in fresh["regimes"].values())
    evaluation_mode = rec.get("evaluation_mode") or "historical_selection_window"
    if labeled_days != len(returns) and evaluation_mode != "extended_window_diagnostic":
        raise ValueError(f"regime labels do not cover the complete report: {csv}")
    destination = SUMMARY_DIR / f"{rel.parent.name}_{rel.stem}.json"
    net = (1.0 + returns["return"] - returns["cost"]).cumprod()
    bench = (1.0 + returns["bench"]).cumprod()
    portable = {**copy.deepcopy(ensemble), **fresh}
    portable["nav_curve"] = [
        [str(date.date()), float(value), float(benchmark)]
        for date, value, benchmark in zip(returns.index, net, bench)
    ]
    portable["portable_report"] = str(destination.with_suffix(".html"))
    return destination, {
        "schema_version": 1,
        "ensemble": portable,
        "evaluation_mode": evaluation_mode,
        "regime_coverage": {"total_days": len(returns), "labeled_days": labeled_days,
                            "unlabeled_days": len(returns) - labeled_days},
        "selection_limitation": LIMITATION,
        "provenance": {"eval_json": fingerprint(path), "report_csv": fingerprint(csv),
                       "regime_labels": labels_info, "metrics_function": "strategy_stability_metrics.summarize_stability"},
    }


def promote(*, repo_root: Path = ROOT, source_root: Path) -> dict:
    """Validate everything first, then materialize portable data and update registry."""
    repo_root, source_root = Path(repo_root).resolve(), Path(source_root).resolve()
    if repo_root == source_root:
        raise ValueError("source-root must remain read-only and separate from repo-root")
    registry = repo_root / "backtest/experiments/registry.jsonl"
    rows = _load_registry_rows(registry)
    ids = [r["exp_id"] for r in rows]
    if ids.count(CANDIDATE) != 1 or ids.count(BASELINE) > 1:
        raise ValueError("candidate and promoted baseline IDs must be unambiguous")
    candidate = next(r for r in rows if r["exp_id"] == CANDIDATE)
    provenance = validate_candidate(source_root, candidate)
    label_path = _resolve_research_path(repo_root / LABELS, repo_root)
    labels = load_regime_labels(label_path)
    labels_info = {**fingerprint(label_path), "path": str(LABELS)}
    snapshots = {}
    for row in rows:
        if row.get("report_kind") not in {"phase_m_v1_bt", "phase_m_v1_bt_baseline"} or row["exp_id"] == BASELINE:
            continue
        # Newly added offline diagnostics can already point to their own durable outputs.
        if str(row["eval_output"]).startswith("backtest/experiments/") and (_resolve_research_path(repo_root / row["eval_output"], _RESEARCH_ROOT)).is_file():
            continue
        destination, doc = snapshot_document(source_root, row, labels, labels_info)
        snapshots[str(destination)] = doc
        row["portable_eval_output"] = str(destination)
    official_path = candidate["portable_eval_output"]
    official = snapshots[official_path]
    official["provenance"].update(provenance)
    official["model_ref"] = MODEL
    official["model_baseline_version"] = "v2"
    official["seeds"] = SEEDS
    official["ensemble"]["method"] = "daily_zscore_mean"
    official["ensemble"]["source_pred"] = provenance["prediction"]["path"]
    for row in rows:
        if row.get("report_kind") == "phase_m_v1_bt_baseline":
            row["current_bt"] = False
    baseline = {
        "exp_id": BASELINE, "direction": "phase-m-v1-bt", "phase": "S", "date": "2026-10-29",
        "state": "completed", "report_kind": "phase_m_v1_bt_baseline", "bt_version": "v5", "current_bt": True,
        "display_name": "v2 M0 H20 ES top5d1 + 掉出前100必卖（hold=1）", "arm": "m0h20es", "seeds": SEEDS,
        "model_ref": MODEL, "model_baseline_version": "v2", "baseline_ref": "self", "promoted_from": CANDIDATE,
        "hypothesis": "用户于2026-10-29批准将现有五种子合成信号的 top5d1/hold1/f100 历史回测提升为 BT v5；模型研究基线仍为 v4。",
        "eval_protocol": candidate["eval_protocol"], "eval_output": official_path,
        "source_eval_output": candidate["eval_output"], "metrics_source": "ensemble",
        "metrics": copy.deepcopy(official["ensemble"]["full_period"]),
        "ensemble_session": official["ensemble"]["session_dir"],
        "evaluation_mode": "historical_selection_window", "note": LIMITATION,
        "detail_report": "backtest/experiments/strategies/baseline/phase-m-v1-bt-v5/reports/phase_m_v1_bt_v5.html",
        "comparison_baseline_ref": "baseline/phase-m-v1-bt-v4",
        "provenance": copy.deepcopy(official["provenance"]),
    }
    existing = next((i for i, row in enumerate(rows) if row["exp_id"] == BASELINE), None)
    if existing is None:
        rows.append(baseline)
    else:
        rows[existing] = baseline
    # Do not rewrite historical conclusions or baseline references.
    json.dumps(rows, allow_nan=False)
    for destination, doc in snapshots.items():
        output = _resolve_research_path(repo_root / destination, repo_root)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    _write_registry_rows(registry, rows)
    return {"baseline": BASELINE, "eval_output": official_path, "portable_documents": len(snapshots)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(promote(repo_root=args.repo_root, source_root=args.source_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
