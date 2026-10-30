"""Read-only catalog contracts, including deliberately misleading source metadata."""

import json
import pytest

from quant_platform.research import ResearchCatalog


SEEDS = [42, 1000, 2000, 3000, 4000]
WINDOW = ["2020-08-03", "2026-07-31"]


def write_json(root, rel, value):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def model_doc(seeds=SEEDS):
    cell = {"net_ann": 0.31, "net_ann_vol": 0.4, "net_sharpe": 0.775, "ann": 0.35, "turnover": 0.18}
    ensemble = {
        "head": {"3": {"5": cell}, "5": {"5": {"net_ann": 9}}},
        "head_years": {"2026": {"3": {"5": {"net_ann": -0.03}}}},
        "head_regimes": {"D": {"3": {"5": {"net_ann": 0.12}}}},
        "h5": {"rank_ic_mean": 0.095},
    }
    return {
        "effective_eval_segment": WINDOW,
        "official_signal": "seed_zscore_mean",
        "eval_labels": {"h5": "Ref($close, -6)/Ref($close, -1) - 1"},
        "head_universe": "t+1 可成交（剔除次日涨停封板与零成交量）",
        "primary_cell": {"k": 5, "h": 5},
        "cost_round_trip": 0.00092,
        "filters": {"st_filter": "daily", "min_listing_days": 60, "min_amount": 1e7},
        "pools": {"all": {"seeds": {str(s): {} for s in seeds}, "ensemble": ensemble}},
    }


def strategy_doc(seeds=SEEDS):
    return {
        "backtest_window": WINDOW,
        "pool": "all",
        "seeds": {},
        "account": 1000000,
        "benchmark": {"equal_weight_csv": "bench.csv"},
        "fees": {"open_cost": 0.00021, "close_cost": 0.00071, "min_cost": 5, "trade_unit": 100},
        "universe_filter": {
            "st_filter": "daily",
            "st_daily": "scripts/data_collector/tushare/st_daily.csv",
            "min_amount": 1e7,
            "min_listing_days": 60,
        },
        "ensemble": {
            "method": "daily_zscore_mean",
            "source_preds": [f"backtest/result/m_s{s}_pred.pkl" for s in seeds],
            "full_period": {"annualized_return": 0.26, "sharpe_ratio": 1.04},
            "years": {"2026": {"annualized_return": 0.038}},
            "regimes": {"D": {"annualized_return": 0.15}},
        },
    }


def row(eid="model/v4", kind="model", **changes):
    value = {
        "exp_id": eid,
        "state": "completed",
        "seeds": SEEDS,
        "date": "2026-10-28",
        "eval_output": f"backtest/experiments/{eid.replace('/', '_')}.json",
    }
    value.update(
        {"phase_m_protocol": "v1", "baseline_version": "v4"}
        if kind == "model"
        else {"report_kind": "phase_m_v1_bt_baseline", "bt_version": "v4"}
    )
    value.update(changes)
    return value


def catalog(tmp_path, rows, docs=None):
    registry = tmp_path / "backtest/experiments/registry.jsonl"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    for r, doc in zip(rows, docs or [model_doc() for _ in rows]):
        if doc is not None and r.get("eval_output"):
            write_json(tmp_path, r["eval_output"], doc)
    return ResearchCatalog(tmp_path)


def test_model_fixed_primary_and_official_slices(tmp_path):
    c = catalog(tmp_path, [row()])
    record = c.snapshot()["records"][0]
    assert record["metrics"]["net_ann"] == 0.31
    assert record["metrics"]["rank_ic_mean"] == 0.095
    assert record["years"]["2026"]["net_ann"] == -0.03
    assert record["regimes"]["D"]["net_ann"] == 0.12
    assert record["official"] and record["comparable"]
    assert record["window"] == {"start": WINDOW[0], "end": WINDOW[1]}


def test_strategy_source_predictions_prove_five_seeds(tmp_path):
    c = catalog(tmp_path, [row("bt/v4", "strategy")], [strategy_doc()])
    record = c.snapshot()["records"][0]
    assert record["kind"] == "strategy"
    assert record["metrics"]["annualized_return"] == 0.26
    assert record["years"]["2026"]["annualized_return"] == 0.038
    assert record["regimes"]["D"]["annualized_return"] == 0.15
    assert record["seeds"] == SEEDS
    assert record["official"]


