"""Classification lifecycle guards; all artifacts are disposable tmp_path fixtures."""

import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def api():
    return importlib.import_module("backtest.scripts.maintenance.style_harness")


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def record(path):
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


@pytest.fixture
def campaign(tmp_path):
    root = tmp_path / "repo"
    for name in [
        "AGENTS.md",
        "backtest/EXPERIMENT_STANDARD.md",
        "docs/research/experiment_harness.md",
    ]:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("frozen policy")
    rel = "backtest/experiments/models/model-structure/style-classifier-k4-20261108-v1"
    archive = root / rel
    write(
        archive / "metadata.json",
        {
            "exp_id": "model-structure/style-classifier-k4-20261108-v1",
            "archive_path": rel,
            "kind": "model",
            "track": "all_a",
        },
    )
    config = write(archive / "configs/scientific.json", {"frozen": True})
    plan = {
        "schema_version": 1,
        "track": "market_style_classifier",
        "campaign_id": "style-20261108-v1",
        "exp_id": "model-structure/style-classifier-k4-20261108-v1",
        "archive_path": rel,
        "baseline_ref": "market-style/majority-prior-v1",
        "data_version": "fixture-1",
        "classes": ["D", "F", "T", "P"],
        "seeds": [42, 1000, 2000, 3000, 4000],
        "candidates": ["logistic_full", "lgb_core", "lgb_full", "extra_full"],
        "classification_protocol": "monthly-k4-next-day-v1",
        "inputs": [str(config)],
        "budget": {},
    }
    path = write(archive / "configs/harness-plan.json", plan)
    return root, archive, path, plan


def init(campaign):
    root, archive, plan, _ = campaign
    state = api().initialize(plan, repo_root=root)
    return root, archive, root / state["state_path"], state


def event(root, path, **payload):
    state = api().inspect_state(path, repo_root=root, check_drift=False)
    return api().append_event(
        path, payload, expected_revision=state["revision"], repo_root=root
    )


def prepare(campaign, labels=None):
    import pandas as pd

    root, archive, path, state = init(campaign)
    event(root, path, type="start_preparation", argv=["python", "prepare.py"])
    dates = pd.to_datetime(
        [
            "2017-01-03",
            "2017-01-04",
            "2017-01-05",
            "2017-01-06",
            "2018-01-03",
            "2020-08-03",
        ]
    )
    dates.name = "date"
    targets = pd.DataFrame(
        {
            "target_date": pd.to_datetime(
                [
                    "2017-01-04",
                    "2017-01-05",
                    "2017-01-06",
                    "2017-01-09",
                    "2018-01-04",
                    "2020-08-04",
                ]
            ),
            "label": labels if labels is not None else ["D", "F", "T", "P", "F", "T"],
        },
        index=dates,
    )
    data = archive / "data"
    data.mkdir()
    pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}, index=dates).to_parquet(
        data / "features.parquet"
    )
    targets.to_parquet(data / "targets.parquet")
    audit = write(archive / "docs/label-audit.json", {"accepted": True})
    features, target = data / "features.parquet", data / "targets.parquet"
    manifest = write(
        archive / "configs/feature-manifest.json",
        {
            "classes": ["D", "F", "T", "P"],
            "classification_protocol": "monthly-k4-next-day-v1",
            "data_version": "fixture-1",
            "full_features": ["x"],
            "core_features": ["x"],
            "dataset_files": {"features": record(features), "targets": record(target)},
            "label_audit": record(audit),
        },
    )
    result = write(
        archive / "docs/preparation-result.json",
        {
            "status": "completed",
            "classification_fits": 0,
            "label_fits": 1,
            "label_initializations": 100,
            "features": record(features),
            "targets": record(target),
            "manifest": record(manifest),
        },
    )
    inputs = dict(
        features=str(features),
        targets=str(target),
        manifest=str(manifest),
        label_audit=str(audit),
        preparation_result=str(result),
    )
    event(
        root,
        path,
        type="prepared",
        outcome="completed",
        inputs=inputs,
        evidence=list(inputs.values()),
    )
    return root, archive, path, inputs


