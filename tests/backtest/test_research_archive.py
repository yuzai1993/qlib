"""Keep the real research archive navigable after future repository edits."""
import hashlib
import json
from pathlib import Path

from backtest.scripts.experiment_registry import load_registry
from backtest.scripts.experiment_archive import scan_experiment_archives
from backtest.scripts.research_layout import resolve_research_path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "backtest/experiments"


def test_registered_experiments_have_local_records_and_asset_manifests():
    rows = load_registry(ARCHIVE / "registry.jsonl")
    entries = [json.loads(line) for line in (ARCHIVE / "registry.jsonl").read_text().splitlines() if line.strip()]
    directories = {item["exp_id"]: ROOT / item["directory"] for item in scan_experiment_archives(ROOT)["experiments"]}
    assert rows
    for row, entry in zip(rows, entries):
        directory = directories[row["exp_id"]]
        expected_parent = directory / "records"
        assert resolve_research_path(ARCHIVE / entry["detail_path"], ROOT).parent == expected_parent
        metadata = json.loads((directory / "metadata.json").read_text())
        assert metadata["exp_id"] == row["exp_id"]
        assert metadata["registry_authority"] == "backtest/experiments/registry.jsonl"


def test_archived_assets_and_frozen_config_hashes_are_present():
    ids = {row["exp_id"] for row in load_registry(ARCHIVE / "registry.jsonl")}
    directories = {item["exp_id"]: ROOT / item["directory"] for item in scan_experiment_archives(ROOT)["experiments"]}
    for exp_id in ids:
        metadata = json.loads((directories[exp_id] / "metadata.json").read_text())
        for key in ("reports", "docs", "data", "shared_assets", "records"):
            for relative in metadata.get(key, []):
                path = resolve_research_path(relative, ROOT)
                assert path.is_file() and path.resolve().is_relative_to(ROOT), (exp_id, key, relative)
        for config in metadata.get("configs", []):
            path = resolve_research_path(config["path"], ROOT)
            assert path.is_file(), (exp_id, config["path"])
            assert hashlib.sha256(path.read_bytes()).hexdigest() == config["sha256"], config["path"]


def test_all_experiments_are_discoverable_in_their_typed_archive():
    scan = scan_experiment_archives(ROOT)
    assert scan["mode"] == "typed"
    assert not scan["errors"]
    ids = {row["exp_id"] for row in load_registry(ARCHIVE / "registry.jsonl")}
    assert {item["exp_id"] for item in scan["experiments"]} == ids
    for item in scan["experiments"]:
        expected_root = "models" if item["kind"] == "model" else "strategies"
        assert Path(item["directory"]).relative_to("backtest/experiments").parts[0] == expected_root


def test_summary_reports_stay_in_the_shared_report_directory():
    for name in ("phase_m_v1_report.html", "phase_m_v1_bt_report.html"):
        assert (ARCHIVE / "reports" / name).is_file()
        assert not (ARCHIVE / name).exists()
    for name in ("report.html", "strategy_stability_report.html"):
        assert not (ARCHIVE / "reports" / name).exists()


def test_real_archive_contains_only_the_retained_research_scope():
    manifests = scan_experiment_archives(ROOT)["experiments"]
    latest = {row["exp_id"]: row for row in load_registry(ARCHIVE / "registry.jsonl")}
    for manifest in manifests:
        if manifest["kind"] == "model":
            assert manifest["track"] == "all_a"
        else:
            assert latest[manifest["exp_id"]]["direction"] == "phase-m-v1-bt"


def test_model_archives_are_classified_without_renaming_historical_ids():
    from collections import Counter

    models = [m for m in scan_experiment_archives(ROOT)["experiments"] if m["kind"] == "model"]
    assert Counter(m["experiment_type"] for m in models) == {
        "training_target": 1, "early_stopping": 3, "model_structure": 2,
    }
    assert not (ARCHIVE / "models/regime-adapt").exists()
    assert not (ARCHIVE / "models/diag").exists()
    for model in models:
        assert model["archive_path"] == model["directory"]
        assert isinstance(model["analysis"]["conclusion"], str)


def test_retained_experiments_have_a_readable_report_and_prefilled_conclusion():
    from quant_platform.research import ResearchCatalog

    records = ResearchCatalog(ROOT).snapshot()["records"]
    assert len(records) == 23
    for record in records:
        assert any(r["kind"] == "report" and r["available"] for r in record["resources"]), record["id"]
        assert 1 <= len(record["analysis"]["conclusion"]) <= 500
    assert not (ARCHIVE / "models/model-structure/de-v2-s42").exists()
    assert not (ARCHIVE / "models/evaluation/window-edge").exists()