@pytest.mark.parametrize("kind", ["model", "strategy"])
def test_portable_metrics_survive_original_result_cleanup(tmp_path, kind):
    portable = "backtest/experiments/retained_metrics.json"
    r = row("portable/baseline", kind, portable_eval_output=portable)
    c = catalog(tmp_path, [r], [None])
    path = write_json(tmp_path, portable, model_doc() if kind == "model" else strategy_doc())

    record = c.snapshot()["records"][0]

    assert record["source_status"] == "available"
    assert record["source"] == portable
    assert record["original_source"] == r["eval_output"]
    key = "net_ann" if kind == "model" else "annualized_return"
    assert record["metrics"][key] == (0.31 if kind == "model" else 0.26)
    metrics_resource = next(resource for resource in record["resources"] if resource["kind"] == "metrics")
    assert metrics_resource["available"]
    assert c.resource(metrics_resource["id"]) == path


def test_portable_metrics_take_precedence_over_original_result(tmp_path):
    portable = "backtest/experiments/retained_metrics.json"
    r = row("bt/portable", "strategy", portable_eval_output=portable)
    original = strategy_doc()
    original["ensemble"]["full_period"]["annualized_return"] = 0.01
    c = catalog(tmp_path, [r], [original])
    write_json(tmp_path, portable, strategy_doc())

    record = c.snapshot()["records"][0]

    assert record["metrics"]["annualized_return"] == 0.26
    original_resource = next(resource for resource in record["resources"] if resource["kind"] == "original_metrics")
    assert c.resource(original_resource["id"]) == tmp_path / r["eval_output"]


@pytest.mark.parametrize("status", ["unsafe", "missing", "invalid"])
def test_declared_portable_metrics_failure_does_not_silently_use_original(tmp_path, status):
    portable = "../outside.json" if status == "unsafe" else "backtest/experiments/retained_metrics.json"
    r = row("bt/portable", "strategy", portable_eval_output=portable)
    c = catalog(tmp_path, [r], [strategy_doc()])
    if status == "invalid":
        write_json(tmp_path, portable, {}).write_text("{broken", encoding="utf-8")

    record = c.snapshot()["records"][0]

    assert record["source_status"] == status
    assert record["source"] == portable
    assert record["metrics"]["annualized_return"] is None
    assert not record["official"]


@pytest.mark.parametrize("portable", [None, "", []])
def test_empty_declared_portable_source_does_not_hide_corrupt_metadata(tmp_path, portable):
    r = row("bt/portable", "strategy", portable_eval_output=portable)
    c = catalog(tmp_path, [r], [strategy_doc()])

    record = c.snapshot()["records"][0]

    assert record["source_status"] == "missing"
    assert record["metrics"]["annualized_return"] is None
    assert not record["official"]


@pytest.mark.parametrize("declared,actual", [(SEEDS, [42]), ([42], SEEDS), (SEEDS, [])])
def test_ensemble_field_alone_cannot_certify_five_seeds(tmp_path, declared, actual):
    c = catalog(tmp_path, [row(seeds=declared)], [model_doc(actual)])
    record = c.snapshot()["records"][0]
    assert not record["official"] and not record["comparable"]
    assert record["warnings"]
    with pytest.raises(ValueError):
        c.compare([record["id"]])


def test_current_baseline_uses_numeric_versions_never_metrics(tmp_path):
    rows = [
        row("m/v9", baseline_version="v9"),
        row("m/v10", baseline_version="v10"),
        row("m/trial", baseline_version=None),
    ]
    records = catalog(tmp_path, rows).snapshot()["records"]
    baseline = [r for r in records if r["is_baseline"]]
    assert [r["version"] for r in baseline] == ["v9", "v10"]
    assert [r["id"] for r in records if r["current"]] == ["m/v10"]


def test_comparison_rejects_kinds_pools_windows_diagnostics_and_unknown(tmp_path):
    rows = [
        row("m/a"),
        row("m/b"),
        row("bt/a", "strategy"),
        row("m/pool", eval_pool="csi1000", comparison_scope="paired_same_CSI1000_pool"),
        row("m/running", state="running"),
        row("bt/ext", "strategy", report_kind="phase_m_v1_bt_extended_window_diagnostic"),
    ]
    docs = [model_doc(), model_doc(), strategy_doc(), model_doc(), None, strategy_doc()]
    docs[5]["backtest_window"] = [WINDOW[0], "2026-10-23"]
    c = catalog(tmp_path, rows, docs)
    assert len(c.compare(["m/a", "m/b"])["records"]) == 2
    for ids in [["m/a", "bt/a"], ["m/a", "m/pool"], ["m/running"], ["bt/a", "bt/ext"], ["unknown"], []]:
        with pytest.raises(ValueError):
            c.compare(ids)
    by_id = {r["id"]: r for r in c.snapshot()["records"]}
    assert by_id["m/running"]["status"] == "running"
    assert by_id["m/pool"]["scope"] == "pool_diagnostic"
    assert by_id["bt/ext"]["scope"] == "extended_window_diagnostic"


