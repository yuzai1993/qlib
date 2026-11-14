"""Isolated, budgeted lifecycle ledger for retrospective market-style classification.

This companion never fits models, launches processes, writes registry events or
promotes baselines. All evidence is checked against actual persistent bytes.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import math
import os
from pathlib import Path

from backtest.scripts.maintenance import experiment_harness as common
from backtest.scripts.register.experiment_registry import load_registry

ROOT = common.ROOT
TRACK = "market_style_classifier"
EXP_ID = "model-structure/style-classifier-k4-20261108-v1"
ARCHIVE = "backtest/experiments/models/" + EXP_ID
PROTOCOL = "monthly-k4-next-day-v1"
BASELINE = "market-style/majority-prior-v1"
CLASSES = ["D", "F", "T", "P"]
SEEDS = [42, 1000, 2000, 3000, 4000]
CANDIDATES = ["logistic_full", "lgb_core", "lgb_full", "extra_full"]
BUDGET = dict(
    max_wall_hours=8, max_classifier_fits=42, max_preparation_fits=1, concurrency=1
)
HUB = "backtest/experiments/reports/market_style_report.html"
PREPARED = {"features", "targets", "manifest", "label_audit", "preparation_result"}
_require = common._require
_json = common._json


def _archive(root, plan):
    path = common._path(root, plan["archive_path"])
    doc = _json(path / "metadata.json")
    _require(
        doc.get("exp_id") == EXP_ID and doc.get("archive_path") == ARCHIVE,
        "archive identity mismatch",
    )
    _require(
        doc.get("kind") == "model"
        and doc.get("track") == "all_a"
        and doc.get("entity_type", "experiment") == "experiment",
        "classification parent must be one all_a model experiment",
    )
    return path


def _record(root, raw, *, durable=False):
    path = common._path(root, str(raw))
    if durable:
        _require(
            path.is_relative_to(root / ARCHIVE)
            or path.is_relative_to(root / "backtest/result"),
            "evidence must persist within parent archive or result",
        )
    return common._fingerprint(root, str(path))


def _verify_record(root, record, *, durable=True):
    _require(isinstance(record, dict), "artifact record required")
    actual = _record(root, record.get("path"), durable=durable)
    _require(
        record.get("sha256") == actual["sha256"],
        f"fingerprint drift: {record.get('path')}",
    )
    if "size_bytes" in record:
        _require(record["size_bytes"] == actual["size"], "artifact size mismatch")
    if "size" in record:
        _require(record["size"] == actual["size"], "artifact size mismatch")
    return actual


def _evidence(root, raw):
    _require(isinstance(raw, list) and raw, "persistent evidence list required")
    result = [_record(root, item, durable=True) for item in raw]
    _require(len({r["path"] for r in result}) == len(result), "duplicate evidence")
    return result


def _load(root, state_path):
    path = common._path(root, str(state_path))
    doc = _json(path)
    checksum = doc.pop("checksum", None)
    _require(
        checksum == common._digest(doc), "state checksum mismatch; never reset silently"
    )
    doc["checksum"] = checksum
    _require(
        doc.get("track") == TRACK and doc.get("schema_version") == 1,
        "not a classification state",
    )
    _require(
        doc.get("repo_root") == str(root)
        and doc.get("state_path") == common._ref(root, path),
        "state repository/path identity mismatch",
    )
    _require(
        path == root / ARCHIVE / "docs/style-harness-state.json",
        "unsafe classification state location",
    )
    return doc


def _summary(state):
    state["remaining_budget"] = {
        "classifier_fits": BUDGET["max_classifier_fits"] - len(state["jobs"]),
        "preparation_fits": BUDGET["max_preparation_fits"]
        - int(state["preparation"] is not None),
        "technical_retries": 2 - sum("retry_of" in j for j in state["jobs"].values()),
        "wall_seconds": max(0.0, state["deadline"] - common._now()),
    }
    if state["status"] == "closed":
        stage = "closed"
    elif state["preparation"] and state["preparation"]["outcome"] == "running":
        stage = "prepared"
    elif any(j["outcome"] == "running" for j in state["jobs"].values()):
        stage = "fit_result"
    elif state["result"]:
        if not state["registered"]:
            stage = "registered"
        elif not state["reported"]:
            stage = "reported"
        elif not state["stop"]:
            stage = "stop"
        else:
            stage = "cleanup" if not state["cleanup"] else "close"
    elif state["stop"]:
        stage = "result"
    elif state["preparation"] is None:
        stage = "start_preparation"
    elif state["preparation"]["outcome"] != "completed":
        stage = "result"
    elif len([j for j in state["jobs"].values() if j["outcome"] == "succeeded"]) == 40:
        stage = "result"
    else:
        stage = "start_fit"
    state["next_stage"] = stage


def _identity(root, state):
    _archive(root, state["plan"])
    for record in [state["plan_source"], *state["policies"], *state["inputs"]]:
        _verify_record(root, record, durable=False)
    preparation = state["preparation"]
    if preparation:
        for record in preparation.get("evidence", []) + list(
            preparation.get("inputs", {}).values()
        ):
            _verify_record(root, record)
    for job in state["jobs"].values():
        for record in job.get("evidence", []):
            _verify_record(root, record)
        if job.get("receipt"):
            _verify_receipt(root, state, job)
    if state["result"]:
        for record in state["result"]["evidence"]:
            _verify_record(root, record)
    if state["reported"]:
        for record in state["reported"]:
            _verify_record(root, record, durable=False)
    if state["cleanup"]:
        _verify_record(root, state["cleanup"]["audit"])


def _guard(root, state):
    _require(
        state["status"] == "active",
        f"campaign is {state['status']}; new starts refused",
    )
    _require(state["result"] is None, "parent result already frozen")
    _identity(root, state)
    _require(common._now() < state["deadline"], "wall budget exhausted")


def initialize(plan_path: Path, *, repo_root: Path = ROOT) -> dict:
    """Freeze rules, actual source/config bytes and budget BEFORE preparation."""
    root = Path(repo_root).resolve()
    plan_path = common._path(root, str(plan_path))
    plan = _json(plan_path)
    expected = dict(
        schema_version=1,
        track=TRACK,
        exp_id=EXP_ID,
        archive_path=ARCHIVE,
        baseline_ref=BASELINE,
        classes=CLASSES,
        seeds=SEEDS,
        candidates=CANDIDATES,
        classification_protocol=PROTOCOL,
    )
    for key, value in expected.items():
        _require(plan.get(key) == value, f"classification plan {key} mismatch")
    common._identifier(plan.get("campaign_id"), "campaign_id")
    common._text(plan.get("data_version"), "data_version")
    _require(
        plan.get("budget", {}) in ({}, BUDGET),
        "classification budget is fixed: 8h,42 fits,one preparation,concurrency1",
    )
    plan["budget"] = copy.deepcopy(BUDGET)
    archive = _archive(root, plan)
    _require(
        plan_path.is_relative_to(archive / "configs"),
        "plan must persist in parent configs",
    )
    inputs = common._files(root, plan.get("inputs"))
    policies = common._files(root, list(common.POLICIES))
    path = archive / "docs/style-harness-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with common._lock(path):
        _require(
            not path.exists(), "classification state already exists; inspect and resume"
        )
        now = common._now()
        state = dict(
            schema_version=1,
            track=TRACK,
            repo_root=str(root),
            state_path=common._ref(root, path),
            plan=plan,
            plan_source=common._fingerprint(root, str(plan_path)),
            policies=policies,
            inputs=inputs,
            revision=0,
            started_at=now,
            deadline=now + 8 * 3600,
            status="active",
            preparation=None,
            jobs={},
            result=None,
            registered=None,
            reported=None,
            stop=None,
            cleanup=None,
            events=[],
        )
        _summary(state)
        common._save(path, state)
    return state


def inspect_state(
    state_path: Path, *, repo_root: Path = ROOT, check_drift=True
) -> dict:
    """Status is always readable; check additionally guards the next actual job."""
    root = Path(repo_root).resolve()
    state = _load(root, state_path)
    if check_drift:
        _guard(root, state)
        _require(
            not any(j["outcome"] == "running" for j in state["jobs"].values()),
            "active fit still running",
        )
        _require(
            not state["preparation"] or state["preparation"]["outcome"] != "running",
            "active preparation still running",
        )
    _summary(state)
    return state


def _argv(raw):
    _require(
        isinstance(raw, list) and raw and all(isinstance(x, str) and x for x in raw),
        "argv must be a nonempty text list",
    )
    return list(raw)


def _prepared(root, state, data):
    prep = state["preparation"]
    _require(
        prep and prep["outcome"] == "running",
        "start preparation exactly once before prepared",
    )
    outcome = data.get("outcome")
    _require(
        outcome in {"completed", "invalid", "failed"}, "invalid preparation outcome"
    )
    evidence = _evidence(root, data.get("evidence"))
    inputs = {}
    if outcome == "completed":
        _identity(root, state)
        raw = data.get("inputs")
        _require(
            isinstance(raw, dict) and set(raw) == PREPARED,
            "exact five prepared inputs required",
        )
        inputs = {k: _record(root, value, durable=True) for k, value in raw.items()}
        _require(
            all(value in evidence for value in inputs.values()),
            "prepared inputs must be evidence",
        )
        manifest = _json(common._path(root, inputs["manifest"]["path"]))
        for key, value in dict(
            classes=CLASSES,
            classification_protocol=PROTOCOL,
            data_version=state["plan"]["data_version"],
        ).items():
            _require(manifest.get(key) == value, f"prepared manifest {key} mismatch")
        audit = _json(common._path(root, inputs["label_audit"]["path"]))
        _require(
            audit.get("accepted") is True, "label audit must accept the frozen mapping"
        )
        doc = _json(common._path(root, inputs["preparation_result"]["path"]))
        _require(
            doc.get("status") == "completed"
            and doc.get("label_fits") == 1
            and doc.get("classification_fits") == 0
            and doc.get("label_initializations") == 100,
            "preparation fit receipt mismatch",
        )
        for key in ("features", "targets", "manifest"):
            _require(
                _verify_record(root, doc.get(key)) == inputs[key],
                f"preparation {key} mismatch",
            )
        for key in ("features", "targets"):
            _require(
                _verify_record(root, manifest.get("dataset_files", {}).get(key))
                == inputs[key],
                f"manifest {key} mismatch",
            )
        _require(
            _verify_record(root, manifest.get("label_audit")) == inputs["label_audit"],
            "manifest label audit mismatch",
        )
        from backtest.market_style.modeling import _read_inputs

        _, target, _ = _read_inputs(
            *(
                common._path(root, inputs[k]["path"])
                for k in ("features", "targets", "manifest")
            )
        )
        _require(
            set(target.label) <= set(CLASSES),
            "prepared targets contain unknown classes",
        )
    else:
        common._text(data.get("reason"), "preparation failure reason")
    prep.update(
        outcome=outcome,
        finished_at=common._now(),
        evidence=evidence,
        inputs=inputs,
        reason=data.get("reason", ""),
    )


def _metrics(metric):
    _require(isinstance(metric, dict), "classification metrics required")
    _require(
        type(metric.get("n_days")) is int and metric["n_days"] > 0,
        "positive classification n_days required",
    )
    for key in (
        "accuracy",
        "macro_f1",
        "weighted_f1",
        "balanced_accuracy",
        "logloss",
        "brier",
    ):
        value = metric.get(key)
        upper = 2 if key == "brier" else math.inf if key == "logloss" else 1
        _require(
            type(value) in {float, int}
            and math.isfinite(value)
            and 0 <= value <= upper,
            f"invalid classification metric {key}",
        )


def _same_metrics(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            _same_metrics(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_metrics(a, b) for a, b in zip(left, right)
        )
    if type(left) in {int, float} and type(right) in {int, float}:
        return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-10)
    return left == right


def _verify_receipt(root, state, job, receipt_path=None):
    record = (
        _record(root, receipt_path, durable=True)
        if receipt_path
        else _verify_record(root, job["receipt"])
    )
    receipt = _json(common._path(root, record["path"]))
    for key in ("candidate", "seed", "phase"):
        _require(receipt.get(key) == job[key], f"fit receipt {key} mismatch")
    _require(
        receipt.get("data_version") == state["plan"]["data_version"],
        "fit receipt data_version mismatch",
    )
    _require(
        set(receipt.get("inputs", {})) == {"features", "targets", "manifest"},
        "fit receipt input set mismatch",
    )
    for key, item in receipt["inputs"].items():
        _require(
            _verify_record(root, item) == state["preparation"]["inputs"][key],
            f"fit receipt frozen {key} mismatch",
        )
    _require(
        {"model", "predictions", "config"}
        <= set(receipt.get("artifacts", {}))
        <= {"model", "predictions", "config", "feature_importance"},
        "fit receipt requires actual model, predictions and config",
    )
    for item in receipt["artifacts"].values():
        _verify_record(root, item)
    if job["phase"] == "final":
        inner = job["inner_receipt"]
        _require(
            _verify_record(root, receipt.get("inner_receipt")) == inner,
            "final inner receipt chain mismatch",
        )
        inner_doc = _json(common._path(root, inner["path"]))
        if job["candidate"].startswith("lgb_"):
            _require(
                receipt.get("selected_iteration")
                == inner_doc.get("selected_iteration"),
                "final LGB selected_iteration differs from inner",
            )
    else:
        _require(
            receipt.get("inner_receipt") is None,
            "inner fit cannot cite an inner receipt",
        )
    from backtest.market_style.evaluation import (
        _verify_final_provenance,
        _verified_prediction,
        classification_metrics,
    )

    # The shared verifier checks fixed classes/protocol, target split/index and actual
    # probability values. Normalize paths so receipts may use repository-relative refs.
    absolute = copy.deepcopy(receipt)
    for group in ("inputs", "artifacts"):
        for item in absolute[group].values():
            item["path"] = str(common._path(root, item["path"]))
    import pandas as pd
    from backtest.market_style.modeling import temporal_split

    targets = pd.read_parquet(absolute["inputs"]["targets"]["path"])
    train_mask, _ = temporal_split(targets, job["phase"])
    counts = (
        targets.loc[train_mask, "label"].value_counts().reindex(CLASSES, fill_value=0)
    )
    _require((counts > 0).all(), "all four training classes required in frozen targets")
    claimed = receipt.get("training_class_counts")
    _require(
        isinstance(claimed, dict)
        and set(claimed) == set(CLASSES)
        and all(type(value) is int for value in claimed.values())
        and claimed == counts.to_dict(),
        "receipt training class counts differ from frozen temporal split",
    )
    if job["phase"] == "final":
        _verify_final_provenance(absolute, inner_doc, receipt["inner_receipt"])
    frame = _verified_prediction(absolute)
    computed = classification_metrics(
        frame.label, frame[["p_D", "p_F", "p_T", "p_P"]].to_numpy()
    )
    _metrics(receipt.get("metrics"))
    _require(
        _same_metrics(computed, receipt["metrics"]),
        "receipt metrics differ from actual probabilities",
    )
    return record, receipt, frame


def _start_fit(root, state, data):
    _guard(root, state)
    _require(
        state["preparation"] and state["preparation"]["outcome"] == "completed",
        "completed preparation required before classifier fit",
    )
    _require(
        not any(j["outcome"] == "running" for j in state["jobs"].values()),
        "an active fit already exists",
    )
    _require(len(state["jobs"]) < 42, "classifier fit budget exhausted")
    job_id = common._identifier(data.get("job_id"), "job_id")
    _require(job_id not in state["jobs"], "job_id already exists")
    candidate, seed, phase = (data.get(k) for k in ("candidate", "seed", "phase"))
    _require(
        candidate in CANDIDATES
        and type(seed) is int
        and seed in SEEDS
        and phase in {"inner", "final"},
        "unknown classification job identity",
    )
    peers = [
        (key, j)
        for key, j in state["jobs"].items()
        if (j["candidate"], j["seed"], j["phase"]) == (candidate, seed, phase)
    ]
    retry = data.get("retry_of")
    if peers:
        _require(
            retry == peers[-1][0] and peers[-1][1]["outcome"] == "failed",
            "repeat fit requires explicit retry_of latest failed same configuration",
        )
        _require(
            sum("retry_of" in j for j in state["jobs"].values()) < 2,
            "technical retry budget exhausted",
        )
        _require(
            data.get("argv") == peers[-1][1]["argv"],
            "technical retry must preserve exact argv/configuration",
        )
    else:
        _require(retry is None, "retry_of must reference failed same configuration")
    job = dict(
        job_id=job_id,
        candidate=candidate,
        seed=seed,
        phase=phase,
        argv=_argv(data.get("argv")),
        outcome="running",
        started_at=common._now(),
    )
    if retry:
        job["retry_of"] = retry
    if phase == "final":
        matches = [
            j
            for j in state["jobs"].values()
            if j["candidate"] == candidate
            and j["seed"] == seed
            and j["phase"] == "inner"
            and j["outcome"] == "succeeded"
        ]
        _require(
            len(matches) == 1, "matching succeeded inner job required before final"
        )
        record = _record(root, data.get("inner_receipt"), durable=True)
        _require(
            record == matches[0]["receipt"],
            "final requires exact matching inner receipt",
        )
        job["inner_receipt"] = record
    state["jobs"][job_id] = job


def _evaluation(root, state, path):
    record = _record(root, path, durable=True)
    doc = _json(common._path(root, record["path"]))
    for key, expected in dict(
        classification_protocol=PROTOCOL,
        classes=CLASSES,
        seeds=SEEDS,
        data_version=state["plan"]["data_version"],
        n_fit_receipts=40,
        evaluation_used_for_selection=False,
    ).items():
        _require(doc.get(key) == expected, f"evaluation {key} mismatch")
    succeeded = [j for j in state["jobs"].values() if j["outcome"] == "succeeded"]
    expected = {
        (c, s, p) for c in CANDIDATES for s in SEEDS for p in ("inner", "final")
    }
    _require(
        {(j["candidate"], j["seed"], j["phase"]) for j in succeeded} == expected
        and len(succeeded) == 40,
        "successful result requires all 40 fits and five seeds",
    )
    for key in ("features", "targets", "manifest"):
        _require(
            _verify_record(root, doc.get("inputs", {}).get(key))
            == state["preparation"]["inputs"][key],
            f"evaluation {key} mismatch",
        )
    _require(
        set(doc.get("candidates", {})) == set(CANDIDATES),
        "evaluation requires exactly four internal candidates",
    )
    import numpy as np
    import pandas as pd
    from backtest.market_style.evaluation import classification_metrics

    shared = {}
    for candidate, item in doc["candidates"].items():
        for phase, ensemble_key, metric_key, per_seed_key in [
            ("inner", "inner_ensemble", "inner", "inner_per_seed"),
            ("final", "ensemble", "evaluation", "per_seed"),
        ]:
            _metrics(item.get(metric_key))
            _require(
                set(item.get(per_seed_key, {})) == set(map(str, SEEDS)),
                "evaluation missing seed metrics",
            )
            records = item.get("receipts", {}).get(phase, [])
            _require(
                len(records) == 5 and {r.get("seed") for r in records} == set(SEEDS),
                "evaluation missing five receipt seeds",
            )
            frames = []
            for seed in SEEDS:
                job = next(
                    j
                    for j in succeeded
                    if (j["candidate"], j["seed"], j["phase"])
                    == (candidate, seed, phase)
                )
                ref = next(r for r in records if r["seed"] == seed)
                _require(
                    _verify_record(root, ref) == job["receipt"],
                    "evaluation receipt ledger mismatch",
                )
                _, receipt, frame = _verify_receipt(root, state, job)
                _metrics(item[per_seed_key][str(seed)])
                _require(
                    _same_metrics(item[per_seed_key][str(seed)], receipt["metrics"]),
                    "evaluation per-seed metrics mismatch",
                )
                frames.append(frame)
            first = frames[0][["target_date", "label"]]
            _require(
                all(first.equals(f[["target_date", "label"]]) for f in frames),
                "seed forecast indices/targets mismatch",
            )
            if phase in shared:
                _require(
                    shared[phase].equals(first), "candidate forecast indices mismatch"
                )
            shared[phase] = first
            ensemble_ref = _verify_record(root, item.get(ensemble_key))
            ensemble = pd.read_csv(
                common._path(root, ensemble_ref["path"]),
                index_col="date",
                parse_dates=["date", "target_date"],
            )
            cols = ["p_D", "p_F", "p_T", "p_P"]
            _require(
                list(ensemble.columns) == ["target_date", "label"] + cols
                and ensemble[["target_date", "label"]].equals(first),
                "ensemble forecast index mismatch",
            )
            _require(
                np.allclose(
                    ensemble[cols].to_numpy(),
                    np.mean([f[cols].to_numpy() for f in frames], axis=0),
                    rtol=0,
                    atol=1e-12,
                ),
                "ensemble is not five-seed probability average",
            )
            computed = classification_metrics(ensemble.label, ensemble[cols].to_numpy())
            _require(
                _same_metrics(computed, item[metric_key]),
                "ensemble metrics differ from actual probabilities",
            )
        for kind in ("models", "predictions"):
            records = item.get(kind, [])
            _require(
                len(records) == 5 and {r.get("seed") for r in records} == set(SEEDS),
                f"evaluation requires five actual {kind}",
            )
            for ref in records:
                job = next(
                    j
                    for j in succeeded
                    if (j["candidate"], j["seed"], j["phase"])
                    == (candidate, ref["seed"], "final")
                )
                receipt = _json(common._path(root, job["receipt"]["path"]))
                artifact = receipt["artifacts"][
                    "model" if kind == "models" else "predictions"
                ]
                _require(
                    _verify_record(root, ref) == _verify_record(root, artifact),
                    f"evaluation {kind} mismatch",
                )
        importance = item.get("feature_importance", {})
        _verify_record(root, importance.get("artifact"))
        _require(
            importance.get("source") == "fitted_model_training_only"
            and importance.get("used_for_selection") is False,
            "feature importance provenance mismatch",
        )
    _require(
        set(doc.get("baselines", {})) == {"training_majority", "training_prior"},
        "classification training baselines required",
    )
    final_receipt = _json(
        common._path(
            root, next(j["receipt"]["path"] for j in succeeded if j["phase"] == "final")
        )
    )
    counts = final_receipt["training_class_counts"]
    _require(
        set(counts) == set(CLASSES)
        and all(type(n) is int and n > 0 for n in counts.values()),
        "baseline requires four positive training counts",
    )
    prior = np.array([counts[c] for c in CLASSES], dtype=float)
    prior /= prior.sum()
    majority = np.zeros(4)
    majority[prior.argmax()] = 1
    for name, item in doc["baselines"].items():
        _metrics(item.get("evaluation"))
        ref = _verify_record(root, item.get("predictions"))
        frame = pd.read_csv(
            common._path(root, ref["path"]),
            index_col="date",
            parse_dates=["date", "target_date"],
        )
        expected = prior if name == "training_prior" else majority
        _require(
            list(frame.columns) == ["target_date", "label", "p_D", "p_F", "p_T", "p_P"]
            and frame[["target_date", "label"]].equals(shared["final"]),
            "baseline forecast coverage mismatch",
        )
        values = frame[["p_D", "p_F", "p_T", "p_P"]].to_numpy()
        _require(
            np.allclose(values, expected, atol=1e-12, rtol=0)
            and item.get("training_class_counts") == counts,
            "baseline probabilities differ from training counts",
        )
        _require(
            _same_metrics(
                classification_metrics(frame.label, values), item["evaluation"]
            ),
            "baseline metrics differ from actual predictions",
        )
    ranking = sorted(
        CANDIDATES,
        key=lambda c: (
            -doc["candidates"][c]["inner"]["macro_f1"],
            -doc["candidates"][c]["inner"]["accuracy"],
            doc["candidates"][c]["inner"]["logloss"],
            CANDIDATES.index(c),
        ),
    )
    _require(
        doc.get("inner_ranking") == ranking
        and doc.get("chosen_candidate") == ranking[0],
        "candidate selection must use frozen inner ranking",
    )
    return record


def _registry(root, state):
    result = state["result"]
    _require(result is not None, "parent result required before registration")
    rows = load_registry(root / "backtest/experiments/registry.jsonl")
    matches = [row for row in rows if row.get("exp_id") == EXP_ID]
    _require(bool(matches), "parent experiment not registered")
    row = matches[-1]
    for key, expected in dict(
        classification_protocol=PROTOCOL,
        baseline_ref=BASELINE,
        data_version=state["plan"]["data_version"],
        seeds=SEEDS,
        classes=CLASSES,
        state="completed" if result["outcome"] == "succeeded" else result["outcome"],
    ).items():
        _require(row.get(key) == expected, f"registry {key} mismatch")
    _require(
        "phase_m_protocol" not in row
        and not any(
            row.get(k)
            for k in (
                "current_baseline",
                "current_bt",
                "is_current_baseline",
                "baseline_version",
            )
        ),
        "classification cannot claim stock protocol or baseline flags",
    )
    identity = row.get("harness", {})
    _require(
        identity.get("campaign_id") == state["plan"]["campaign_id"]
        and identity.get("state_path") == state["state_path"],
        "registry campaign identity mismatch",
    )
    expected = result.get("evaluation") or result["evidence"][0]
    actual = _record(root, row.get("portable_eval_output"), durable=True)
    _require(
        actual == expected and row.get("portable_eval_sha256") == expected["sha256"],
        "registry result path/hash mismatch",
    )
    _require(
        set(row.get("candidates", {})) == set(CANDIDATES),
        "registry requires four internal candidates",
    )
    for candidate, item in row["candidates"].items():
        _require(item.get("state") == row["state"], "registry candidate state mismatch")
        if result["outcome"] == "succeeded":
            evaluation = _json(common._path(root, result["evaluation"]["path"]))
            _require(
                item.get("models") == evaluation["candidates"][candidate]["models"],
                "registry five models mismatch",
            )
    detail = common._path(root, row.get("detail_report"))
    _require(
        detail.is_relative_to(root / ARCHIVE),
        "detail report must be within parent archive",
    )
    return row, detail


def _settled(root, state, *, strict):
    _require(
        not any(j["outcome"] == "running" for j in state["jobs"].values())
        and (not state["preparation"] or state["preparation"]["outcome"] != "running"),
        "active jobs must settle before closure",
    )
    row, _ = _registry(root, state)
    _require(
        state["registered"]
        and state["registered"]["event_sha256"] == common._digest(row),
        "latest actual registration receipt required",
    )
    _require(state["reported"], "actual report receipt required")
    for ref in state["reported"]:
        _verify_record(root, ref, durable=False)
    # Failure paths retain originally frozen evidence even when input drift itself
    # caused failure; successful closure must preserve every scientific artifact.
    if strict:
        _identity(root, state)
        _evaluation(root, state, state["result"]["evaluation"]["path"])
    else:
        for job in state["jobs"].values():
            for ref in job.get("evidence", []):
                _verify_record(root, ref)
        for ref in state["result"]["evidence"]:
            _verify_record(root, ref)


def _transition(root, state, data):
    _require(state["status"] != "closed", "campaign already closed")
    kind = data.get("type")
    if kind == "start_preparation":
        _guard(root, state)
        _require(state["preparation"] is None, "preparation fit credit already spent")
        state["preparation"] = dict(
            argv=_argv(data.get("argv")), outcome="running", started_at=common._now()
        )
    elif kind == "prepared":
        _prepared(root, state, data)
    elif kind == "start_fit":
        _start_fit(root, state, data)
    elif kind == "fit_result":
        job = state["jobs"].get(data.get("job_id"))
        _require(
            job is not None and job["outcome"] == "running",
            "active job required for fit_result",
        )
        outcome = data.get("outcome")
        _require(outcome in {"succeeded", "failed"}, "invalid fit outcome")
        evidence = _evidence(root, data.get("evidence"))
        if outcome == "succeeded":
            _identity(root, state)
            receipt, _, _ = _verify_receipt(root, state, job, data.get("receipt"))
            _require(
                receipt in evidence, "receipt must be included in durable evidence"
            )
            job["receipt"] = receipt
        else:
            common._text(data.get("reason"), "failed fit reason")
        job.update(
            outcome=outcome,
            evidence=evidence,
            reason=data.get("reason", ""),
            finished_at=common._now(),
        )
    elif kind == "result":
        _require(state["result"] is None, "parent result already frozen")
        _require(
            state["preparation"] is not None
            and state["preparation"]["outcome"] != "running"
            and not any(j["outcome"] == "running" for j in state["jobs"].values()),
            "settle preparation/active jobs before parent result",
        )
        outcome = data.get("outcome")
        _require(
            outcome in {"succeeded", "invalid", "failed"}, "invalid parent outcome"
        )
        evidence = _evidence(root, data.get("evidence"))
        result = dict(
            outcome=outcome,
            evidence=evidence,
            finished_at=common._now(),
            reason=data.get("reason", ""),
        )
        if outcome == "succeeded":
            _identity(root, state)
            evaluation = _evaluation(root, state, data.get("evaluation"))
            _require(evaluation in evidence, "evaluation must be durable evidence")
            result["evaluation"] = evaluation
        else:
            common._text(data.get("reason"), "parent failure reason")
        state["result"] = result
    elif kind in {"registered", "reported"}:
        row, detail = _registry(root, state)
        digest = common._digest(row)
        if kind == "registered":
            _require(state["registered"] is None, "registration already frozen")
            state["registered"] = dict(
                event_sha256=digest, detail_report=common._ref(root, detail)
            )
        else:
            _require(
                state["registered"] and state["registered"]["event_sha256"] == digest,
                "register latest event before reporting",
            )
            hub = root / HUB
            refs = [_record(root, str(detail)), _record(root, str(hub))]
            _require(all(ref["size"] > 0 for ref in refs), "reports must be nonempty")
            href = os.path.relpath(detail, hub.parent)
            text = hub.read_text()
            _require(
                href in text or html.escape(href, quote=True) in text,
                "classification hub missing detail link",
            )
            state["reported"] = refs
    elif kind == "stop":
        _require(state["stop"] is None, "stop reason already frozen")
        common._text(data.get("reason"), "stop reason")
        state["stop"] = copy.deepcopy(data)
        state["status"] = "stopping"
    elif kind == "cleanup":
        _require(
            state["stop"] is not None and state["cleanup"] is None,
            "stop before one cleanup receipt",
        )
        _settled(
            root,
            state,
            strict=(
                state["result"]["outcome"] == "succeeded" if state["result"] else False
            ),
        )
        audit = _record(root, data.get("audit"), durable=True)
        doc = _json(common._path(root, audit["path"]))
        _require(
            doc.get("temporary") == "cleaned"
            and doc.get("result") in {"applied", "no_changes"}
            and doc.get("mlruns") in {"handled", "not_applicable"},
            "cleanup audit has pending temporary/result/mlruns work",
        )
        state["cleanup"] = dict(audit=audit, finished_at=common._now())
    elif kind == "close":
        _require(
            state["stop"] is not None and state["cleanup"] is not None,
            "stop and cleanup required before close",
        )
        _settled(
            root,
            state,
            strict=(
                state["result"]["outcome"] == "succeeded" if state["result"] else False
            ),
        )
        _verify_record(root, state["cleanup"]["audit"])
        state["status"] = "closed"
    else:
        raise ValueError(f"unknown classification event: {kind}")


def append_event(
    state_path: Path, event: dict, *, expected_revision: int, repo_root: Path = ROOT
) -> dict:
    """Append one checked transition atomically under the shared stable lock."""
    root = Path(repo_root).resolve()
    path = common._path(root, str(state_path))
    _require(isinstance(event, dict), "event object required")
    _load(root, path)
    with common._lock(path):
        state = _load(root, path)
        _require(
            type(expected_revision) is int and expected_revision == state["revision"],
            "stale revision; read status and reconcile",
        )
        _transition(root, state, event)
        state["revision"] += 1
        state["events"].append(
            dict(
                revision=state["revision"],
                time=common._now(),
                event=copy.deepcopy(event),
            )
        )
        _summary(state)
        common._save(path, state)
    return state


def main(argv=None, *, default_root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=default_root)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser(
        "init", help="freeze classification contract before preparation"
    )
    init.add_argument("--plan", type=Path, required=True)
    for name in ("check", "status", "event"):
        child = sub.add_parser(name)
        child.add_argument("--state", type=Path, required=True)
        if name == "event":
            child.add_argument("--event", type=Path, required=True)
            child.add_argument("--expected-revision", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            state = initialize(args.plan, repo_root=args.repo_root)
        elif args.command == "event":
            state = append_event(
                args.state,
                _json(args.event),
                expected_revision=args.expected_revision,
                repo_root=args.repo_root,
            )
        else:
            state = inspect_state(
                args.state,
                repo_root=args.repo_root,
                check_drift=args.command == "check",
            )
        print(json.dumps(state, indent=2, ensure_ascii=False, allow_nan=False))
    except (ValueError, OSError) as error:
        parser.exit(2, f"style harness: {error}\n")
