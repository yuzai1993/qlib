"""Folder discovery is separate from registry-owned research authority."""

import json

import pytest

from quant_platform.research import ResearchCatalog
from backtest.scripts.experiment_registry import write_registry
from tests.quant_platform.test_research import catalog, model_doc, row, write_json, write_legacy_registry


def manifest(root, eid="draft/new", kind="model", track="all_a", **changes):
    directory = f"backtest/experiments/{'models' if kind == 'model' else 'strategies'}/{eid}"
    value = {"schema_version": 2, "entity_type": "experiment", "exp_id": eid,
             "kind": kind, "track": track, "title": eid, **changes}
    write_json(root, f"{directory}/metadata.json", value)
    return directory


def test_manifest_only_folder_is_discovered_on_each_refresh_without_registry(tmp_path):
    c = ResearchCatalog(tmp_path)
    manifest(tmp_path)
    first = c.snapshot()
    assert [r["id"] for r in first["records"]] == ["draft/new"]
    record = first["records"][0]
    assert record["track"] == "all_a" and record["status"] == "unregistered"
    assert not any(record[k] for k in ("current", "official", "comparable", "is_baseline"))
    manifest(tmp_path, "draft/second", track="csi1000", current=True, baseline_version="v99")
    second = c.snapshot()
    assert len(second["records"]) == 2
    assert not any(r["current"] or r["is_baseline"] for r in second["records"])
    assert second["sources"]["discovery"]["mode"] == "typed"
    assert "csi1000" in second["metric_definitions_by_track"]


@pytest.mark.parametrize("invalid", [None, 1, True])
def test_bad_inventory_does_not_prevent_other_archives_from_loading(tmp_path, invalid):
    manifest(tmp_path, "draft/bad", data=invalid)
    manifest(tmp_path, "draft/good")

    snapshot = ResearchCatalog(tmp_path).snapshot()

    assert [record["id"] for record in snapshot["records"]] == ["draft/good"]
    errors = snapshot["sources"]["discovery"]["errors"]
    assert len(errors) == 1 and errors[0]["exp_id"] == "draft/bad"
    assert "data" in errors[0]["message"]


def test_aliases_resolve_read_paths_but_preserve_resource_identity(tmp_path):
    r = row()
    c = catalog(tmp_path, [r])
    old = r["eval_output"]
    directory = manifest(tmp_path, r["exp_id"])
    new = f"{directory}/metrics/source.json"
    (tmp_path / new).parent.mkdir(parents=True)
    (tmp_path / old).rename(tmp_path / new)
    write_json(tmp_path, "backtest/experiments/_catalog/path_map.json", {
        "schema_version": 1, "paths": {"backtest/experiments/registry_records/prior.json": old, old: new}})
    record = c.snapshot()["records"][0]
    assert record["source"] == old and record["metrics"]["net_ann"] == .31
    source = next(r for r in record["resources"] if r["kind"] == "metrics")
    assert c.resource(source["id"]) == tmp_path / new


def test_migrated_registry_detail_retains_opaque_resource_identity(tmp_path):
    r = row()
    c = catalog(tmp_path, [r])
    write_legacy_registry(c.registry, [r])
    old_record = c.snapshot()["records"][0]
    old_resource = next(p for p in old_record["resources"] if p["kind"] == "registry_record")
    index = json.loads(c.registry.read_text())
    old = "backtest/experiments/" + index["detail_path"]
    directory = manifest(tmp_path, r["exp_id"])
    new = f"{directory}/records/{index['detail_sha256']}.json"
    (tmp_path / new).parent.mkdir()
    (tmp_path / old).rename(tmp_path / new)
    write_json(tmp_path, "backtest/experiments/_catalog/path_map.json", {
        "schema_version": 1, "paths": {"backtest/experiments/registry_records/prior.json": old, old: new}})
    c.registry.write_text(json.dumps({**index, "_registry_schema": "qlib-experiment-index-v3",
                                    "_archive_kind": "model", "detail_path": new.removeprefix("backtest/experiments/")}))
    resources = c.snapshot()["records"][0]["resources"]
    assert old_resource in resources
    assert c.resource(old_resource["id"]) == tmp_path / new


