"""Pure contracts for the B1-M/B6-M Phase S strategy experiment."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

MODEL_REFS = ("b1-m", "b6-m")
CURRENT_MODEL_REFS = ("b6-m",)
BASELINE_CANDIDATE_ID = "topk-t10-d2-h1"
CURRENT_STRATEGY_BASELINE_ID = "topk-t22-d2-h2"
VALID_SEGMENT = ("2020-01-13", "2021-07-15")
TEST_SEGMENT = ("2021-07-16", "2026-07-31")
# The only active Phase S selection interval.  The split segments remain for
# reproducing and auditing historical valid/test sweeps.
FULL_SEGMENT = ("2020-01-13", "2026-07-31")
POOL_BENCHMARKS = {
    "csi1000": "SH000852",
    "csi300": "SH000300",
    "csi500": "SH000905",
}
ACCOUNT = 500_000
RISK_DEGREE = 0.90
EXCHANGE_KWARGS = {
    "freq": "day",
    "deal_price": "close",
    "limit_threshold": 0.095,
    "open_cost": 0.00021,
    "close_cost": 0.00071,
    "min_cost": 5.0,
    "trade_unit": 100,
}
SELECTION_METRICS = (
    "excess_with_cost_information_ratio",
    "excess_with_cost_annualized_return",
    "excess_with_cost_max_drawdown",
    "annualized_one_way_turnover",
)


@dataclass(frozen=True)
class FrozenModel:
    model_ref: str
    manifest_path: Path
    model_path: Path
    model_sha256: str
    source_config: Path
    manifest: dict[str, Any]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_path(repo_root: Path, raw: Any, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty repository-relative path")
    root = repo_root.resolve()
    candidate = (root / raw).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"{label} must stay inside repository: {raw}")
    return candidate


def load_frozen_model(repo_root: Path, model_ref: str) -> FrozenModel:
    """Load and validate the only legal Phase S model artifact for a baseline."""
    if model_ref not in MODEL_REFS:
        raise ValueError(f"unsupported Phase S model_ref: {model_ref}")
    root = Path(repo_root).resolve()
    baseline_dir = (root / "backtest" / "models" / "baselines" / model_ref).resolve()
    manifest_path = baseline_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"frozen model manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("baseline_exp_id") != f"baseline/{model_ref}":
        raise ValueError(
            f"baseline_exp_id mismatch for {model_ref}: {manifest.get('baseline_exp_id')}"
        )

    retained = manifest.get("retained_model") or manifest.get("model") or {}
    model_path = _repo_path(root, retained.get("path"), "model.path")
    if not model_path.is_relative_to(baseline_dir):
        raise ValueError(
            f"retained model must stay inside baseline directory: {baseline_dir}"
        )
    if not model_path.is_file():
        raise FileNotFoundError(f"retained model missing: {model_path}")
    expected_size = retained.get("size_bytes")
    if not isinstance(expected_size, int) or model_path.stat().st_size != expected_size:
        raise ValueError(
            f"model size mismatch for {model_ref}: expected={expected_size}, "
            f"actual={model_path.stat().st_size}"
        )
    expected_sha = retained.get("sha256")
    actual_sha = sha256_file(model_path)
    if not isinstance(expected_sha, str) or actual_sha != expected_sha:
        raise ValueError(
            f"model sha mismatch for {model_ref}: expected={expected_sha}, actual={actual_sha}"
        )

    config_meta = manifest.get("config") or {}
    config_raw = config_meta.get("path") or (manifest.get("source") or {}).get(
        "config"
    )
    source_config = _repo_path(root, config_raw, "config.path")
    if not source_config.is_file():
        raise FileNotFoundError(f"source config missing: {source_config}")
    expected_config_sha = config_meta.get("sha256")
    if expected_config_sha and sha256_file(source_config) != expected_config_sha:
        raise ValueError(f"config sha mismatch for {model_ref}: {source_config}")
    return FrozenModel(
        model_ref=model_ref,
        manifest_path=manifest_path,
        model_path=model_path,
        model_sha256=actual_sha,
        source_config=source_config,
        manifest=manifest,
    )


def _topk_candidate(topk: int, n_drop: int, hold_thresh: int) -> dict[str, Any]:
    return {
        "candidate_id": f"topk-t{topk}-d{n_drop}-h{hold_thresh}",
        "strategy_class": "TopkDropoutStrategy",
        "topk": topk,
        "n_drop": n_drop,
        "hold_thresh": hold_thresh,
    }


def _soft_candidate(topk: int, impact_ratio: float) -> dict[str, Any]:
    ratio_label = int(round(impact_ratio * 100))
    return {
        "candidate_id": f"soft-t{topk}-i{ratio_label:03d}",
        "strategy_class": "SoftTopkStrategy",
        "topk": topk,
        "impact_ratio": impact_ratio,
        "trade_impact_limit": RISK_DEGREE / topk * impact_ratio,
        "risk_degree": RISK_DEGREE,
    }


def strategy_grid(model_ref: str) -> list[dict[str, Any]]:
    """Return the immutable, model-aware valid candidate grid."""
    if model_ref == "b1-m":
        rows = [
            _topk_candidate(topk, n_drop, hold)
            for topk, drops in ((10, (1, 2)), (20, (2, 4)), (30, (3, 6)))
            for n_drop in drops
            for hold in (1, 3)
        ]
        rows.extend(
            _soft_candidate(topk, impact)
            for topk in (10, 20, 30)
            for impact in (0.50, 1.00)
        )
        rows.sort(
            key=lambda row: row["candidate_id"] != BASELINE_CANDIDATE_ID
        )
    elif model_ref == "b6-m":
        rows = [_topk_candidate(10, 2, 1)]
        rows.extend(
            _topk_candidate(topk, n_drop, hold)
            for topk, drops in ((10, (1,)), (20, (1, 2)), (30, (2, 3)))
            for n_drop in drops
            for hold in (5, 10, 20)
        )
        rows.extend(
            _soft_candidate(topk, impact)
            for topk in (10, 20, 30)
            for impact in (0.25, 0.50)
        )
    else:
        raise ValueError(f"unsupported Phase S model_ref: {model_ref}")
    if len({row["candidate_id"] for row in rows}) != len(rows):
        raise AssertionError(f"duplicate strategy candidate for {model_ref}")
    return rows


def _finite_metric(row: dict[str, Any], key: str) -> float | None:
    try:
        value = float(row.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def select_strategy_winner(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Select by preregistered IR, annualized return, MDD, turnover, then ID."""
    eligible: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for row in rows:
        if row.get("status") != "success":
            continue
        values = [_finite_metric(row, key) for key in SELECTION_METRICS]
        if any(value is None for value in values):
            continue
        ir, ann, mdd, turnover = (float(value) for value in values)
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        eligible.append(((-ir, -ann, -mdd, turnover, candidate_id), row))
    if not eligible:
        raise ValueError("no successful candidate has all finite selection metrics")
    return dict(min(eligible, key=lambda item: item[0])[1])


def select_valid_winner(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Historical valid-segment compatibility wrapper for strategy selection."""
    return select_strategy_winner(rows)
