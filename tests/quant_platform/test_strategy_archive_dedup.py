"""A promoted strategy and its candidate share one canonical experiment archive."""

from pathlib import Path
import hashlib

from quant_platform.research import ResearchCatalog
from backtest.scripts.research_layout import resolve_research_path


ROOT = Path(__file__).resolve().parents[2]


def test_v2_top5_drop1_hold1_is_listed_once_and_remains_a_benchmark():
    snapshot = ResearchCatalog(ROOT).snapshot()
    matches = [r for r in snapshot["records"] if r["kind"] == "strategy"
               and r["model_summary"]["ref"] == "regime-adapt/m0-h20-t5h5-es-v1"
               and r["strategy_summary"]["name"] == "Top5换1·持1"]
    assert len(matches) == 1
    record = matches[0]
    assert record["id"] == "baseline/phase-m-v1-bt-v2"
    assert record["version"] == "v2" and record["is_baseline"]
    assert record["metrics"]["annualized_return"] == .23130372925034037
    assert record["comparison_baseline"]["ref"] == "baseline/phase-m-v1-bt-v1"
    assert [r["id"] for r in snapshot["benchmarks"] if r["kind"] == "strategy" and r["version"] == "v2"] == [record["id"]]
    assert not snapshot["warnings"] and not snapshot["sources"]["discovery"]["errors"]


def test_retained_strategy_reports_and_current_model_and_bt_are_unchanged():
    c = ResearchCatalog(ROOT)
    records = {r["id"]: r for r in c.snapshot()["records"]}
    assert len(records) == 23
    assert "regime-adapt/m0h20es-all-top5d1-bt" not in records
    retained = records["baseline/phase-m-v1-bt-v2"]
    assert all(c.resource(r["id"]).is_file() for r in retained["resources"] if r["kind"] == "report")
    assert records["regime-adapt/m0-h20-t5h5-es-v1"]["current"]
    assert records["baseline/phase-m-v1-bt-v5"]["current"]
    # The equivalent F100 candidate now shares the historical BT v5 source.
    assert "regime-adapt/m0h20es-all-top5d1f100-bt" not in records
    assert records["baseline/phase-m-v1-bt-v5"]["metrics"]["annualized_return"] == .3483992380540464


def test_pre_merge_bt_v2_report_url_still_resolves_without_a_duplicate_link():
    c = ResearchCatalog(ROOT)
    snapshot = c.snapshot()
    old = "backtest/experiments/strategies/topk-dropout/v2-top5-drop1/reports/all_top5d1_m0h20es.html"
    resource_id = hashlib.sha256(old.encode()).hexdigest()[:24]
    assert c.resource(resource_id) == resolve_research_path(
        "backtest/experiments/strategies/topk-dropout/bt-v2/reports/all_top5d1_m0h20es.html", ROOT)
    record = next(r for r in snapshot["records"] if r["id"] == "baseline/phase-m-v1-bt-v2")
    reports = [c.resource(r["id"]) for r in record["resources"] if r["kind"] == "report"]
    assert len(reports) == len(set(reports))


def test_merged_candidate_legacy_resources_keep_their_urls_and_display_each_file_once():
    c = ResearchCatalog(ROOT)
    snapshot = c.snapshot()
    for resource_id in ("114e1f14d8624c1c4bb52d6b", "ace411167d60a8ff56c0a605",
                        "38a17b3db3274f7e5b5bcf49", "518999b5b3256d3627db668b", "e168c583ef911677acdc8dfa"):
        assert c.resource(resource_id) is not None, resource_id
    record = next(r for r in snapshot["records"] if r["id"] == "baseline/phase-m-v1-bt-v2")
    resources = [(r["kind"], c.resource(r["id"])) for r in record["resources"] if r["available"]]
    assert len(resources) == len(set(resources))
