"""Editorial names, categories and analyses do not alter research authority."""

import json

import pytest

from backtest.scripts.experiment_registry import write_registry
from quant_platform.research import ResearchCatalog
from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, model_doc, row, strategy_doc, write_json


def update_metadata(root, directory, **changes):
    path = root / directory / "metadata.json"
    value = json.loads(path.read_text())
    value.update(changes)
    path.write_text(json.dumps(value, ensure_ascii=False))
    return path


@pytest.mark.parametrize(
    "category,label",
    [
        ("training_target", "训练目标"),
        ("early_stopping", "早停方法"),
        ("model_structure", "模型结构"),
        ("features", "特征"),
        ("evaluation_method", "评估方法"),
    ],
)
def test_metadata_title_and_experiment_type_override_old_display_name(tmp_path, category, label):
    original = row(display_name="原登记名称", conclusion="原登记结论")
    c = catalog(tmp_path, [original])
    manifest(tmp_path, original["exp_id"], title="可读的新名称", experiment_type=category)
    original_registry = c.registry.read_bytes()

    record = c.snapshot()["records"][0]

    assert record["name"] == "可读的新名称"
    assert record["experiment_type"] == category
    assert record["experiment_type_label"] == label
    assert record["analysis"] == {}
    assert record["conclusion"] == "原登记结论"
    assert c.registry.read_bytes() == original_registry


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize(
    "kind,expected,label", [("model", "uncategorized", "未分类"), ("strategy", "strategy_backtest", "策略回测")]
)
def test_missing_category_defaults_by_kind_for_legacy_and_typed_records(tmp_path, typed, kind, expected, label):
    original = row(kind=kind, display_name="原名称")
    doc = model_doc() if kind == "model" else strategy_doc()
    c = catalog(tmp_path, [original], [doc])
    if typed:
        manifest(tmp_path, original["exp_id"], kind=kind, title="原名称")

    record = c.snapshot()["records"][0]

    assert record["name"] == "原名称"
    assert record["experiment_type"] == expected
    assert record["experiment_type_label"] == label
    assert record["analysis"] == {}


def test_editorial_analysis_preserves_registry_metrics_and_baseline_authority(tmp_path):
    trial = row("model/trial", baseline_version=None, conclusion="原登记：未晋升", promoted_to="model/current")
    baseline = row("model/current", baseline_version="v4", promoted_from=trial["exp_id"])
    c = catalog(tmp_path, [trial, baseline])
    for original in (trial, baseline):
        manifest(tmp_path, original["exp_id"])
    before = {record["id"]: record for record in c.snapshot()["records"]}
    registry_bytes = c.registry.read_bytes()
    source_bytes = (tmp_path / trial["eval_output"]).read_bytes()
    analysis = {
        "conclusion": "人工分析草稿：建议继续核验，不是晋升决定",
        "evidence": ["notes/review.md"],
        "limitations": "仅用于解释已有来源，不新增科学结论",
        "review_status": "draft",
        "updated_at": "2026-10-31",
    }
    update_metadata(
        tmp_path, "backtest/experiments/models/model/trial", title="结构试验",
        experiment_type="model_structure", analysis=analysis,
        current=True, is_baseline=True, baseline_version="v99", promoted_to="model/imaginary",
    )

    after = {record["id"]: record for record in c.snapshot()["records"]}

    assert after[trial["exp_id"]]["analysis"] == analysis
    assert after[trial["exp_id"]]["name"] == "结构试验"
    for eid in before:
        for key in (
            "id", "source", "metrics", "years", "regimes", "conclusion", "note", "version",
            "comparison_key", "official", "comparable", "current", "is_baseline", "promoted_from",
            "promoted_to", "promoted_version", "status", "window", "protocol",
        ):
            assert after[eid][key] == before[eid][key], (eid, key)
    assert c.registry.read_bytes() == registry_bytes
    assert (tmp_path / trial["eval_output"]).read_bytes() == source_bytes


def test_manifest_only_analysis_cannot_create_a_baseline_or_registered_conclusion(tmp_path):
    analysis = {"conclusion": "人工备注中的 v99 不授予基线身份", "review_status": "reviewed"}
    directory = manifest(
        tmp_path, "draft/new", title="待验证模型", experiment_type="features", analysis=analysis,
        current=True, is_baseline=True, baseline_version="v99", metrics_source={"path": "metrics.json"},
    )
    write_json(tmp_path, f"{directory}/metrics.json", model_doc())

    record = ResearchCatalog(tmp_path).snapshot()["records"][0]

    assert record["analysis"] == analysis
    assert record["metrics"]["net_ann"] == .31
    assert record["conclusion"] == ""
    assert not any(record[key] for key in ("registered", "is_baseline", "current", "official", "comparable"))
    assert record["version"] == record["promoted_version"] == ""


@pytest.mark.parametrize(
    "changes",
    [
        {"experiment_type": []},
        {"experiment_type": "unknown"},
        {"analysis": []},
        {"analysis": {"conclusion": 5}},
        {"analysis": {"evidence": ["../escape.md"]}},
        {"analysis": {"review_status": "approved"}},
    ],
)
def test_invalid_editorial_metadata_is_diagnosed_without_interrupting_other_records(tmp_path, changes):
    manifest(tmp_path, "draft/bad", **changes)
    manifest(tmp_path, "draft/good", title="有效模型", experiment_type="evaluation_method")

    snapshot = ResearchCatalog(tmp_path).snapshot()

    assert [record["id"] for record in snapshot["records"]] == ["draft/good"]
    assert snapshot["records"][0]["experiment_type"] == "evaluation_method"
    assert any(error.get("exp_id") == "draft/bad" for error in snapshot["sources"]["discovery"]["errors"])


def test_categorized_archive_alias_keeps_logical_source_and_resource_ids(tmp_path):
    original = row("regime-adapt/model-v4", display_name="旧名称")
    old = "backtest/experiments/models/regime-adapt/model-v4"
    new = "backtest/experiments/models/early-stopping/rankic-v4"
    original["eval_output"] = f"{old}/metrics/eval.json"
    write_json(tmp_path, original["eval_output"], model_doc())
    registry = tmp_path / "backtest/experiments/registry.jsonl"
    write_registry(registry, [original], indexed=True)
    c = ResearchCatalog(tmp_path)
    before = c.snapshot()["records"][0]
    source_resource = next(resource for resource in before["resources"] if resource["kind"] == "metrics")
    detail_resource = next(resource for resource in before["resources"] if resource["kind"] == "registry_record")
    index_bytes = registry.read_bytes()
    detail_bytes = c.resource(detail_resource["id"]).read_bytes()
    (tmp_path / new).parent.mkdir(parents=True)
    (tmp_path / old).rename(tmp_path / new)
    update_metadata(tmp_path, new, archive_path=new, title="RankIC早停·v4", experiment_type="early_stopping")
    write_json(tmp_path, "backtest/experiments/_catalog/path_map.json", {
        "schema_version": 1, "paths": {}, "directories": {old: new},
    })

    record = c.snapshot()["records"][0]

    assert record["name"] == "RankIC早停·v4"
    assert record["directory"] == new
    assert record["metadata_path"] == f"{new}/metadata.json"
    assert record["experiment_type"] == "early_stopping"
    assert record["source"] == original["eval_output"]
    assert record["metrics"] == before["metrics"]
    assert source_resource in record["resources"]
    assert detail_resource in record["resources"]
    assert c.resource(source_resource["id"]) == tmp_path / new / "metrics/eval.json"
    assert c.resource(detail_resource["id"]).read_bytes() == detail_bytes
    assert registry.read_bytes() == index_bytes