def job_fixture(
    root, archive, inputs, candidate="logistic_full", seed=42, phase="inner", inner=None
):
    import pandas as pd
    from backtest.market_style.evaluation import classification_metrics

    target = pd.read_parquet(inputs["targets"]).iloc[[4 if phase == "inner" else 5]]
    probabilities = (
        [[0.1, 0.6, 0.2, 0.1]] if phase == "inner" else [[0.1, 0.1, 0.7, 0.1]]
    )
    pred = target.copy()
    pred[["p_D", "p_F", "p_T", "p_P"]] = probabilities
    out = archive / "jobs" / f"{candidate}-{seed}-{phase}"
    out.mkdir(parents=True)
    pred.to_csv(out / "predictions.csv", date_format="%Y-%m-%d", float_format="%.17g")
    (out / "model.joblib").write_bytes(b"fixture model bytes; never loaded")
    write(
        out / "config.json",
        {
            "candidate": candidate,
            "inner_receipt": record(inner) if inner else None,
            "parameters": {"n_estimators": 5},
        },
    )
    importance = write(
        out / "feature-importance.json",
        {
            "candidate": candidate,
            "seed": seed,
            "phase": phase,
            "source": "fitted_model_training_only",
            "used_for_selection": False,
            "method": "test fixture",
            "limitations": ["fixture only"],
            "model": record(out / "model.joblib"),
            "manifest": record(Path(inputs["manifest"])),
            "groups": {"local": {"normalized_importance": 1.0}},
        },
    )
    receipt = {
        "classification_protocol": "monthly-k4-next-day-v1",
        "status": "completed",
        "fit_count": 1,
        "candidate": candidate,
        "seed": seed,
        "phase": phase,
        "classes": ["D", "F", "T", "P"],
        "data_version": "fixture-1",
        "inputs": {
            key: record(Path(inputs[key]))
            for key in ["features", "targets", "manifest"]
        },
        "artifacts": {
            key: record(out / name)
            for key, name in [
                ("model", "model.joblib"),
                ("predictions", "predictions.csv"),
                ("config", "config.json"),
                ("feature_importance", "feature-importance.json"),
            ]
        },
        "inner_receipt": record(inner) if inner else None,
        "selected_iteration": 5,
        "training_class_counts": dict(D=1, F=1 if phase == "inner" else 2, T=1, P=1),
        "metrics": classification_metrics(target.label, probabilities),
    }
    return write(out / "receipt.json", receipt)


def test_init_before_preparation_is_frozen_and_cannot_reset(campaign):
    root, archive, path, state = init(campaign)
    assert not (archive / "data/features.parquet").exists()
    assert state["revision"] == 0
    assert state["remaining_budget"]["classifier_fits"] == 42
    assert state["next_stage"] == "start_preparation"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="exists"):
        api().initialize(campaign[2], repo_root=root)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "key,value",
    [
        ("seeds", [42]),
        ("candidates", ["lgb_full"]),
        ("classes", ["P", "T", "F", "D"]),
        ("baseline_ref", "baseline/v5"),
    ],
)
def test_initialization_rejects_stock_identity_or_missing_seeds(campaign, key, value):
    root, _, plan_path, plan = campaign
    plan[key] = value
    write(plan_path, plan)
    with pytest.raises(ValueError):
        api().initialize(plan_path, repo_root=root)


def test_failed_preparation_retains_evidence_and_forbids_fits(campaign):
    root, archive, path, _ = init(campaign)
    event(root, path, type="start_preparation", argv=["python", "prepare.py"])
    failure = write(
        archive / "docs/label-audit.json", {"accepted": False, "reason": "missing P"}
    )
    state = event(
        root,
        path,
        type="prepared",
        outcome="invalid",
        reason="missing P",
        evidence=[str(failure)],
    )
    assert state["preparation"]["outcome"] == "invalid"
    assert state["remaining_budget"]["classifier_fits"] == 42
    with pytest.raises(ValueError, match="preparation"):
        event(
            root,
            path,
            type="start_fit",
            job_id="first",
            candidate="lgb_full",
            seed=42,
            phase="inner",
            argv=["train"],
        )


