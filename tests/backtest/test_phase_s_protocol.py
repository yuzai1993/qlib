from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import phase_s_protocol as protocol  # noqa: E402


def _write_frozen_repo(tmp_path: Path, model_ref: str = "b1-m") -> tuple[Path, Path, str]:
    baseline = tmp_path / "backtest" / "models" / "baselines" / model_ref
    model = baseline / "seed2000" / "trained_model"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"frozen-model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    config = tmp_path / "backtest" / "configs" / f"{model_ref}.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("run: {mode: train_only}\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "baseline_exp_id": f"baseline/{model_ref}",
        "source": {"config": str(config.relative_to(tmp_path))},
        "retained_model": {
            "path": str(model.relative_to(tmp_path)),
            "sha256": digest,
            "size_bytes": model.stat().st_size,
        },
    }
    (baseline / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path, model, digest


def test_load_frozen_model_verifies_manifest_path_size_and_sha(tmp_path):
    repo, model, digest = _write_frozen_repo(tmp_path)

    frozen = protocol.load_frozen_model(repo, "b1-m")

    assert frozen.model_ref == "b1-m"
    assert frozen.model_path == model.resolve()
    assert frozen.model_sha256 == digest
    assert frozen.source_config == (repo / "backtest/configs/b1-m.yaml").resolve()


def test_load_frozen_model_accepts_model_and_config_schema_used_by_b6(tmp_path):
    baseline = tmp_path / "backtest/models/baselines/b6-m"
    model = baseline / "seed4000/trained_model"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"b6-model")
    config = tmp_path / "backtest/configs/b6.yaml"
    config.parent.mkdir(parents=True)
    config.write_bytes(b"run: {mode: train_only}\n")
    manifest = {
        "baseline_exp_id": "baseline/b6-m",
        "model": {
            "path": str(model.relative_to(tmp_path)),
            "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
            "size_bytes": model.stat().st_size,
        },
        "config": {
            "path": str(config.relative_to(tmp_path)),
            "sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        },
    }
    (baseline / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    frozen = protocol.load_frozen_model(tmp_path, "b6-m")

    assert frozen.model_path == model.resolve()
    assert frozen.source_config == config.resolve()


def test_load_frozen_model_rejects_retained_path_outside_model_baseline(tmp_path):
    repo, _model, _digest = _write_frozen_repo(tmp_path, "b6-m")
    manifest_path = repo / "backtest/models/baselines/b6-m/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outside = repo / "elsewhere" / "trained_model"
    outside.parent.mkdir()
    outside.write_bytes(b"outside")
    manifest["retained_model"] = {
        "path": str(outside.relative_to(repo)),
        "sha256": hashlib.sha256(b"outside").hexdigest(),
        "size_bytes": len(b"outside"),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="inside baseline directory"):
        protocol.load_frozen_model(repo, "b6-m")


@pytest.mark.parametrize("mutation", ["size", "sha"])
def test_load_frozen_model_rejects_tampered_model(tmp_path, mutation):
    repo, _model, _digest = _write_frozen_repo(tmp_path)
    manifest_path = repo / "backtest/models/baselines/b1-m/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "size":
        manifest["retained_model"]["size_bytes"] += 1
    else:
        manifest["retained_model"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=mutation):
        protocol.load_frozen_model(repo, "b1-m")


def test_b1_grid_has_exact_unique_candidates_and_live_baseline():
    rows = protocol.strategy_grid("b1-m")

    assert len(rows) == 18
    assert len({row["candidate_id"] for row in rows}) == 18
    baseline = next(row for row in rows if row["candidate_id"] == protocol.BASELINE_CANDIDATE_ID)
    assert baseline == {
        "candidate_id": "topk-t10-d2-h1",
        "strategy_class": "TopkDropoutStrategy",
        "topk": 10,
        "n_drop": 2,
        "hold_thresh": 1,
    }
    assert {
        (row["topk"], row["n_drop"], row["hold_thresh"])
        for row in rows
        if row["strategy_class"] == "TopkDropoutStrategy"
    } == {
        (topk, n_drop, hold)
        for topk, drops in ((10, (1, 2)), (20, (2, 4)), (30, (3, 6)))
        for n_drop in drops
        for hold in (1, 3)
    }


def test_b6_grid_has_exact_unique_low_turnover_candidates():
    rows = protocol.strategy_grid("b6-m")

    assert len(rows) == 22
    assert len({row["candidate_id"] for row in rows}) == 22
    topk_rows = [row for row in rows if row["strategy_class"] == "TopkDropoutStrategy"]
    assert len(topk_rows) == 16
    assert protocol.BASELINE_CANDIDATE_ID in {row["candidate_id"] for row in rows}
    assert {
        (row["topk"], row["n_drop"], row["hold_thresh"])
        for row in topk_rows
        if row["candidate_id"] != protocol.BASELINE_CANDIDATE_ID
    } == {
        (topk, n_drop, hold)
        for topk, drops in ((10, (1,)), (20, (1, 2)), (30, (2, 3)))
        for n_drop in drops
        for hold in (5, 10, 20)
    }


@pytest.mark.parametrize(
    ("model_ref", "candidate_id", "expected"),
    [
        ("b1-m", "soft-t10-i050", 0.0475),
        ("b1-m", "soft-t30-i100", 0.95 / 30),
        ("b6-m", "soft-t10-i025", 0.02375),
        ("b6-m", "soft-t20-i050", 0.02375),
    ],
)
def test_soft_topk_grid_precomputes_absolute_impact_limits(model_ref, candidate_id, expected):
    row = next(row for row in protocol.strategy_grid(model_ref) if row["candidate_id"] == candidate_id)
    assert row["trade_impact_limit"] == pytest.approx(expected)
    assert row["risk_degree"] == 0.95


def _metric_row(candidate_id: str, ir=1.0, ann=0.2, mdd=-0.1, turnover=12.0):
    return {
        "candidate_id": candidate_id,
        "status": "success",
        "excess_with_cost_information_ratio": ir,
        "excess_with_cost_annualized_return": ann,
        "excess_with_cost_max_drawdown": mdd,
        "annualized_one_way_turnover": turnover,
    }


@pytest.mark.parametrize(
    "rows, expected",
    [
        ([_metric_row("a", ir=1.1), _metric_row("b", ir=1.0)], "a"),
        ([_metric_row("a", ann=0.21), _metric_row("b", ann=0.20)], "a"),
        ([_metric_row("a", mdd=-0.09), _metric_row("b", mdd=-0.10)], "a"),
        ([_metric_row("a", turnover=11.0), _metric_row("b", turnover=12.0)], "a"),
        ([_metric_row("candidate-b"), _metric_row("candidate-a")], "candidate-a"),
    ],
)
def test_select_valid_winner_uses_preregistered_tie_break_order(rows, expected):
    assert protocol.select_valid_winner(rows)["candidate_id"] == expected


@pytest.mark.parametrize("bad_value", [None, math.nan, math.inf, -math.inf])
def test_select_valid_winner_rejects_missing_or_non_finite_metrics(bad_value):
    row = _metric_row("broken")
    row["excess_with_cost_information_ratio"] = bad_value

    with pytest.raises(ValueError, match="no successful candidate"):
        protocol.select_valid_winner([row])
