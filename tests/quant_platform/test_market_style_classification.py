import json
import hashlib

from backtest.scripts.maintenance.experiment_archive import classify_experiment
from backtest.scripts.register.experiment_registry import append_registry
from quant_platform.research import ResearchCatalog


def test_classification_is_all_a_without_stock_return_protocol():
    info = classify_experiment(
        {"exp_id": "model-structure/style-test", "phase": "M", "classification_protocol": "monthly-k4-next-day-v1"}
    )
    assert info["track"] == "all_a"
    assert info["kind"] == "model"


def test_catalog_uses_classifier_metrics_without_implying_stock_baseline(tmp_path):
    eid = "model-structure/style-test"
    base = tmp_path / "backtest/experiments/models" / eid
    (base / "metrics").mkdir(parents=True)
    (base / "reports").mkdir()
    (base / "reports/detail.html").write_text("<p>classification report</p>")
    seeds = [42, 1000, 2000, 3000, 4000]
    cell = {
        "accuracy": 0.65,
        "macro_f1": 0.55,
        "balanced_accuracy": 0.6,
        "per_class": {c: {"precision": 0.5, "recall": 0.6, "f1": 0.55, "support": 10} for c in "DFTP"},
    }
    bundle = {
        "classification_protocol": "monthly-k4-next-day-v1",
        "classes": list("DFTP"),
        "seeds": seeds,
        "chosen_candidate": "lgb_core",
        "candidates": {"lgb_core": {"evaluation": cell}},
        "baselines": {"majority": cell},
    }
    (base / "metrics/evaluation.json").write_text(json.dumps(bundle))
    row = {
        "exp_id": eid,
        "phase": "M",
        "classification_protocol": "monthly-k4-next-day-v1",
        "state": "completed",
        "date": "2026-11-08",
        "seeds": seeds,
        "baseline_ref": "market-style/majority-prior-v1",
        "portable_eval_output": str((base / "metrics/evaluation.json").relative_to(tmp_path)),
        "portable_eval_sha256": hashlib.sha256((base / "metrics/evaluation.json").read_bytes()).hexdigest(),
        "detail_report": str((base / "reports/detail.html").relative_to(tmp_path)),
    }
    append_registry(tmp_path / "backtest/experiments/registry.jsonl", row)
    record = ResearchCatalog(tmp_path).snapshot()["records"][0]
    assert record["track"] == "all_a"
    assert record["metrics"]["accuracy"] == 0.65
    assert "net_ann" not in record["metrics"]
    assert not record["comparable"] and not record["is_baseline"]
    assert record["core_results"]["kind"] == "market_style_classification"
    assert len(record["core_results"]["records"]) == 2
    assert {v["key"] for v in record["metric_definitions"]} >= {"accuracy", "P_recall"}
    assert not any("top3" in w for w in record["warnings"])
    assert record["official"]
    (base / "metrics/evaluation.json").write_text(json.dumps({**bundle, "tampered": True}))
    changed = ResearchCatalog(tmp_path).snapshot()["records"][0]
    assert not changed["official"]
    assert changed["source_status"] == "invalid"