def test_compare_separates_cost_filter_and_data_vintage(tmp_path):
    rows = [row("m/a"), row("m/fees"), row("m/filter"), row("m/data")]
    docs = [model_doc() for _ in rows]
    docs[1]["cost_round_trip"] = 0.01
    docs[2]["filters"]["st_filter"] = "snapshot"
    docs[3]["data_version"] = "2030-01-01"
    c = catalog(tmp_path, rows, docs)
    for r in rows[1:]:
        with pytest.raises(ValueError):
            c.compare(["m/a", r["exp_id"]])


@pytest.mark.parametrize(
    "field,value",
    [
        ("eval_labels", {"h5": "Ref($close, -5)/$close - 1"}),
        ("head_universe", "all"),
        ("eval_labels", {"h5": None}),
        ("eval_labels", None),
        ("head_universe", None),
    ],
)
def test_comparison_rejects_explicit_model_label_or_tradability_changes(tmp_path, field, value):
    changed = model_doc()
    changed[field] = value
    c = catalog(tmp_path, [row("m/reference"), row("m/changed")], [model_doc(), changed])
    with pytest.raises(ValueError):
        c.compare(["m/reference", "m/changed"])


@pytest.mark.parametrize("st_daily", [None, "", "none", "other_st_daily.csv"])
def test_comparison_uses_actual_strategy_st_daily_field(tmp_path, st_daily):
    changed = strategy_doc()
    changed["universe_filter"]["st_daily"] = st_daily
    c = catalog(
        tmp_path,
        [row("bt/reference", "strategy"), row("bt/changed", "strategy")],
        [strategy_doc(), changed],
    )
    with pytest.raises(ValueError):
        c.compare(["bt/reference", "bt/changed"])


def test_omitted_historical_evaluation_metadata_uses_warned_standard_defaults(tmp_path):
    old_model, old_strategy = model_doc(), strategy_doc()
    old_model.pop("eval_labels")
    old_model.pop("head_universe")
    old_strategy["universe_filter"].pop("st_daily")
    modern_model = model_doc()
    modern_model["eval_labels"]["h5"] = "Ref($close,-6)/Ref($close,-1)-1"
    c = catalog(
        tmp_path,
        [row("m/old"), row("m/new"), row("bt/old", "strategy"), row("bt/new", "strategy")],
        [old_model, modern_model, old_strategy, strategy_doc()],
    )
    for ids in [["m/old", "m/new"], ["bt/old", "bt/new"]]:
        records = c.compare(ids)["records"]
        assert len(records) == 2
        assert any("历史" in warning and "补足" in warning for warning in records[0]["warnings"])


def test_missing_invalid_json_and_registry_lines_preserve_other_records(tmp_path):
    rows = [row("m/good"), row("m/missing"), row("m/invalid")]
    c = catalog(tmp_path, rows, [model_doc(), None, None])
    write_json(tmp_path, rows[2]["eval_output"], {}).write_text("{broken", encoding="utf-8")
    registry = tmp_path / "backtest/experiments/registry.jsonl"
    registry.write_text(registry.read_text() + "\nnot json\n[]", encoding="utf-8")
    snapshot = c.snapshot()
    assert len(snapshot["records"]) == 3
    assert snapshot["warnings"]
    by_id = {r["id"]: r for r in snapshot["records"]}
    assert by_id["m/missing"]["source_status"] == "missing"
    assert by_id["m/invalid"]["source_status"] == "invalid"
    assert not by_id["m/missing"]["official"]


def test_legacy_seed_mean_is_labeled_and_not_official(tmp_path):
    doc = model_doc()
    pool = doc["pools"]["all"]
    pool["seed_mean"] = pool.pop("ensemble")
    record = catalog(tmp_path, [row()], [doc]).snapshot()["records"][0]
    assert record["metrics"]["net_ann"] == 0.31
    assert not record["official"] and not record["comparable"]
    assert any("seed_mean" in w for w in record["warnings"])


