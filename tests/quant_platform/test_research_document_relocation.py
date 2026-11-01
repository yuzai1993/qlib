"""Research documents keep resource identities within a narrow read boundary."""

import hashlib

import pytest

from quant_platform.research import ResearchCatalog
from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import write_json


def test_shared_document_move_keeps_old_and_current_resource_links(tmp_path):
    old = "backtest/experiments/_shared/docs/plan.md"
    current = "docs/research/plan.md"
    target = tmp_path / current
    target.parent.mkdir(parents=True)
    target.write_text("# Frozen research plan\n")
    write_json(tmp_path, "backtest/experiments/_catalog/path_map.json", {
        "schema_version": 1, "paths": {old: current}})
    manifest(tmp_path, inventory_kind="asset_snapshot", docs=[old, current])
    catalog = ResearchCatalog(tmp_path)

    record = catalog.snapshot()["records"][0]

    plans = [item for item in record["resources"] if item["kind"] == "plan"]
    assert len(plans) == 1 and plans[0]["available"]
    for relative in (old, current):
        identity = hashlib.sha256(relative.encode()).hexdigest()[:24]
        assert catalog.resource(identity) == target


@pytest.mark.parametrize("relative", ["docs/private.md", "docs/research/model.pkl", "docs/research/data.json"])
def test_document_access_does_not_expand_to_other_docs_or_binary_data(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a registered research document")
    catalog = ResearchCatalog(tmp_path)
    resource = catalog._resource(relative, "plan")
    assert not resource["available"]
    assert catalog.resource(resource["id"]) is None


def test_legacy_document_symlink_cannot_replace_an_explicit_migration_alias(tmp_path):
    target = tmp_path / "docs/research/plan.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Plan\n")
    link = tmp_path / "backtest/experiments/cross.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    catalog = ResearchCatalog(tmp_path)
    assert not catalog._resource("backtest/experiments/cross.md", "plan")["available"]


def test_research_document_rechecks_symlink_escape_on_download(tmp_path):
    path = tmp_path / "docs/research/plan.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Plan\n")
    catalog = ResearchCatalog(tmp_path)
    resource = catalog._resource("docs/research/plan.md", "plan")
    assert resource["available"]
    outside = tmp_path / "docs/private.md"
    outside.write_text("private")
    path.unlink()
    path.symlink_to(outside)
    assert catalog.resource(resource["id"]) is None
