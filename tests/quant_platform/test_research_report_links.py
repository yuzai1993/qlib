"""Report links describe usable files once, without rewriting source history."""

import hashlib
from pathlib import Path

from quant_platform.research import ResearchCatalog
from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, row, write_json


def write_report(root, path, text="<h1>report</h1>"):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def test_report_aliases_collapse_to_one_working_link_and_keep_original_id(tmp_path):
    old = "backtest/experiments/legacy/report.html"
    original = row("model/trial", baseline_version=None, detail_report=old)
    c = catalog(tmp_path, [original])
    directory = manifest(tmp_path, original["exp_id"], reports=["reports/report.html"])
    current = f"{directory}/reports/report.html"
    path = write_report(tmp_path, current)
    write_json(tmp_path, "backtest/experiments/_catalog/path_map.json", {
        "schema_version": 1, "paths": {old: current}})
    registry_bytes = c.registry.read_bytes()

    record = c.snapshot()["records"][0]

    reports = [r for r in record["resources"] if r["kind"] == "report"]
    assert len(reports) == 1
    assert reports[0]["id"] == hashlib.sha256(old.encode()).hexdigest()[:24]
    assert reports[0]["available"] and c.resource(reports[0]["id"]) == path
    assert c.registry.read_bytes() == registry_bytes


def test_missing_report_link_is_removed_but_missing_metric_evidence_remains(tmp_path):
    original = row("model/trial", baseline_version=None,
                   detail_report="backtest/experiments/obsolete.html")
    c = catalog(tmp_path, [original])
    directory = manifest(tmp_path, original["exp_id"], reports=["reports/current.html"],
                         data=["metrics/cleaned.json"])
    target = write_report(tmp_path, f"{directory}/reports/current.html")

    record = c.snapshot()["records"][0]

    reports = [r for r in record["resources"] if r["kind"] == "report"]
    assert len(reports) == 1 and c.resource(reports[0]["id"]) == target
    assert any(r["name"] == "cleaned.json" and not r["available"] for r in record["resources"])


def test_distinct_report_files_with_the_same_name_are_not_collapsed(tmp_path):
    original = row("model/trial", baseline_version=None,
                   detail_report="backtest/experiments/old/report.html")
    c = catalog(tmp_path, [original])
    directory = manifest(tmp_path, original["exp_id"], reports=["reports/report.html"])
    write_report(tmp_path, original["detail_report"])
    write_report(tmp_path, f"{directory}/reports/report.html")

    record = c.snapshot()["records"][0]

    reports = [r for r in record["resources"] if r["kind"] == "report"]
    assert len(reports) == 2
    assert len({c.resource(r["id"]) for r in reports}) == 2


def test_report_with_failed_integrity_is_not_offered(tmp_path):
    original = row("model/trial", baseline_version=None)
    c = catalog(tmp_path, [original])
    directory = manifest(tmp_path, original["exp_id"],
                         reports=[{"path": "reports/report.html", "sha256": "0" * 64}])
    write_report(tmp_path, f"{directory}/reports/report.html")

    record = c.snapshot()["records"][0]

    assert not any(r["kind"] == "report" for r in record["resources"])


def test_retained_five_seed_ensemble_offers_one_resolvable_report():
    c = ResearchCatalog(Path(__file__).resolve().parents[2])

    record = next(r for r in c.snapshot()["records"]
                  if r["id"] == "regime-adapt/m0-h20-t3h5es-densemble-v1")

    reports = [r for r in record["resources"] if r["kind"] == "report"]
    assert len(reports) == 1 and reports[0]["available"]
    assert c.resource(reports[0]["id"]).is_file()