def test_prepared_input_drift_blocks_new_work_but_allows_failure_close(campaign):
    root, archive, path, inputs = prepare(campaign)
    Path(inputs["features"]).write_bytes(b"drift")
    with pytest.raises(ValueError, match="drift"):
        api().inspect_state(path, repo_root=root)
    event(root, path, type="stop", reason="execution_blocked")
    failure = write(archive / "docs/failure.json", {"reason": "input drift"})
    event(
        root,
        path,
        type="result",
        outcome="failed",
        reason="input drift",
        evidence=[str(failure)],
    )
    assert (
        api().inspect_state(path, repo_root=root, check_drift=False)["result"][
            "outcome"
        ]
        == "failed"
    )


def test_fit_lock_revision_and_no_more_fits_after_stop(campaign):
    root, _, path, _ = prepare(campaign)
    event(
        root,
        path,
        type="start_fit",
        job_id="first",
        candidate="logistic_full",
        seed=42,
        phase="inner",
        argv=["train"],
    )
    state = api().inspect_state(path, repo_root=root, check_drift=False)
    with pytest.raises(ValueError, match="active"):
        event(
            root,
            path,
            type="start_fit",
            job_id="second",
            candidate="lgb_full",
            seed=42,
            phase="inner",
            argv=["train"],
        )
    with pytest.raises(ValueError, match="stale"):
        api().append_event(
            path,
            {"type": "stop", "reason": "user_stopped"},
            expected_revision=state["revision"] - 1,
            repo_root=root,
        )
    event(root, path, type="stop", reason="user_stopped")
    with pytest.raises(ValueError, match="stopping"):
        event(
            root,
            path,
            type="start_fit",
            job_id="third",
            candidate="lgb_full",
            seed=42,
            phase="inner",
            argv=["train"],
        )


def test_fit_receipt_chain_and_actual_evidence_hashes(campaign):
    root, archive, path, inputs = prepare(campaign)
    event(
        root,
        path,
        type="start_fit",
        job_id="inner",
        candidate="logistic_full",
        seed=42,
        phase="inner",
        argv=["train"],
    )
    receipt = job_fixture(root, archive, inputs)
    event(
        root,
        path,
        type="fit_result",
        job_id="inner",
        outcome="succeeded",
        receipt=str(receipt),
        evidence=[str(receipt)],
    )
    event(
        root,
        path,
        type="start_fit",
        job_id="final",
        candidate="logistic_full",
        seed=42,
        phase="final",
        argv=["train"],
        inner_receipt=str(receipt),
    )
    final = job_fixture(root, archive, inputs, phase="final", inner=receipt)
    state = event(
        root,
        path,
        type="fit_result",
        job_id="final",
        outcome="succeeded",
        receipt=str(final),
        evidence=[str(final)],
    )
    assert state["remaining_budget"]["classifier_fits"] == 40
    model = Path(json.loads(final.read_text())["artifacts"]["model"]["path"])
    model.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="fingerprint|drift"):
        api().inspect_state(path, repo_root=root)


def test_malformed_probability_rejected_even_with_matching_hash(campaign):
    root, archive, path, inputs = prepare(campaign)
    event(
        root,
        path,
        type="start_fit",
        job_id="inner",
        candidate="logistic_full",
        seed=42,
        phase="inner",
        argv=["train"],
    )
    receipt = job_fixture(root, archive, inputs)
    doc = json.loads(receipt.read_text())
    pred = Path(doc["artifacts"]["predictions"]["path"])
    pred.write_text(pred.read_text().replace("0.59999999999999998", "1.6"))
    doc["artifacts"]["predictions"] = record(pred)
    write(receipt, doc)
    with pytest.raises(ValueError, match="probabilit"):
        event(
            root,
            path,
            type="fit_result",
            job_id="inner",
            outcome="succeeded",
            receipt=str(receipt),
            evidence=[str(receipt)],
        )


