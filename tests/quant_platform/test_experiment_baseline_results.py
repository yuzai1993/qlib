"""Experiment core results always include the historical control, never latest."""

from pathlib import Path

import pytest

from quant_platform.research import ResearchCatalog
from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, model_doc, row, strategy_doc


def test_experiment_pairs_with_historical_baseline_and_preserves_original_metrics(tmp_path):
    baseline = row("model/v2", baseline_version="v2")
    trial = row("model/trial", baseline_version=None, baseline_ref="model/v2")
    latest = row("model/v4", baseline_version="v4", current=True)
    docs = [model_doc() for _ in range(3)]
    for d, n in zip(docs, (.2, .25, .4)):
        d["pools"]["all"]["ensemble"]["head"]["3"]["5"]["net_ann"] = n
    c = catalog(tmp_path, [baseline, trial, latest], docs)
    before = c.registry.read_bytes()
    records = {r["id"]: r for r in c.snapshot()["records"]}
    result = records[trial["exp_id"]]["core_results"]
    assert result["kind"] == "baseline_comparison"
    assert [r["id"] for r in result["records"]] == [baseline["exp_id"], trial["exp_id"]]
    assert [r["metrics"]["net_ann"] for r in result["records"]] == [.2, .25]
    assert [r["role"] for r in result["records"]] == ["baseline", "experiment"]
    assert records[trial["exp_id"]]["metrics"]["net_ann"] == .25
    assert c.registry.read_bytes() == before


@pytest.mark.parametrize("case", ["initial", "missing", "wrong_kind", "wrong_version", "failed_control"])
def test_missing_or_invalid_control_is_explicit_empty_row_not_an_invented_comparator(tmp_path, case):
    base = row("model/base", baseline_version="v2")
    trial = row("model/trial", baseline_version=None, baseline_ref=base["exp_id"])
    rows, docs = [base, trial], [model_doc(), model_doc()]
    if case == "failed_control":
        base["state"] = "failed"
    if case == "wrong_kind":
        base.update(report_kind="phase_m_v1_bt_baseline", bt_version="v2")
        base.pop("baseline_version")
        base.pop("phase_m_protocol")
        docs[0] = strategy_doc()
    if case == "missing":
        rows, docs = [trial], [model_doc()]
    c = catalog(tmp_path, rows, docs)
    for r in rows:
        manifest(tmp_path, r["exp_id"], kind="strategy" if case == "wrong_kind" and r is base else "model")
    declared = {"ref": "", "version": "", "initial": True} if case == "initial" else {
        "ref": base["exp_id"], "version": "v99" if case == "wrong_version" else "v2"}
    manifest(tmp_path, trial["exp_id"], comparison_baseline=declared)
    result = next(r for r in c.snapshot()["records"] if r["id"] == trial["exp_id"])["core_results"]
    assert len(result["records"]) == 2
    assert all(v is None for v in result["records"][0]["metrics"].values())
    assert "Baseline" in result["records"][0]["name"]
    assert result["warnings"] or "无前版" in result["note"]


def test_single_seed_trial_uses_matching_baseline_seed_from_source(tmp_path):
    baseline = row("model/v4", baseline_version="v4")
    trial = row("model/seed42", baseline_version=None, baseline_ref=baseline["exp_id"], seeds=[42])
    base_doc, trial_doc = model_doc(), model_doc([42])
    base_doc["pools"]["all"]["seeds"]["42"] = {
        "head": {"3": {"5": {"net_ann": .17, "net_ann_vol": .5, "net_sharpe": .34}}},
        "h5": {"rank_ic_mean": .08}}
    c = catalog(tmp_path, [baseline, trial], [base_doc, trial_doc])
    record = next(r for r in c.snapshot()["records"] if r["id"] == trial["exp_id"])
    pair = record["core_results"]
    assert pair["records"][0]["metrics"]["net_ann"] == .17
    assert pair["records"][0]["metrics"]["rank_ic_mean"] == .08
    assert pair["records"][0]["seeds"] == [42]
    assert "42" in pair["note"]
    assert record["comparable"] is False


def test_missing_matching_seed_does_not_substitute_five_seed_ensemble(tmp_path):
    baseline = row("model/v4", baseline_version="v4")
    trial = row("model/seed42", baseline_version=None, baseline_ref=baseline["exp_id"], seeds=[42])
    c = catalog(tmp_path, [baseline, trial], [model_doc(), model_doc([42])])
    result = next(r for r in c.snapshot()["records"] if r["id"] == trial["exp_id"])["core_results"]
    assert result["records"][0]["metrics"]["net_ann"] is None
    assert result["warnings"]


def test_failed_trial_keeps_valid_strategy_baseline_and_different_window_is_labeled(tmp_path):
    base = row("bt/v5", "strategy", bt_version="v5")
    trial = row("bt/failed", "strategy", baseline_ref=base["exp_id"], state="failed")
    base_doc, failed = strategy_doc(), strategy_doc()
    failed["ensemble"]["full_period"] = {}
    failed["backtest_window"] = ["2026-08-03", "2026-10-28"]
    c = catalog(tmp_path, [base, trial], [base_doc, failed])
    result = next(r for r in c.snapshot()["records"] if r["id"] == trial["exp_id"])["core_results"]
    assert result["records"][0]["metrics"]["annualized_return"] == .26
    assert result["records"][1]["metrics"]["annualized_return"] is None
    assert "2026-08-03" in result["note"]
    assert result["warnings"]


def test_all_retained_experiments_have_a_control_row_or_explicit_initial_placeholder():
    root = Path(__file__).resolve().parents[2]
    records = ResearchCatalog(root).snapshot()["records"]
    for record in records:
        result = record["core_results"]
        if result["kind"] == "training_target_comparison":
            assert len(result["records"]) == 4
            continue
        assert len(result["records"]) == 2, record["id"]
        assert result["records"][0]["role"] == "baseline"
        assert result["records"][1]["metrics"] == record["metrics"]