def test_registered_folder_missing_disables_authority_and_emits_diagnostic(tmp_path):
    c = catalog(tmp_path, [row()])
    manifest(tmp_path)
    snapshot = c.snapshot()
    assert not any(r["current"] for r in snapshot["records"])
    missing = next(r for r in snapshot["records"] if r["id"] == "model/v4")
    assert not missing["official"] and not missing["comparable"]
    assert any("model/v4" in warning and "目录" in warning for warning in snapshot["warnings"])


@pytest.mark.parametrize("failure", ["corrupt", "kind", "traversal", "symlink"])
def test_bad_manifest_is_reported_without_fallback(tmp_path, failure):
    directory = manifest(tmp_path)
    path = tmp_path / directory / "metadata.json"
    doc = json.loads(path.read_text())
    if failure == "corrupt":
        path.write_text("{broken")
    elif failure == "kind":
        path.write_text(json.dumps({**doc, "kind": "strategy"}))
    elif failure == "traversal":
        path.write_text(json.dumps({**doc, "metrics_source": {"path": "../../escape.json"}}))
    else:
        outside = write_json(tmp_path, "outside.json", {})
        (path.parent / "source.json").symlink_to(outside)
        path.write_text(json.dumps({**doc, "metrics_source": {"path": "source.json"}}))
    snapshot = ResearchCatalog(tmp_path).snapshot()
    assert not snapshot["records"]
    assert snapshot["sources"]["discovery"]["errors"]


def test_local_metrics_source_draft_remains_unofficial(tmp_path):
    directory = manifest(tmp_path, metrics_source={"path": "metrics.json"})
    write_json(tmp_path, f"{directory}/metrics.json", model_doc())
    record = ResearchCatalog(tmp_path).snapshot()["records"][0]
    assert record["metrics"]["net_ann"] == .31
    assert record["source_status"] == "available"
    assert not record["official"] and not record["comparable"]


def test_invalid_draft_source_hash_does_not_expose_metrics_or_download(tmp_path):
    directory = manifest(tmp_path, metrics_source={"path": "metrics.json", "sha256": "0" * 64})
    write_json(tmp_path, f"{directory}/metrics.json", model_doc())
    c = ResearchCatalog(tmp_path)
    record = c.snapshot()["records"][0]
    assert record["source_status"] == "invalid"
    assert all(value is None for value in record["metrics"].values())
    source = next(r for r in record["resources"] if r["kind"] == "metrics")
    assert not source["available"] and c.resource(source["id"]) is None


def test_duplicate_manifests_are_diagnosed_and_not_selected(tmp_path):
    manifest(tmp_path, "same/id")
    manifest(tmp_path, "same/id", kind="strategy")
    snapshot = ResearchCatalog(tmp_path).snapshot()
    assert not snapshot["records"]
    assert len(snapshot["sources"]["discovery"]["errors"]) == 2
    assert any("duplicate experiment exp_id" in warning for warning in snapshot["warnings"])


def test_registered_manifest_source_hash_is_checked_on_refresh_and_download(tmp_path):
    r = row()
    c = catalog(tmp_path, [r])
    directory = manifest(tmp_path, r["exp_id"], metrics_source={"path": "metrics.json", "sha256": "0" * 64})
    new = f"{directory}/metrics.json"
    (tmp_path / r["eval_output"]).rename(tmp_path / new)
    write_json(tmp_path, "backtest/experiments/_catalog/path_map.json", {
        "schema_version": 1, "paths": {r["eval_output"]: new}})
    record = c.snapshot()["records"][0]
    assert record["source_status"] == "invalid"
    assert not record["official"] and not record["comparable"]
    assert not record["is_baseline"] and not record["current"]
    assert all(v is None for v in record["metrics"].values())
    resource = next(resource for resource in record["resources"] if resource["kind"] == "metrics")
    assert c.resource(resource["id"]) is None


@pytest.mark.parametrize("kind,track", [("model", "all_a"), ("model", "csi1000"), ("strategy", "csi1000")])
def test_invalid_json_source_cannot_be_a_current_typed_baseline(tmp_path, kind, track):
    if track == "all_a":
        registered = row()
    else:
        registered = {"exp_id": "baseline/b6-m" if kind == "model" else "baseline/b4-s-on-b6-m",
                      "phase": "M" if kind == "model" else "S", "state": "baseline",
                      "portable_eval_output": "backtest/experiments/corrupt.json"}
    c = catalog(tmp_path, [registered], [None])
    source = registered.get("portable_eval_output", registered.get("eval_output"))
    path = tmp_path / source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken")
    manifest(tmp_path, registered["exp_id"], kind=kind, track=track)
    record = c.snapshot()["records"][0]
    assert record["source_status"] == "invalid"
    assert not record["is_baseline"] and not record["current"]
    assert not record["official"] and not record["comparable"]


