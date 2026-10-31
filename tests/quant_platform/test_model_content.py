"""Concise model descriptions are editorial; historical research remains frozen."""

import json

import pytest

from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, row


def by_id(c):
    return {r["id"]: r for r in c.snapshot()["records"]}


def test_historical_comparison_overrides_promoted_self_only_for_display(tmp_path):
    before = row("model/v3", baseline_version="v3")
    trial = row("model/v4", baseline_version="v4", baseline_ref="self",
                hypothesis="完整实验假设", conclusion="原历史结论")
    c = catalog(tmp_path, [before, trial])
    manifest(tmp_path, before["exp_id"])
    directory = manifest(tmp_path, trial["exp_id"])
    old = by_id(c)[trial["exp_id"]]
    registry_bytes = c.registry.read_bytes()
    path = tmp_path / directory / "metadata.json"
    metadata = json.loads(path.read_text())
    metadata.update(change_summary="早停：Top3×H5净年化 → 日RankIC。",
                    comparison_baseline={"ref": before["exp_id"], "version": "v3"})
    path.write_text(json.dumps(metadata, ensure_ascii=False))

    after = by_id(c)[trial["exp_id"]]

    assert after["change_summary"] == metadata["change_summary"]
    assert after["comparison_baseline"] == {"ref": before["exp_id"], "version": "v3", "initial": False}
    for key in old:
        if key not in {"change_summary", "comparison_baseline", "analysis_revision", "core_results"}:
            assert after[key] == old[key], key
    assert after["core_results"]["records"][0]["id"] == before["exp_id"]
    assert after["core_results"]["records"][1]["metrics"] == old["metrics"]
    assert after["analysis_revision"] != old["analysis_revision"]
    assert after["version"] == "v4" and after["baseline_ref"] == "self"
    assert c.registry.read_bytes() == registry_bytes


@pytest.mark.parametrize("ref,expected", [("model/v2", "v2"), ("self", ""), ("model/missing", ""), (None, "")])
def test_missing_editorial_baseline_only_resolves_explicit_historical_reference(tmp_path, ref, expected):
    baseline = row("model/v2", baseline_version="v2")
    current = row("model/v4", baseline_version="v4", current=True)
    trial = row("model/trial", baseline_version=None, baseline_ref=ref)
    c = catalog(tmp_path, [baseline, current, trial])
    record = by_id(c)[trial["exp_id"]]

    assert record["comparison_baseline"]["version"] == expected
    assert record["change_summary"] == ""
    assert not record["comparison_baseline"]["initial"]


def test_initial_baseline_has_no_invented_predecessor(tmp_path):
    original = row("model/v1", baseline_version="v1", baseline_ref="model/v2")
    c = catalog(tmp_path, [original])
    manifest(tmp_path, original["exp_id"], change_summary="建立H20训练目标基线。",
             comparison_baseline={"ref": "", "version": "", "initial": True})
    record = by_id(c)[original["exp_id"]]
    assert record["comparison_baseline"] == {"ref": "", "version": "", "initial": True}
    assert record["baseline_ref"] == "model/v2"


def test_editorial_comparison_cannot_promote_an_unregistered_draft(tmp_path):
    c = catalog(tmp_path, [])
    manifest(tmp_path, "draft/new", change_summary="替换训练标签。",
             comparison_baseline={"ref": "model/removed", "version": "v2"})
    record = by_id(c)["draft/new"]
    assert record["comparison_baseline"]["version"] == "v2"
    assert not any(record[k] for k in ["current", "is_baseline", "official", "comparable"])


@pytest.mark.parametrize("changes", [
    {"change_summary": None}, {"change_summary": []},
    {"comparison_baseline": "v3"}, {"comparison_baseline": {"ref": "model/v3"}},
    {"comparison_baseline": {"ref": "../bad", "version": "v3"}},
    {"comparison_baseline": {"ref": "self", "version": "v3"}},
    {"comparison_baseline": {"ref": "model/v3", "version": "v3", "initial": "true"}},
    {"comparison_baseline": {"ref": "model/v3", "version": "v3", "initial": True}},
    {"comparison_baseline": {"ref": "model/v3", "version": "v3", "current": True}},
])
def test_malformed_editorial_content_does_not_load_as_a_model_archive(tmp_path, changes):
    c = catalog(tmp_path, [])
    manifest(tmp_path, "draft/bad", **changes)
    snapshot = c.snapshot()
    assert snapshot["records"] == []
    assert snapshot["sources"]["discovery"]["errors"]


def test_editorial_baseline_version_must_match_a_retained_model(tmp_path):
    baseline = row("model/v2", baseline_version="v2")
    trial = row("model/trial", baseline_version=None, baseline_ref="model/v2")
    c = catalog(tmp_path, [baseline, trial])
    for original in [baseline, trial]:
        manifest(tmp_path, original["exp_id"], **({"comparison_baseline": {"ref": "model/v2", "version": "v4"}}
                                                if original == trial else {}))
    record = by_id(c)[trial["exp_id"]]
    assert record["comparison_baseline"]["version"] == ""
    assert any("对照版本" in message for message in record["warnings"])
