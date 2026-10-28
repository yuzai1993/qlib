from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.scripts.strategy_stability_metrics import summarize_stability


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def promotion_fixture(tmp_path):
    repo, source = tmp_path / "repo", tmp_path / "source"
    exp = repo / "backtest/experiments"
    exp.mkdir(parents=True)
    labels = repo / "backtest/configs/regime-adapt/monthly_regime_labels_eval_window_v1.csv"
    labels.parent.mkdir(parents=True)
    labels.write_text("datetime,regime3\n2020-08-01,D\n2024-02-01,F\n2026-07-01,T\n")
    returns = pd.DataFrame(
        {"return": [0.01, -0.02, 0.03, 0.02], "cost": [0.001] * 4,
         "bench": [0.005, -0.01, 0.015, 0.01], "turnover": [0.2] * 4},
        index=pd.to_datetime(["2020-08-03", "2020-08-04", "2024-02-01", "2026-07-31"]),
    )
    session = "backtest/result/candidate"
    csv = source / session / "run_01/report_normal.csv"
    csv.parent.mkdir(parents=True)
    returns.to_csv(csv)
    summary = summarize_stability(returns)
    summary["session_dir"] = session
    summary["session"] = "candidate"
    _write_json(source / "backtest/result/candidate.json", {"ensemble": summary})
    pred = source / "backtest/result/ensemble.pkl"
    pred.write_bytes(b"immutable-prediction-fixture")
    meta = {
        "source_pred": str(pred),
        "source_pred_sha256": hashlib.sha256(pred.read_bytes()).hexdigest(),
        "strategy": {"class": "TopkDropoutStrategy", "topk": 5, "n_drop": 1,
                     "hold_thresh": 1, "force_sell_rank": 100, "risk_degree": 0.9,
                     "only_tradable": False, "forbid_all_trade_at_limit": False},
        "backtest": {"account": 1000000, "start_time": "2020-08-03", "end_time": "2026-07-31",
                     "exchange_kwargs": {"deal_price": "close", "limit_threshold": "market_cn",
                                         "open_cost": 0.00021, "close_cost": 0.00071,
                                         "min_cost": 5.0, "trade_unit": 100}},
    }
    _write_json(source / session / "meta.json", meta)
    rows = [
        {"exp_id": "model-v4", "phase_m_protocol": "v1", "baseline_version": "v4", "metrics": {"keep": 9}},
        {"exp_id": "baseline/phase-m-v1-bt-v4", "report_kind": "phase_m_v1_bt_baseline",
         "bt_version": "v4", "current_bt": True, "display_name": "BT v4", "metrics": summary["full_period"],
         "eval_output": "backtest/result/candidate.json"},
        {"exp_id": "regime-adapt/m0h20es-all-top5d1f100-bt", "report_kind": "phase_m_v1_bt",
         "display_name": "M0 H20 ES top5d1 + f100", "seeds": [42, 1000, 2000, 3000, 4000],
         "state": "completed", "eval_output": "backtest/result/candidate.json", "baseline_ref": "baseline/phase-m-v1-bt-v3",
         "detail_report": "backtest/experiments/candidate.html", "eval_protocol": "ensemble | top5d1 f100",
         "metrics": summary["full_period"]},
    ]
    registry = exp / "registry.jsonl"
    registry.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return repo, source, rows