def test_retry_is_only_same_failed_configuration_and_cost_is_cumulative(campaign):
    root, archive, path, _ = prepare(campaign)
    for i in range(3):
        payload = dict(
            type="start_fit",
            job_id=f"job{i}",
            candidate="extra_full",
            seed=42,
            phase="inner",
            argv=["train"],
        )
        if i:
            payload["retry_of"] = f"job{i-1}"
        event(root, path, **payload)
        failure = write(archive / f"docs/failure{i}.json", {"error": "infrastructure"})
        event(
            root,
            path,
            type="fit_result",
            job_id=f"job{i}",
            outcome="failed",
            reason="infrastructure",
            evidence=[str(failure)],
        )
    with pytest.raises(ValueError, match="retr"):
        event(
            root,
            path,
            type="start_fit",
            job_id="fourth",
            retry_of="job2",
            candidate="extra_full",
            seed=42,
            phase="inner",
            argv=["train"],
        )
    assert (
        api().inspect_state(path, repo_root=root, check_drift=False)[
            "remaining_budget"
        ]["classifier_fits"]
        == 39
    )


def failed_result(campaign):
    root, archive, path, _ = init(campaign)
    event(root, path, type="start_preparation", argv=["prepare"])
    failure = write(
        archive / "docs/failure.json", {"accepted": False, "reason": "invalid labels"}
    )
    event(
        root,
        path,
        type="prepared",
        outcome="invalid",
        reason="invalid labels",
        evidence=[str(failure)],
    )
    event(
        root,
        path,
        type="result",
        outcome="invalid",
        reason="invalid labels",
        evidence=[str(failure)],
    )
    return root, archive, path, failure


def register_report(root, archive, path, output, state_value="invalid", models=None):
    state = api().inspect_state(path, repo_root=root, check_drift=False)
    detail = archive / "reports/detail.html"
    detail.parent.mkdir(parents=True, exist_ok=True)
    detail.write_text("<html>Actual persisted classification result</html>")
    row = dict(
        exp_id="model-structure/style-classifier-k4-20261108-v1",
        classification_protocol="monthly-k4-next-day-v1",
        baseline_ref="market-style/majority-prior-v1",
        data_version="fixture-1",
        classes=["D", "F", "T", "P"],
        seeds=[42, 1000, 2000, 3000, 4000],
        state=state_value,
        harness=dict(
            campaign_id=state["plan"]["campaign_id"], state_path=state["state_path"]
        ),
        portable_eval_output=str(output),
        portable_eval_sha256=record(output)["sha256"],
        detail_report=str(detail),
        candidates={
            c: dict(state=state_value, models=(models[c] if models else []))
            for c in ["logistic_full", "lgb_core", "lgb_full", "extra_full"]
        },
    )
    registry = root / "backtest/experiments/registry.jsonl"
    registry.write_text(json.dumps(row) + "\n")
    event(root, path, type="registered")
    hub = root / "backtest/experiments/reports/market_style_report.html"
    hub.parent.mkdir(parents=True, exist_ok=True)
    import os

    hub.write_text('<a href="' + os.path.relpath(detail, hub.parent) + '">detail</a>')
    event(root, path, type="reported")
    return row


def test_failure_closure_requires_actual_registration_report_and_cleanup(campaign):
    root, archive, path, failure = failed_result(campaign)
    event(root, path, type="stop", reason="invalid_labels")
    with pytest.raises(ValueError, match="cleanup"):
        event(root, path, type="close")
    register_report(root, archive, path, failure)
    audit = write(
        archive / "docs/cleanup.json",
        {"temporary": "cleaned", "result": "no_changes", "mlruns": "not_applicable"},
    )
    event(root, path, type="cleanup", audit=str(audit))
    state = event(root, path, type="close")
    assert state["status"] == "closed"
    assert state["remaining_budget"]["classifier_fits"] == 42
    with pytest.raises(ValueError, match="closed"):
        event(root, path, type="start_preparation", argv=["retry"])


def test_registry_rejects_wrong_campaign_result_hash_and_stock_flags(campaign):
    root, archive, path, failure = failed_result(campaign)
    row = register_report(root, archive, path, failure)
    registry = root / "backtest/experiments/registry.jsonl"
    for key, value in [
        ("harness", {"campaign_id": "foreign", "state_path": "wrong"}),
        ("portable_eval_sha256", "0" * 64),
        ("phase_m_protocol", "v1"),
    ]:
        changed = copy.deepcopy(row)
        changed[key] = value
        registry.write_text(json.dumps(changed) + "\n")
        event(root, path, type="stop", reason="done") if key == "harness" else None
        with pytest.raises(ValueError):
            event(root, path, type="cleanup", audit=str(failure))