def test_refresh_and_finite_json_serialization(tmp_path):
    r = row()
    c = catalog(tmp_path, [r])
    assert c.snapshot()["records"][0]["metrics"]["net_ann"] == 0.31
    doc = model_doc()
    doc["pools"]["all"]["ensemble"]["head"]["3"]["5"]["net_ann"] = float("nan")
    write_json(tmp_path, r["eval_output"], doc)
    snapshot = c.snapshot()
    assert snapshot["records"][0]["metrics"]["net_ann"] is None
    json.dumps(snapshot, allow_nan=False)
    assert snapshot["sources"]["root"] == str(tmp_path)


def test_resources_allow_only_registered_safe_paths_and_recheck_symlinks(tmp_path):
    report = "backtest/experiments/detail.html"
    r = row(detail_report=report, configs=["../../secret.txt", "/etc/passwd", "backtest/experiments/escape.html"])
    c = catalog(tmp_path, [r])
    safe = tmp_path / report
    safe.write_text("<h1>report</h1>", encoding="utf-8")
    external = tmp_path.parent / (tmp_path.name + "-secret.txt")
    external.write_text("secret", encoding="utf-8")
    (tmp_path / "backtest/experiments/escape.html").symlink_to(external)
    resources = c.snapshot()["records"][0]["resources"]
    rid = next(x["id"] for x in resources if x["name"] == "detail.html")
    assert c.resource(rid) == safe
    assert c.resource("../../secret.txt") is None
    assert all(not x["available"] for x in resources if x["name"] in ["secret.txt", "passwd", "escape.html"])
    safe.unlink()
    safe.symlink_to(external)
    assert c.resource(rid) is None


def test_empty_or_missing_registry_has_explicit_warning(tmp_path):
    snapshot = ResearchCatalog(tmp_path).snapshot()
    assert snapshot["records"] == [] and snapshot["warnings"]


def test_legacy_backtest_retained_but_excluded_from_official_comparison(tmp_path):
    r = row("legacy/bt", "strategy", report_kind="phase_m_v1_backtest", state="superseded")
    record = catalog(tmp_path, [r], [strategy_doc()]).snapshot()["records"][0]
    assert record["status"] == "superseded"
    assert not record["comparable"]


def test_historical_strategy_registry_evidence_and_numeric_protocol_normalization(tmp_path):
    rows = [row("bt/v3", "strategy", bt_version="v3"), row("bt/v4", "strategy")]
    old, new = strategy_doc(), strategy_doc()
    old["ensemble"].pop("source_preds")
    old["ensemble"].pop("method")
    old.pop("fees")
    new["fees"]["min_cost"] = 5.0
    c = catalog(tmp_path, rows, [old, new])
    comparison = c.compare(["bt/v3", "bt/v4"])
    assert len(comparison["records"]) == 2
    assert any("registry" in w for w in comparison["records"][0]["warnings"])
    assert all(not any(k.startswith("_") for k in r) for r in c.snapshot()["records"])


def test_strategy_contradictory_source_seed_list_cannot_use_registry_fallback(tmp_path):
    c = catalog(tmp_path, [row("bt/trial", "strategy")], [strategy_doc([42])])
    record = c.snapshot()["records"][0]
    assert not record["official"]
    assert record["seeds"] == [42]


@pytest.mark.parametrize(
    "field,value",
    [
        ("official_signal", {}),
        ("official_signal", "seed_mean"),
        ("cost_round_trip", float("nan")),
        ("cost_round_trip", "invalid"),
    ],
)
def test_invalid_protocol_metadata_is_visible_and_not_comparable(tmp_path, field, value):
    doc = model_doc()
    doc[field] = value
    record = catalog(tmp_path, [row()], [doc]).snapshot()["records"][0]
    assert not record["comparable"]
    assert record["warnings"]


def test_extra_or_unidentified_source_prediction_cannot_be_official(tmp_path):
    doc = strategy_doc()
    doc["ensemble"]["source_preds"].append("backtest/result/unknown_pred.pkl")
    record = catalog(tmp_path, [row("bt/extra", "strategy")], [doc]).snapshot()["records"][0]
    assert not record["official"]