@pytest.mark.parametrize("failure", ["hash", "kind", "track", "missing"])
def test_invalid_latest_baseline_does_not_promote_older_baseline_to_current(tmp_path, failure):
    old, latest = row("model/v3", baseline_version="v3"), row("model/v4", baseline_version="v4")
    c = catalog(tmp_path, [old, latest])
    manifest(tmp_path, old["exp_id"])
    if failure == "hash":
        directory = manifest(tmp_path, latest["exp_id"], metrics_source={"path": "metrics.json", "sha256": "0" * 64})
        moved = f"{directory}/metrics.json"
        (tmp_path / latest["eval_output"]).rename(tmp_path / moved)
        write_json(tmp_path, "backtest/experiments/_catalog/path_map.json", {
            "schema_version": 1, "paths": {latest["eval_output"]: moved}})
    elif failure != "missing":
        manifest(tmp_path, latest["exp_id"], kind="strategy" if failure == "kind" else "model",
                 track="csi1000" if failure == "track" else "all_a")
    snapshot = c.snapshot()
    records = {r["id"]: r for r in snapshot["records"]}
    assert records[old["exp_id"]]["is_baseline"]
    assert not records[latest["exp_id"]]["is_baseline"]
    assert not any(r["current"] for r in snapshot["records"])


def test_invalid_older_baseline_does_not_block_valid_latest_baseline(tmp_path):
    old, latest = row("model/v3", baseline_version="v3"), row("model/v4", baseline_version="v4")
    c = catalog(tmp_path, [old, latest])
    manifest(tmp_path, latest["exp_id"])
    records = c.snapshot()["records"]
    assert [r["id"] for r in records if r["current"]] == [latest["exp_id"]]


def test_verified_csi1000_summary_keeps_pool_metrics_and_provenance(tmp_path):
    history = {"exp_id": "history/verified", "phase": "M", "state": "completed",
               "seeds": [42], "metrics_summary": {pool: {"ic_mean": value, "rank_ic_mean": value / 2}
                                                 for pool, value in (("csi1000", .3), ("csi300", .1), ("csi500", .2))}}
    c = catalog(tmp_path, [history], [None])
    write_registry(c.registry, [history], indexed=True)
    manifest(tmp_path, history["exp_id"], track="csi1000")
    record = c.snapshot()["records"][0]
    assert record["source_status"] == "historical_summary"
    assert record["metrics"]["ic_mean"] == .3
    assert record["pool_metrics"]["csi300"]["rank_ic_mean"] == .05
    assert record["pool_metrics"]["csi500"]["ic_mean"] == .2
    assert any(r["kind"] == "registry_record" and r["available"] for r in record["resources"])
    assert not record["official"] and not record["comparable"]


@pytest.mark.parametrize("kind,track", [("strategy", "csi1000"), ("model", "all_a")])
def test_historical_manifest_cannot_reclassify_registered_model_baseline(tmp_path, kind, track):
    historical = {"exp_id": "baseline/b6-m", "phase": "M", "state": "baseline"}
    c = catalog(tmp_path, [historical], [None])
    # A valid manifest in the wrong folder must not rewrite registry authority.
    manifest(tmp_path, historical["exp_id"], kind=kind, track=track)
    snapshot = c.snapshot()
    record = snapshot["records"][0]
    assert record["kind"] == "model" and record["track"] == "csi1000"
    assert not record["is_baseline"] and not record["current"]
    assert not record["official"] and not record["comparable"]
    assert any(historical["exp_id"] in warning and "不符" in warning for warning in snapshot["warnings"])