def completed_archive(campaign):
    from backtest.market_style.evaluation import evaluate_archive

    root, archive, path, inputs = prepare(campaign)
    for candidate in ["logistic_full", "lgb_core", "lgb_full", "extra_full"]:
        for seed in [42, 1000, 2000, 3000, 4000]:
            inner = None
            for phase in ["inner", "final"]:
                job = f"{candidate}-{seed}-{phase}"
                payload = dict(
                    type="start_fit",
                    job_id=job,
                    candidate=candidate,
                    seed=seed,
                    phase=phase,
                    argv=["train", candidate, str(seed), phase],
                )
                if inner:
                    payload["inner_receipt"] = str(inner)
                event(root, path, **payload)
                receipt = job_fixture(
                    root, archive, inputs, candidate, seed, phase, inner
                )
                event(
                    root,
                    path,
                    type="fit_result",
                    job_id=job,
                    outcome="succeeded",
                    receipt=str(receipt),
                    evidence=[str(receipt)],
                )
                if phase == "inner":
                    inner = receipt
    bundle = evaluate_archive(archive, bootstrap_replicates=2)
    output = archive / "metrics/evaluation.json"
    return root, archive, path, bundle, output


def test_complete_campaign_verifies_40_receipts_ensembles_registry_and_close(campaign):
    root, archive, path, bundle, output = completed_archive(campaign)
    original = output.read_bytes()
    broken = copy.deepcopy(bundle)
    broken["candidates"]["lgb_full"]["models"] = broken["candidates"]["lgb_full"][
        "models"
    ][:-1]
    write(output, broken)
    with pytest.raises(ValueError, match="five actual models"):
        event(
            root,
            path,
            type="result",
            outcome="succeeded",
            evaluation=str(output),
            evidence=[str(output)],
        )
    output.write_bytes(original)
    broken = copy.deepcopy(bundle)
    broken["baselines"]["training_prior"]["evaluation"]["accuracy"] = 0.123
    write(output, broken)
    with pytest.raises(ValueError, match="baseline"):
        event(
            root,
            path,
            type="result",
            outcome="succeeded",
            evaluation=str(output),
            evidence=[str(output)],
        )
    output.write_bytes(original)
    importance_path = Path(
        bundle["candidates"]["logistic_full"]["feature_importance"]["artifact"]["path"]
    )
    importance_original = importance_path.read_bytes()
    importance_path.write_bytes(b"changed grouped importance")
    with pytest.raises(ValueError, match="fingerprint"):
        event(
            root,
            path,
            type="result",
            outcome="succeeded",
            evaluation=str(output),
            evidence=[str(output)],
        )
    importance_path.write_bytes(importance_original)
    state = event(
        root,
        path,
        type="result",
        outcome="succeeded",
        evaluation=str(output),
        evidence=[str(output)],
    )
    assert state["remaining_budget"]["classifier_fits"] == 2
    register_report(
        root,
        archive,
        path,
        output,
        "completed",
        {c: v["models"] for c, v in bundle["candidates"].items()},
    )
    event(root, path, type="stop", reason="hypotheses_exhausted")
    audit = write(
        archive / "docs/cleanup.json",
        {"temporary": "cleaned", "result": "no_changes", "mlruns": "not_applicable"},
    )
    event(root, path, type="cleanup", audit=str(audit))
    assert event(root, path, type="close")["status"] == "closed"


def test_main_cli_dispatches_style_without_changing_old_tracks(campaign, capsys):
    from backtest.scripts.maintenance.experiment_harness import main

    root, _, plan, _ = campaign
    main(["--repo-root", str(root), "style", "init", "--plan", str(plan)])
    state = json.loads(capsys.readouterr().out)
    main(
        [
            "--repo-root",
            str(root),
            "style",
            "check",
            "--state",
            str(root / state["state_path"]),
        ]
    )
    assert json.loads(capsys.readouterr().out)["next_stage"] == "start_preparation"