def test_resource_does_not_expose_unregistered_or_cross_directory_symlink(tmp_path):
    r = row(detail_report="backtest/experiments/cross.html")
    c = catalog(tmp_path, [r])
    other = write_json(tmp_path, "backtest/configs/secret.json", {"secret": True})
    (tmp_path / "backtest/experiments/cross.html").symlink_to(other)
    resources = c.snapshot()["records"][0]["resources"]
    cross = next(x for x in resources if x["name"] == "cross.html")
    assert not cross["available"] and c.resource(cross["id"]) is None
    assert c.resource("secret.json") is None


def test_nonfinite_registry_metadata_does_not_escape_json_response(tmp_path):
    snapshot = catalog(tmp_path, [row(baseline_ref=float("nan"))]).snapshot()
    json.dumps(snapshot, allow_nan=False)


@pytest.mark.parametrize("note", ["实验备注", None])
def test_experiment_conclusion_remains_distinct_from_note(tmp_path, note):
    record = catalog(tmp_path, [row(conclusion="未达预期", note=note)]).snapshot()["records"][0]
    assert record["conclusion"] == "未达预期"
    assert record["note"] == (note or "未达预期")


@pytest.mark.parametrize("kind", ["model", "strategy"])
def test_promoted_versions_resolve_baseline_explicit_and_reverse_links(tmp_path, kind):
    trial_fields = (
        {"baseline_version": None} if kind == "model" else {"report_kind": "phase_m_v1_bt", "bt_version": None}
    )
    version_field = "baseline_version" if kind == "model" else "bt_version"
    rows = [
        row("trial/explicit", kind, promoted_to="baseline/v2", **trial_fields),
        row("baseline/v2", kind, **{version_field: "v2"}),
        row("trial/reverse", kind, **trial_fields),
        row("baseline/v3", kind, promoted_from="trial/reverse", **{version_field: "v3"}),
        row("trial/unpromoted", kind, note="看起来更好，晋升为 v99", **trial_fields),
    ]
    doc = model_doc if kind == "model" else strategy_doc
    records = {r["id"]: r for r in catalog(tmp_path, rows, [doc() for _ in rows]).snapshot()["records"]}
    assert records["baseline/v2"]["promoted_version"] == "v2"
    assert records["baseline/v3"]["promoted_version"] == "v3"
    assert records["trial/explicit"]["promoted_version"] == "v2"
    assert records["trial/explicit"]["promoted_to"] == "baseline/v2"
    assert records["trial/reverse"]["promoted_version"] == "v3"
    assert records["trial/reverse"]["promoted_to"] == "baseline/v3"
    assert records["baseline/v3"]["promoted_from"] == "trial/reverse"
    assert records["trial/unpromoted"]["promoted_version"] == ""


def test_promotion_links_reject_unknown_cross_kind_and_nonbaseline_targets(tmp_path):
    rows = [
        row("model/trial", baseline_version=None, promoted_to="strategy/v4"),
        row("model/missing", baseline_version=None, promoted_to="missing/v9"),
        row("model/notbaseline", baseline_version=None, promoted_to="model/trial"),
        row("strategy/v4", "strategy", promoted_from="model/trial"),
        row("model/v4", promoted_from="missing/trial"),
    ]
    docs = [model_doc(), model_doc(), model_doc(), strategy_doc(), model_doc()]
    records = {r["id"]: r for r in catalog(tmp_path, rows, docs).snapshot()["records"]}
    for eid in ["model/trial", "model/missing", "model/notbaseline"]:
        assert records[eid]["promoted_version"] == ""
        assert records[eid]["promoted_to"] == ""
    assert records["strategy/v4"]["promoted_from"] == ""
    assert records["model/v4"]["promoted_from"] == ""


def test_strategy_benchmark_return_uses_same_full_and_dimensional_sources(tmp_path):
    doc = strategy_doc()
    doc["ensemble"]["full_period"]["benchmark_cumulative_return"] = 0.42
    doc["ensemble"]["years"]["2026"]["benchmark_cumulative_return"] = -0.07
    doc["ensemble"]["regimes"]["D"]["benchmark_cumulative_return"] = 0.11
    snapshot = catalog(tmp_path, [row("bt/v4", "strategy")], [doc]).snapshot()
    record = snapshot["records"][0]
    assert record["metrics"]["benchmark_cumulative_return"] == 0.42
    assert record["years"]["2026"]["benchmark_cumulative_return"] == -0.07
    assert record["regimes"]["D"]["benchmark_cumulative_return"] == 0.11
    assert {"key": "benchmark_cumulative_return", "label": "基准涨幅", "format": "pct"} in snapshot["metric_definitions"][
        "strategy"
    ]