def test_redundant_label_entries_are_consolidated_without_losing_study_sources():
    group = ARCHIVE / "models/training-target/horizon-comparison"
    metadata = json.loads((group / "metadata.json").read_text())
    active = {r["exp_id"] for r in load_registry(ARCHIVE / "registry.jsonl")}
    removed = {f"regime-adapt/m0-h{h}-t3h5es-v1" for h in (1, 5, 10)}
    removed.add("regime-adapt/m0-h20-t3h5es-densemble-s42-v1")
    assert not removed & active
    for directory in ("training-target/h1", "training-target/h5", "training-target/h10",
                      "model-structure/de-v3-s42"):
        assert not (ARCHIVE / "models" / directory).exists()
    records = metadata["training_target_comparison"]["archived_records"]
    assert len(records) == 3
    retained = {}
    for ref in records:
        path = group / ref["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == ref["sha256"]
        value = json.loads(path.read_text())
        retained[value["train_label_horizon"]] = value
        source = resolve_research_path(value["portable_eval_output"], ROOT)
        assert source.parent == group / "metrics"
        doc = json.loads(source.read_text())
        cell = doc["pools"]["all"]["ensemble"]["head"]["3"]["5"]
        assert cell["net_ann"] == value["metrics"]["net_ann"]
    assert set(retained) == {1, 5, 10}


def test_historical_v1_is_a_control_source_without_a_duplicate_active_experiment():
    group = ARCHIVE / "models/training-target/horizon-comparison"
    metadata = json.loads((group / "metadata.json").read_text())
    historical_id = "regime-adapt/m0-h20-label-v4"
    active = {row["exp_id"] for row in load_registry(ARCHIVE / "registry.jsonl")}
    assert historical_id not in active
    assert not (ARCHIVE / "models/training-target/h20-v1").exists()
    assert len(metadata["historical_baselines"]) == 1
    source = metadata["historical_baselines"][0]
    path = group / source["path"]
    assert path.parent == group / "baseline-records"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
    row = json.loads(path.read_text())
    assert row["exp_id"] == historical_id and row["baseline_version"] == "v1"
    assert row["metrics"]["net_ann"] == .2933431911523481
    metrics = json.loads(resolve_research_path(row["portable_eval_output"], ROOT).read_text())
    assert metrics["pools"]["all"]["ensemble"]["head"]["3"]["5"]["net_ann"] == row["metrics"]["net_ann"]
    assert metadata["training_target_comparison"]["control_ref"] == "regime-adapt/m0-h20-t3h5es-v1"
    original = (ARCHIVE / "models/training-target/h20-v1/records" / path.name).as_posix()
    assert resolve_research_path(original, ROOT) == path


def test_strategy_archives_follow_trading_rules_without_changing_stable_ids():
    from collections import Counter

    strategies = [m for m in scan_experiment_archives(ROOT)["experiments"] if m["kind"] == "strategy"]
    assert Counter(Path(m["directory"]).relative_to("backtest/experiments/strategies").parts[0]
                   for m in strategies) == {"topk-dropout": 12, "cohort-ladder": 5}
    for role in ("baseline", "diag", "regime-adapt"):
        assert not (ARCHIVE / "strategies" / role).exists()
    for strategy in strategies:
        assert strategy["archive_path"] == strategy["directory"]
        assert resolve_research_path(f"backtest/experiments/strategies/{strategy['exp_id']}", ROOT) == ROOT / strategy["directory"]


def test_bt_v2_retains_one_strategy_entry_with_original_candidate_sources():
    from quant_platform.research import ResearchCatalog

    snapshot = ResearchCatalog(ROOT).snapshot()
    candidate = "regime-adapt/m0h20es-all-top5d1-bt"
    baseline = "baseline/phase-m-v1-bt-v2"
    records = {row["id"]: row for row in snapshot["records"]}
    assert candidate not in records
    assert records[baseline]["version"] == "v2" and records[baseline]["is_baseline"]
    matching = [row for row in records.values() if row["kind"] == "strategy"
                and row["model_summary"]["version"] == "v2"
                and row["strategy_summary"]["name"] == "Top5换1·持1"]
    assert [row["id"] for row in matching] == [baseline]
    directory = ARCHIVE / "strategies/topk-dropout/v2-top5-hold-f100/variants/bt-v2"
    assert not (ARCHIVE / "strategies/topk-dropout/v2-top5-drop1").exists()
    legacy = "backtest/experiments/regime-adapt/m0h20es-all-top5d1-bt/metrics/all_top5d1_m0h20es.json"
    metric = directory / "metrics/all_top5d1_m0h20es.json"
    assert resolve_research_path(legacy, ROOT) == metric
    assert hashlib.sha256(metric.read_bytes()).hexdigest() == "d6e7e819cb69877fdf33fcf1bb00320d473d8cbc1875eb0cbf7bcf03df94ef1e"
    for digest in ("317a896eb33f0730b61fe0034cabc069759ab4b5c006a9a1ea42c167588a7682",
                   "2fb9a969d8d9a7ad8ce028b4b85cc1ef3b9b26dd44eb1574673612bcb3b6842b"):
        assert hashlib.sha256((directory / f"records/{digest}.json").read_bytes()).hexdigest() == digest
    assert records[baseline]["metrics"]["annualized_return"] == .23130372925034037
    assert len([row for row in snapshot["benchmarks"] if row["kind"] == "strategy"]) == 5
    assert records["baseline/phase-m-v1-bt-v5"]["current"]