def test_final_fit_rejects_config_with_foreign_inner_chain(campaign):
    root, archive, path, inputs = prepare(campaign)
    event(
        root,
        path,
        type="start_fit",
        job_id="inner",
        candidate="logistic_full",
        seed=42,
        phase="inner",
        argv=["train"],
    )
    inner = job_fixture(root, archive, inputs)
    event(
        root,
        path,
        type="fit_result",
        job_id="inner",
        outcome="succeeded",
        receipt=str(inner),
        evidence=[str(inner)],
    )
    event(
        root,
        path,
        type="start_fit",
        job_id="final",
        candidate="logistic_full",
        seed=42,
        phase="final",
        argv=["train"],
        inner_receipt=str(inner),
    )
    final = job_fixture(root, archive, inputs, phase="final", inner=inner)
    payload = json.loads(final.read_text())
    config = Path(payload["artifacts"]["config"]["path"])
    write(config, {"inner_receipt": None})
    payload["artifacts"]["config"] = record(config)
    write(final, payload)
    with pytest.raises(ValueError, match="config inner receipt"):
        event(
            root,
            path,
            type="fit_result",
            job_id="final",
            outcome="succeeded",
            receipt=str(final),
            evidence=[str(final)],
        )


def test_status_failure_next_stage_tracks_registration_and_running_jobs(campaign):
    root, archive, path, failure = failed_result(campaign)
    assert (
        api().inspect_state(path, repo_root=root, check_drift=False)["next_stage"]
        == "registered"
    )


def test_wall_budget_and_checksum_are_enforced_without_reset(campaign, monkeypatch):
    root, _, path, state = init(campaign)
    monkeypatch.setattr(api().common, "_now", lambda: state["deadline"] + 1)
    with pytest.raises(ValueError, match="wall budget"):
        api().inspect_state(path, repo_root=root)
    assert (
        api().inspect_state(path, repo_root=root, check_drift=False)[
            "remaining_budget"
        ]["wall_seconds"]
        == 0
    )
    doc = json.loads(path.read_text())
    doc["deadline"] += 3600
    write(path, doc)
    with pytest.raises(ValueError, match="checksum"):
        api().inspect_state(path, repo_root=root, check_drift=False)


def test_stable_lock_rejects_concurrent_writer(campaign):
    root, _, path, state = init(campaign)
    with api().common._lock(path):
        with pytest.raises(ValueError, match="concurrent"):
            api().append_event(
                path,
                {"type": "stop", "reason": "done"},
                expected_revision=0,
                repo_root=root,
            )
    assert api().inspect_state(path, repo_root=root, check_drift=False)["revision"] == 0


@pytest.mark.parametrize("invalid", ["false_counts", "missing_class"])
def test_receipt_rejects_false_training_counts_or_absent_training_class(
    campaign, invalid
):
    labels = ["D", "F", "T", "D", "F", "T"] if invalid == "missing_class" else None
    root, archive, path, inputs = prepare(campaign, labels=labels)
    event(
        root,
        path,
        type="start_fit",
        job_id="inner",
        candidate="logistic_full",
        seed=42,
        phase="inner",
        argv=["train"],
    )
    receipt = job_fixture(root, archive, inputs)
    if invalid == "false_counts":
        doc = json.loads(receipt.read_text())
        doc["training_class_counts"]["D"] = 2
        write(receipt, doc)
    with pytest.raises(ValueError, match="training class"):
        event(
            root,
            path,
            type="fit_result",
            job_id="inner",
            outcome="succeeded",
            receipt=str(receipt),
            evidence=[str(receipt)],
        )
    state = api().inspect_state(path, repo_root=root, check_drift=False)
    assert state["jobs"]["inner"]["outcome"] == "running"


def test_result_rejects_ensemble_per_class_and_confusion_mutations(campaign):
    root, archive, path, bundle, output = completed_archive(campaign)
    for phase in ["inner", "evaluation"]:
        for field in ["per_class", "confusion_matrix"]:
            broken = copy.deepcopy(bundle)
            metric = broken["candidates"]["logistic_full"][phase]
            if field == "per_class":
                metric["per_class"]["P"]["recall"] = 0.75
            else:
                metric["confusion_matrix"][0][0] += 1
            write(output, broken)
            with pytest.raises(ValueError, match="ensemble metrics"):
                event(
                    root,
                    path,
                    type="result",
                    outcome="succeeded",
                    evaluation=str(output),
                    evidence=[str(output)],
                )
    assert (
        api().inspect_state(path, repo_root=root, check_drift=False)["result"] is None
    )