@pytest.mark.parametrize("kind", ["model", "strategy"])
def test_wrong_shape_historical_source_uses_verified_summary_provenance(tmp_path, kind):
    wrong_source = "backtest/experiments/unrelated_metadata.json"
    historical = {"exp_id": "history/wrong-shape", "phase": "M" if kind == "model" else "S",
                  "state": "completed", "eval_output": wrong_source}
    if kind == "model":
        historical["metrics_summary"] = {"csi1000": {"ic_mean": .12, "rank_ic_mean": .2}}
    else:
        historical["full_period"] = {"annualized_return": .23, "sharpe_ratio": 1.16}
        historical["years"] = {"2025": {"annualized_return": .2}}
    c = catalog(tmp_path, [historical], [None])
    write_legacy_registry(c.registry, [historical])
    directory = manifest(tmp_path, historical["exp_id"], kind=kind, track="csi1000")
    write_json(tmp_path, wrong_source, {"schema_version": 2, "description": "not metrics"})
    index = json.loads(c.registry.read_text())
    record = c.snapshot()["records"][0]
    assert record["source_status"] == "historical_summary"
    assert record["source"] == "backtest/experiments/" + index["detail_path"]
    assert record["original_source"] == wrong_source
    key = "ic_mean" if kind == "model" else "annualized_return"
    assert record["metrics"][key] == (.12 if kind == "model" else .23)
    provenance = next(resource for resource in record["resources"] if resource["kind"] == "registry_record")
    assert c.resource(provenance["id"]) == c.registry.parent / index["detail_path"]


def test_all_typed_registered_ids_without_valid_folders_get_diagnostics(tmp_path):
    historical = {"exp_id": "history/missing", "phase": "M", "state": "completed"}
    c = catalog(tmp_path, [historical], [None])
    manifest(tmp_path)
    snapshot = c.snapshot()
    assert any("history/missing" in warning for warning in snapshot["warnings"])
    assert next(r for r in snapshot["records"] if r["id"] == "history/missing")["registered"]


def test_historical_strategy_full_period_years_and_current_are_track_local(tmp_path):
    historical = {"exp_id": "baseline/b4-s-on-b6-m", "phase": "S", "state": "baseline",
                  "full_period": {"annualized_return": .23, "sharpe_ratio": 1.16},
                  "years": {"2025": {"annualized_return": .2}},
                  "neighborhood": {"absolute_p25": {"annualized_return": .19}}}
    c = catalog(tmp_path, [historical], [None])
    write_registry(c.registry, [historical], indexed=True)
    record = c.snapshot()["records"][0]
    assert record["metrics"]["annualized_return"] == .23
    assert record["years"]["2025"]["annualized_return"] == .2
    assert record["neighborhood"]["absolute_p25"]["annualized_return"] == .19
    assert record["is_baseline"] and record["current"] and record["track"] == "csi1000"
    assert not record["comparable"]


def test_historical_promotions_follow_registry_within_same_track_and_protocol(tmp_path):
    source = {"exp_id": "strategy/candidate", "phase": "S", "state": "promoted",
              "promoted_to": "baseline/b4-s-on-b6-m"}
    baseline = {"exp_id": "baseline/b4-s-on-b6-m", "phase": "S", "state": "baseline",
                "promoted_from": source["exp_id"]}
    c = catalog(tmp_path, [source, baseline], [None, None])
    write_registry(c.registry, [source, baseline], indexed=True)
    records = {r["id"]: r for r in c.snapshot()["records"]}
    assert records[source["exp_id"]]["promoted_to"] == baseline["exp_id"]
    assert records[source["exp_id"]]["promoted_version"] == "B4"
    assert records[baseline["exp_id"]]["promoted_from"] == source["exp_id"]


def test_csi1000_history_has_distinct_metrics_and_no_promotion_into_alla(tmp_path):
    all_a = row()
    historical = {"exp_id": "history/model", "phase": "M", "state": "completed",
                  "seeds": [42], "metrics_summary": {"csi1000": {"ic_mean": .1, "rank_ic_mean": .2}},
                  "baseline_version": "v99", "promoted_to": all_a["exp_id"]}
    c = catalog(tmp_path, [all_a, historical], [model_doc(), None])
    manifest(tmp_path, all_a["exp_id"])
    manifest(tmp_path, historical["exp_id"], track="csi1000")
    snapshot = c.snapshot()
    assert len(snapshot["records"]) == 2
    records = {r["id"]: r for r in snapshot["records"]}
    assert records[all_a["exp_id"]]["current"]
    assert records[historical["exp_id"]]["track"] == "csi1000"
    assert not records[historical["exp_id"]]["comparable"]
    assert records[historical["exp_id"]]["promoted_to"] == ""
    with pytest.raises(ValueError):
        c.compare([all_a["exp_id"], historical["exp_id"]])
