"""Strategy display summaries preserve scientific model references and metrics."""

import json

import pytest

from backtest.scripts.experiment_registry import append_registry, write_registry
from quant_platform.research import ResearchCatalog
from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, model_doc, row, strategy_doc


def update(root, eid, **changes):
    path = root / "backtest/experiments/strategies" / eid / "metadata.json"
    value = json.loads(path.read_text())
    value.update(changes)
    path.write_text(json.dumps(value, ensure_ascii=False))
    return path


def setup(tmp_path, *, model_ref="model/v2"):
    models = [row("model/v2", baseline_version="v2"), row("model/v4", baseline_version="v4")]
    strategy = row("strategy/bt-v5", "strategy", bt_version="v5", model_ref=model_ref,
                   display_name="Historical long strategy title", hypothesis="Scientific hypothesis")
    c = catalog(tmp_path, [strategy, *models], [strategy_doc(), model_doc(), model_doc()])
    manifest(tmp_path, strategy["exp_id"], kind="strategy", title="Preserved main title")
    for model in models:
        version = model["baseline_version"]
        manifest(tmp_path, model["exp_id"], title=f"五种子早停模型（{version}）")
    return c, strategy


def records(c):
    return {record["id"]: record for record in c.snapshot()["records"]}


@pytest.mark.parametrize("ref,version", [("model/v2", "v2"), ("model/v4", "v4")])
def test_strategy_uses_referenced_model_version_and_strips_title_version(tmp_path, ref, version):
    c, strategy = setup(tmp_path, model_ref=ref)
    result = records(c)[strategy["exp_id"]]
    assert result["version"] == "v5"
    assert result["model_summary"] == {"ref": ref, "version": version, "name": "五种子早停模型"}
    assert result["strategy_summary"] == {"name": "Preserved main title", "description": ""}


def test_editorial_strategy_fields_do_not_change_authority_metrics_or_resources(tmp_path):
    c, strategy = setup(tmp_path)
    before = records(c)[strategy["exp_id"]]
    registry_bytes = c.registry.read_bytes()
    source_bytes = (tmp_path / strategy["eval_output"]).read_bytes()
    summary = {"name": "Top5/drop1", "description": "持有一天，掉出前100强制卖出"}
    update(tmp_path, strategy["exp_id"], model_ref="model/v2", strategy_summary=summary)
    after = records(c)[strategy["exp_id"]]

    assert after["strategy_summary"] == summary
    assert after["model_summary"]["version"] == "v2"
    for key in before:
        if key not in ("model_summary", "strategy_summary", "analysis_revision"):
            assert after[key] == before[key], key
    assert after["analysis_revision"] != before["analysis_revision"]
    assert c.registry.read_bytes() == registry_bytes
    assert (tmp_path / strategy["eval_output"]).read_bytes() == source_bytes


def test_metadata_model_ref_only_fills_missing_display_reference(tmp_path):
    c, strategy = setup(tmp_path, model_ref=None)
    before = records(c)[strategy["exp_id"]]
    update(tmp_path, strategy["exp_id"], model_ref="model/v4")
    after = records(c)[strategy["exp_id"]]
    assert after["model_summary"] == {"ref": "model/v4", "version": "v4", "name": "五种子早停模型"}
    for key in ("comparison_key", "official", "comparable", "current", "is_baseline", "metrics", "source"):
        assert after[key] == before[key]


def test_conflicting_metadata_model_ref_warns_and_keeps_registered_source(tmp_path):
    c, strategy = setup(tmp_path)
    update(tmp_path, strategy["exp_id"], model_ref="model/v4")
    snapshot = c.snapshot()
    result = next(record for record in snapshot["records"] if record["id"] == strategy["exp_id"])
    assert result["model_summary"]["ref"] == "model/v2"
    assert result["model_summary"]["version"] == "v2"
    assert any("模型引用" in warning and "登记" in warning for warning in result["warnings"])
    assert any(strategy["exp_id"] in warning and "模型引用" in warning for warning in snapshot["warnings"])


@pytest.mark.parametrize("ref", [None, "missing/model-v2", "strategy/bt-v5"])
def test_unknown_reference_does_not_guess_current_model_or_bt_version(tmp_path, ref):
    c, strategy = setup(tmp_path, model_ref=ref)
    result = records(c)[strategy["exp_id"]]
    assert result["model_summary"] == {"ref": ref or "", "version": "", "name": ""}


def test_legacy_long_title_never_substitutes_for_missing_model_reference(tmp_path):
    original = row("strategy/legacy", "strategy", bt_version="v5", display_name="v4模型 × Top5/drop1")
    c = catalog(tmp_path, [original], [strategy_doc()])
    result = c.snapshot()["records"][0]
    assert result["model_summary"] == {"ref": "", "version": "", "name": ""}
    assert result["strategy_summary"] == {"name": original["display_name"], "description": ""}


def test_optional_model_short_name_and_ascii_version_suffix_are_supported(tmp_path):
    c, strategy = setup(tmp_path)
    path = tmp_path / "backtest/experiments/models/model/v2/metadata.json"
    value = json.loads(path.read_text())
    value["short_name"] = "精简模型名 (v2)"
    path.write_text(json.dumps(value))
    assert records(c)[strategy["exp_id"]]["model_summary"] == {
        "ref": "model/v2", "version": "v2", "name": "精简模型名"}


def test_latest_scientific_event_owns_the_model_reference(tmp_path):
    c, strategy = setup(tmp_path)
    c.registry.write_text(c.registry.read_text() + "\n" + json.dumps({**strategy, "model_ref": "model/v4"}))
    result = records(c)[strategy["exp_id"]]
    assert result["model_summary"]["ref"] == "model/v4"
    assert result["model_summary"]["version"] == "v4"


@pytest.mark.parametrize("changes", [
    {"model_ref": []}, {"model_ref": "../model"},
    {"strategy_summary": None}, {"strategy_summary": []},
    {"strategy_summary": {"name": "only name"}},
    {"strategy_summary": {"name": "", "description": "description"}},
    {"strategy_summary": {"name": "name", "description": 1}},
    {"strategy_summary": {"name": "name", "description": "description", "current": True}},
])
def test_invalid_strategy_display_metadata_is_isolated(tmp_path, changes):
    manifest(tmp_path, "draft/bad", kind="strategy", **changes)
    manifest(tmp_path, "draft/good", kind="strategy")
    snapshot = ResearchCatalog(tmp_path).snapshot()
    assert [record["id"] for record in snapshot["records"]] == ["draft/good"]
    assert any(error.get("exp_id") == "draft/bad" for error in snapshot["sources"]["discovery"]["errors"])


def test_writer_preserves_model_reference_and_strategy_editorial_summary(tmp_path):
    path = tmp_path / "backtest/experiments/registry.jsonl"
    strategy = row("strategy/bt-v5", "strategy", bt_version="v5", model_ref="model/v2")
    write_registry(path, [strategy])
    summary = {"name": "Top5/drop1", "description": "固定持有阈值"}
    metadata = update(tmp_path, strategy["exp_id"], model_ref="model/v2", strategy_summary=summary)
    append_registry(path, {**strategy, "note": "latest event"})
    actual = json.loads(metadata.read_text())
    assert actual["model_ref"] == "model/v2" and actual["strategy_summary"] == summary
