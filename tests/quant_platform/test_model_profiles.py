"""Model introductions describe recipes without changing metrics or permissions."""

import json
from pathlib import Path

import pytest

from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, row


def profile():
    return {
        "structure": "单LightGBM。",
        "features": "Alpha158加range特征。",
        "training_sample": {"pool": "沪深A股", "time_range": "2004-01-02～2020-07-31", "filters": "上市满60日、标签非空。"},
        "training_target": "下一交易日收盘起算的H20累计收益，CSRankNorm。",
        "evaluation_sample": {"pool": "沪深A股", "time_range": "2020-08-03～2026-07-31", "filters": "日频ST、成交额和可交易性过滤。"},
        "early_stopping": "评估窗H5标签的日RankIC。",
        "prediction_signal": "每个种子每日截面z-score后等权平均，合成信号只评估一次。",
        "changes": "早停从Top3×H5净年化改为日RankIC。",
        "model_paths": [{"path": "backtest/result/model_s42/run_01/artifacts_root/artifacts/trained_model", "seed": 42}],
    }


def setup(root, **fields):
    original = row("model/v4", baseline_version="v4", seeds=[42])
    c = catalog(root, [original])
    directory = manifest(root, original["exp_id"], **fields)
    return c, root / directory / "metadata.json"


def test_profile_uses_recipe_but_owns_no_scientific_fields(tmp_path):
    c, path = setup(tmp_path)
    before = c.snapshot()["records"][0]
    registry_bytes = c.registry.read_bytes()
    declared = profile()
    model = tmp_path / declared["model_paths"][0]["path"]
    model.parent.mkdir(parents=True)
    model.write_bytes(b"Do not deserialize this model")
    doc = json.loads(path.read_text())
    doc["model_profile"] = declared
    path.write_text(json.dumps(doc, ensure_ascii=False))

    after = c.snapshot()["records"][0]
    summary = after["model_profile"]
    assert summary["version"] == "v4"
    assert summary["experiment_id"] == "models/model/v4"
    assert summary["seeds"] == before["seeds"]
    assert summary["structure"] == declared["structure"]
    assert summary["features"] == declared["features"]
    assert summary["early_stopping"] == declared["early_stopping"]
    assert summary["prediction_signal"] == declared["prediction_signal"]
    assert "evaluation_method" not in summary
    assert summary["model_paths"] == [{**declared["model_paths"][0], "available": True}]
    assert summary["training_sample"] == declared["training_sample"]
    assert summary["evaluation_sample"] == declared["evaluation_sample"]
    for key in before:
        if key not in ["model_profile", "analysis_revision"]:
            assert after[key] == before[key], key
    assert c.registry.read_bytes() == registry_bytes
    assert c.resource("backtest/result/model_s42/run_01/artifacts_root/artifacts/trained_model") is None


def test_directory_id_follows_classified_archive_not_internal_identity(tmp_path):
    c, old_path = setup(tmp_path)
    target = tmp_path / "backtest/experiments/models/early-stopping/rankic-v4"
    target.parent.mkdir(parents=True)
    old_path.parent.rename(target)
    path = target / "metadata.json"
    doc = json.loads(path.read_text())
    doc["archive_path"] = target.relative_to(tmp_path).as_posix()
    path.write_text(json.dumps(doc))
    record = c.snapshot()["records"][0]
    assert record["id"] == "model/v4"
    assert record["model_profile"]["experiment_id"] == "models/early-stopping/rankic-v4"


def test_missing_profile_does_not_guess_a_current_recipe_or_locations(tmp_path):
    c, _ = setup(tmp_path)
    p = c.snapshot()["records"][0]["model_profile"]
    assert p["structure"] == p["training_target"] == p["features"] == p["early_stopping"] == p["prediction_signal"] == p["changes"] == ""
    assert p["training_sample"] == p["evaluation_sample"] == {"pool": "", "time_range": "", "filters": ""}
    assert p["model_paths"] == []


def test_missing_model_is_reported_without_binary_loading_or_restoration(tmp_path, monkeypatch):
    declared = profile()
    c, _ = setup(tmp_path, model_profile=declared)
    original = Path.read_bytes

    def guarded(path):
        if path.name == "trained_model":
            raise AssertionError("a model introduction must not read model bytes")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    model = c.snapshot()["records"][0]["model_profile"]["model_paths"][0]
    assert model["available"] is False
    assert not (tmp_path / model["path"]).exists()


def test_model_link_cannot_follow_a_symlink_outside_the_research_root(tmp_path):
    root = tmp_path / "repo"
    declared = profile()
    c, _ = setup(root, model_profile=declared)
    model = root / declared["model_paths"][0]["path"]
    model.parent.mkdir(parents=True)
    outside = tmp_path / "outside-model"
    outside.write_bytes(b"private model")
    model.symlink_to(outside)
    record = c.snapshot()["records"][0]
    assert record["model_profile"]["model_paths"][0]["available"] is False


def test_resources_receive_standard_purpose_without_changing_resource_ids(tmp_path):
    c, _ = setup(tmp_path, model_profile=profile())
    record = c.snapshot()["records"][0]
    artifacts = record["model_profile"]["artifacts"]
    assert [a["resource_id"] for a in artifacts] == [r["id"] for r in record["resources"]]
    assert all(a["label"] and a["description"] for a in artifacts)
    assert all(a["available"] == r["available"] for a,r in zip(artifacts, record["resources"]))


@pytest.mark.parametrize("bad", [
    None, [], {"version": "v99"}, {"seeds": [99]}, {"experiment_id": "invented"},
    {"structure": []}, {"features": []}, {"early_stopping": []}, {"prediction_signal": None},
    {"training_sample": []}, {"evaluation_sample": {"pool": 5}},
    {"model_paths": ["backtest/result/model"]},
    {"model_paths": [{"path": "../outside"}]},
    {"model_paths": [{"path": "/tmp/model"}]},
    {"model_paths": [{"path": ".git/config"}]},
    {"model_paths": [{"path": "backtest/result/model", "seed": True}]},
    {"model_paths": [{"path": "backtest/result/model", "available": True}]},
])
def test_malformed_profiles_do_not_load_or_gain_authority(tmp_path, bad):
    c = catalog(tmp_path, [])
    manifest(tmp_path, "draft/new", model_profile=bad)
    snapshot = c.snapshot()
    assert snapshot["records"] == []
    assert snapshot["sources"]["discovery"]["errors"]


def test_legacy_combined_method_is_not_guessed_as_new_training_or_signal_fields(tmp_path):
    c, _ = setup(tmp_path, model_profile={"structure": "原结构", "evaluation_method": "旧的混合早停/信号描述"})
    p = c.snapshot()["records"][0]["model_profile"]
    assert p["structure"] == "原结构"
    assert p["features"] == p["early_stopping"] == p["prediction_signal"] == ""