def test_promotion_preserves_history_and_model_baseline_and_is_idempotent(promotion_fixture):
    promote = importlib.import_module("backtest.scripts.promote_bt_v5_top5d1f100")
    repo, source, before = promotion_fixture
    source_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob("*") if p.is_file()}
    result = promote.promote(repo_root=repo, source_root=source)
    rows = [json.loads(line) for line in (repo / "backtest/experiments/registry.jsonl").read_text().splitlines()]
    assert rows[0] == before[0]
    assert rows[1]["metrics"] == before[1]["metrics"]
    assert rows[2]["baseline_ref"] == before[2]["baseline_ref"]
    assert [r["exp_id"] for r in rows if r.get("current_bt")] == ["baseline/phase-m-v1-bt-v5"]
    v5 = next(r for r in rows if r["exp_id"] == "baseline/phase-m-v1-bt-v5")
    assert v5["model_ref"] == "regime-adapt/m0-h20-t5h5-es-v1"
    assert v5["model_baseline_version"] == "v2"
    assert v5["evaluation_mode"] == "historical_selection_window"
    doc = json.loads((repo / v5["eval_output"]).read_text())
    assert set(doc["ensemble"]["regimes"]) == {"D", "F", "T"}
    assert doc["ensemble"]["full_period"]["cumulative_return"] == pytest.approx(1.009 * .979 * 1.029 * 1.019 - 1)
    assert doc["provenance"]["report_csv"]["sha256"] == source_hashes[str(source / "backtest/result/candidate/run_01/report_normal.csv")]
    assert doc["ensemble"]["nav_curve"][-1][1] == pytest.approx(1.009 * .979 * 1.029 * 1.019)
    snapshots = {str(p): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert promote.promote(repo_root=repo, source_root=source) == result
    assert snapshots == {str(p): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert source_hashes == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob("*") if p.is_file()}


@pytest.mark.parametrize("change", ["prediction_hash", "cost_basis", "metrics"])
def test_promotion_rejects_mismatched_provenance_before_writing(promotion_fixture, change):
    promote = importlib.import_module("backtest.scripts.promote_bt_v5_top5d1f100")
    repo, source, _ = promotion_fixture
    if change == "prediction_hash":
        (source / "backtest/result/ensemble.pkl").write_bytes(b"changed")
    elif change == "cost_basis":
        p = source / "backtest/result/candidate/meta.json"
        d = json.loads(p.read_text())
        d["backtest"]["account"] = 300000
        _write_json(p, d)
    else:
        p = source / "backtest/result/candidate.json"
        d = json.loads(p.read_text())
        d["ensemble"]["full_period"]["annualized_return"] = 0.99
        _write_json(p, d)
    before = {str(p): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        promote.promote(repo_root=repo, source_root=source)
    assert before == {str(p): p.read_bytes() for p in repo.rglob("*") if p.is_file()}


@pytest.mark.parametrize("flag", ["only_tradable", "forbid_all_trade_at_limit"])
@pytest.mark.parametrize("change", ["enabled", "missing"])
def test_promotion_rejects_changed_execution_flags_before_writing(promotion_fixture, flag, change):
    promote = importlib.import_module("backtest.scripts.promote_bt_v5_top5d1f100")
    repo, source, _ = promotion_fixture
    meta_path = source / "backtest/result/candidate/meta.json"
    meta = json.loads(meta_path.read_text())
    if change == "enabled":
        meta["strategy"][flag] = True
    else:
        del meta["strategy"][flag]
    _write_json(meta_path, meta)
    before = {str(p): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="candidate strategy differs from the approved BT v5"):
        promote.promote(repo_root=repo, source_root=source)
    assert before == {str(p): p.read_bytes() for p in repo.rglob("*") if p.is_file()}


def test_portable_catalog_works_without_original_artifacts(promotion_fixture, monkeypatch):
    promote = importlib.import_module("backtest.scripts.promote_bt_v5_top5d1f100")
    from backtest.scripts import build_phase_m_v1_bt_report as report

    repo, source, _ = promotion_fixture
    promote.promote(repo_root=repo, source_root=source)
    monkeypatch.setattr(report, "EXP_ROOT", repo)
    monkeypatch.setattr(report, "EXP_DIR", repo / "backtest/experiments")
    rows = report.load_registry(repo / "backtest/experiments/registry.jsonl")
    baselines, experiments = report.catalog_from_registry(rows)
    assert len(baselines) == 2
    html = report.render_hub(baselines, experiments)
    assert "BT v5" in html
    assert "历史选型窗口" in html
    current = next(b for b in baselines if b["current"])
    detail = report.render_experiment({"display_name": "BT v5", "doc": current["doc"]}, baselines[0])
    assert "<svg" in detail
    assert "<iframe" not in detail
    assert "backtest/result/" not in detail


def test_promotion_preserves_unrelated_legacy_registry_duplicates(promotion_fixture):
    promote = importlib.import_module("backtest.scripts.promote_bt_v5_top5d1f100")
    repo, source, before = promotion_fixture
    registry = repo / "backtest/experiments/registry.jsonl"
    with registry.open("a") as stream:
        stream.write(json.dumps(before[0]) + "\n")
    promote.promote(repo_root=repo, source_root=source)
    rows = [json.loads(line) for line in registry.read_text().splitlines()]
    assert [r for r in rows if r["exp_id"] == "model-v4"] == [before[0], before[0]]


def test_extended_diagnostic_discloses_days_without_canonical_regime_labels(promotion_fixture):
    promote = importlib.import_module("backtest.scripts.promote_bt_v5_top5d1f100")
    from backtest.scripts.strategy_stability_metrics import load_regime_labels

    repo, source, rows = promotion_fixture
    csv = source / "backtest/result/candidate/run_01/report_normal.csv"
    returns = pd.read_csv(csv, index_col=0, parse_dates=True)
    returns.loc[pd.Timestamp("2026-08-03")] = [.01, .001, .005, .2]
    returns.to_csv(csv)
    summary = summarize_stability(returns)
    summary["session_dir"] = "backtest/result/candidate"
    _write_json(source / "backtest/result/candidate.json", {"ensemble": summary})
    rec = {**rows[-1], "evaluation_mode": "extended_window_diagnostic"}
    _, doc = promote.snapshot_document(source, rec, load_regime_labels(repo / promote.LABELS), {})
    assert doc["evaluation_mode"] == "extended_window_diagnostic"
    assert doc["regime_coverage"] == {"total_days": 5, "labeled_days": 4, "unlabeled_days": 1}
