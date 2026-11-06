"""Bounded research checkpoints; never executes experiments or changes the registry.

The AI owns scientific interpretation and process management. This module checks
explicit identities, budgets, lifecycle receipts and stale-writer revisions.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import argparse
import copy
import hashlib
import html
import json
import math
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from backtest.scripts.maintenance.research_layout import (
    report_href,
    resolve_research_path,
)
from backtest.scripts.register.experiment_registry import load_registry

ROOT = Path(__file__).resolve().parents[3]
SEEDS = [42, 1000, 2000, 3000, 4000]
DEFAULT_BUDGET = {
    "max_rounds": 3,
    "max_variants": 4,
    "max_attempts_per_variant": 2,
    "max_wall_hours": 8,
    "max_no_new_evidence_rounds": 2,
    "concurrency": 1,
}
POLICIES = (
    "AGENTS.md",
    "backtest/EXPERIMENT_STANDARD.md",
    "docs/research/experiment_harness.md",
)
HUBS = {
    "all_a_model": "backtest/experiments/reports/phase_m_v1_report.html",
    "all_a_strategy": "backtest/experiments/reports/phase_m_v1_bt_report.html",
}
OUTCOMES = {"succeeded", "failed", "interrupted", "invalid", "cancelled"}
COMPOSITE_SOURCE = "multihorizon_seed_groups_v1"
BLEND_CONSTRUCTION = (
    "per_seed_daily_zscore_horizon_weighted_then_daily_zscore_equal_seed"
)
STOP_REASONS = {
    "budget_exhausted",
    "hypotheses_exhausted",
    "execution_blocked",
    "user_stopped",
}


def _now():
    return time.time()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, name):
    _require(
        isinstance(value, str) and bool(value.strip()), f"{name} must be nonempty text"
    )
    return value


def _identifier(value, name):
    _text(value, name)
    _require(
        all(
            re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part)
            for part in value.split("/")
        ),
        f"unsafe {name}",
    )
    return value


def _bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _json(path, *, allow_nonfinite=False):
    try:
        _require(path.stat().st_size <= 16 * 1024 * 1024, "JSON exceeds 16 MiB")
        result = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=float if allow_nonfinite else _invalid_number,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read JSON {path}: {error}") from error
    _require(isinstance(result, dict), f"JSON object required: {path}")
    return result


def _invalid_number(value):
    raise ValueError(f"non-finite JSON number: {value}")


def _path(root, raw, *, external=False):
    _text(raw, "path")
    _require("\\" not in raw and ".." not in Path(raw).parts, f"unsafe path: {raw}")
    path = Path(raw)
    path = path if path.is_absolute() else root / path
    _require(external or path.is_relative_to(root), f"path escapes repository: {raw}")
    for ancestor in (path, *path.parents):
        _require(not ancestor.is_symlink(), f"symlink path refused: {raw}")
        if ancestor == root:
            break
    return path


def _ref(root, path):
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def _fingerprint(root, raw):
    path = _path(root, raw, external=True)
    _require(path.is_file(), f"input missing or not a regular file: {raw}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": _ref(root, path),
        "sha256": digest.hexdigest(),
        "size": path.stat().st_size,
    }


def _files(root, values):
    _require(
        isinstance(values, list) and bool(values),
        "nonempty file evidence list required",
    )
    refs = [_fingerprint(root, _text(raw, "file path")) for raw in values]
    _require(len({item["path"] for item in refs}) == len(refs), "duplicate input path")
    return refs


def _baseline(root, track):
    rows = load_registry(_path(root, "backtest/experiments/registry.jsonl"))
    for row in reversed(rows):
        if track == "all_a_model":
            selected = (
                row.get("phase_m_protocol") == "v1"
                and row.get("current_baseline") is True
            )
        else:
            selected = (
                row.get("report_kind") == "phase_m_v1_bt_baseline"
                and row.get("current_bt") is True
            )
        if selected:
            return row
    raise ValueError(f"no explicit current baseline selection for {track}")


def _archive(root, archive_path, exp_id, track):
    _identifier(exp_id, "exp_id")
    kind = "models" if track == "all_a_model" else "strategies"
    path = _path(root, archive_path)
    _require(
        path.is_relative_to(root / "backtest/experiments" / kind),
        "archive path has wrong kind",
    )
    metadata = _json(_path(root, str(path / "metadata.json")))
    _require(
        metadata.get("entity_type", "experiment") == "experiment",
        "group cannot be an experiment",
    )
    _require(metadata.get("exp_id") == exp_id, "archive exp_id mismatch")
    _require(
        metadata.get("archive_path") == _ref(root, path),
        "metadata archive path mismatch",
    )
    expected_kind = "model" if track == "all_a_model" else "strategy"
    _require(
        metadata.get("kind") == expected_kind and metadata.get("track") == "all_a",
        "metadata kind/track mismatch",
    )
    return path


def _normalize_plan(root, raw):
    plan = copy.deepcopy(raw)
    _require(
        type(plan.get("schema_version")) is int and plan["schema_version"] in {1, 2},
        "unsupported plan schema_version",
    )
    for key in ("campaign_id", "direction", "anchor_exp_id", "baseline_ref"):
        _identifier(plan.get(key), key)
    for key in ("idea", "selection_rule", "data_version"):
        _text(plan.get(key), key)
    date.fromisoformat(plan["data_version"])
    track = _text(plan.get("track"), "track")
    _require(
        track not in {"csi1000_model", "csi1000_strategy"},
        "CSI1000 inputs are retired; explicit rebuild and new support required",
    )
    _require(track in HUBS, "unsupported track")
    _require(plan.get("seeds") == SEEDS, f"fixed seeds required: {SEEDS}")
    _require(
        isinstance(plan.get("allowed_changes"), list) and plan["allowed_changes"],
        "allowed_changes required",
    )
    for change in plan["allowed_changes"]:
        _text(change, "allowed change")
    overrides = plan.get("budget", {})
    _require(
        isinstance(overrides, dict) and not (set(overrides) - set(DEFAULT_BUDGET)),
        "unknown budget fields",
    )
    budget = {**DEFAULT_BUDGET, **overrides}
    for key, value in budget.items():
        if key == "max_wall_hours":
            valid = type(value) in (int, float) and math.isfinite(value) and value > 0
        else:
            valid = type(value) is int and value > 0
        _require(valid, f"invalid budget {key}")
    _require(budget["concurrency"] == 1, "first version supports concurrency=1")
    plan["budget"] = budget
    if "execution_budget" in plan:
        _require(plan["schema_version"] == 2, "execution_budget requires schema 2")
        execution = plan["execution_budget"]
        _require(
            track == "all_a_model"
            and isinstance(execution, dict)
            and set(execution) == {"max_model_fits", "new_label_horizons"},
            "invalid execution_budget fields/track",
        )
        _require(
            type(execution["max_model_fits"]) is int
            and execution["max_model_fits"] > 0,
            "invalid execution max_model_fits",
        )
        horizons = execution["new_label_horizons"]
        _require(
            isinstance(horizons, list)
            and bool(horizons)
            and all(type(h) is int and h > 0 for h in horizons)
            and len(set(horizons)) == len(horizons),
            "invalid execution new_label_horizons",
        )
    _archive(root, plan.get("archive_path"), plan["anchor_exp_id"], track)
    return plan


def _save(path, state):
    state.pop("checksum", None)
    state["checksum"] = _digest(state)
    payload = json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    _require(len(payload.encode()) <= 16 * 1024 * 1024, "state exceeds 16 MiB")
    fd, name = tempfile.mkstemp(prefix=".harness-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _lock(path):
    # A stable sidecar survives atomic replacement. A stale revision is checked
    # inside this lock; the lock protects state writes, not external processes.
    import fcntl

    lock = path.with_name(f".{path.name}.lock")
    _require(not lock.is_symlink(), "symlink lock refused")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "concurrent state writer; retry from fresh status"
            ) from error
        yield
    finally:
        os.close(fd)


def _load(root, state_path):
    path = _path(root, str(state_path))
    state = _json(path)
    checksum = state.pop("checksum", None)
    _require(
        checksum == _digest(state),
        "state checksum mismatch; do not reset or repair silently",
    )
    state["checksum"] = checksum
    _require(
        type(state.get("schema_version")) is int
        and state["schema_version"] in {1, 2}
        and state["schema_version"] == state["plan"]["schema_version"],
        "unsupported state schema",
    )
    if state["schema_version"] == 2:
        _require(isinstance(state.get("jobs"), dict), "v2 jobs ledger missing")
    _require(
        state.get("repo_root") == str(root), "state belongs to another repository root"
    )
    _require(state.get("state_path") == _ref(root, path), "state path mismatch")
    _require(
        path == _path(root, state["plan"]["archive_path"]) / "docs/harness-state.json",
        "unsafe state path",
    )
    return state


def _identity(root, state):
    for frozen in [state["plan_source"], *state["policies"], *state["inputs"]]:
        _require(
            _fingerprint(root, frozen["path"]) == frozen,
            f"input/policy drift: {frozen['path']}",
        )
    _require(
        _digest(_baseline(root, state["plan"]["track"])) == state["baseline_sha256"],
        "baseline changed/drift",
    )
    for variant in state["variants"].values():
        _archive(
            root, variant["archive_path"], variant["exp_id"], state["plan"]["track"]
        )
        for frozen in variant["configs"]:
            _require(
                _fingerprint(root, frozen["path"]) == frozen,
                f"config drift: {frozen['path']}",
            )


def _launch_guard(root, state):
    _require(
        state["status"] == "active",
        f"campaign is {state['status']}; new launches refused",
    )
    _identity(root, state)
    _require(
        _now() < state["deadline"],
        "exploration time budget exhausted; finalize existing attempts",
    )
    empty_rounds = 0
    for reflection in reversed(state["reflections"]):
        if reflection["new_evidence"]:
            break
        empty_rounds += 1
    _require(
        empty_rounds < state["plan"]["budget"]["max_no_new_evidence_rounds"],
        "no-new-evidence round limit reached",
    )


def initialize(plan_path: Path, *, repo_root: Path = ROOT) -> dict:
    """Freeze one real draft's plan and inputs, never overwriting prior progress."""
    root = Path(repo_root).resolve()
    source = _path(root, str(plan_path))
    plan = _normalize_plan(root, _json(source))
    anchor = _path(root, plan["archive_path"])
    _require(
        source == anchor / "configs/harness-plan.json",
        "plan must live in anchor configs/harness-plan.json",
    )
    current = _baseline(root, plan["track"])
    _require(
        current.get("exp_id") == plan["baseline_ref"],
        "baseline_ref is not the explicit current selection",
    )
    path = _path(root, str(anchor / "docs/harness-state.json"))
    inputs = _files(root, plan.get("inputs"))
    policies = [_fingerprint(root, raw) for raw in POLICIES]
    with _lock(root / "backtest/experiments/harness-campaigns"):
        for kind in ("models", "strategies"):
            for existing in (root / "backtest/experiments" / kind).glob(
                "**/docs/harness-state.json"
            ):
                prior = _load(root, existing)
                _require(
                    prior["plan"]["campaign_id"] != plan["campaign_id"],
                    "campaign already exists; inspect original state instead",
                )
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock(path):
            _require(
                not path.exists(), "state already exists; inspect and resume instead"
            )
            now = _now()
            state = {
                "schema_version": plan["schema_version"],
                "repo_root": str(root),
                "state_path": _ref(root, path),
                "plan": plan,
                "plan_source": _fingerprint(root, str(source)),
                "inputs": inputs,
                "policies": policies,
                "baseline_sha256": _digest(current),
                "baseline_model_ref": current.get("model_ref"),
                "started_at": now,
                "deadline": now + plan["budget"]["max_wall_hours"] * 3600,
                "revision": 0,
                "status": "active",
                "variants": {},
                "reflections": [],
                "stop": None,
                "cleanup": None,
                "events": [],
            }
            if plan["schema_version"] == 2:
                state["jobs"] = {}
            _save(path, state)
    return state


def inspect_state(
    state_path: Path, *, repo_root: Path = ROOT, check_drift: bool = True
) -> dict:
    """Check launch eligibility, or read status even after drift/budget exhaustion."""
    root = Path(repo_root).resolve()
    state = _load(root, state_path)
    if check_drift:
        _launch_guard(root, state)
    return state


def _settled(root, state, *, round_number=None):
    _require(
        not any(job["outcome"] == "running" for job in state.get("jobs", {}).values()),
        "running fit is not settled; reconcile and record fit_result",
    )
    for job in state.get("jobs", {}).values():
        for frozen in job["evidence"]:
            _require(
                _fingerprint(root, frozen["path"]) == frozen,
                "fit evidence changed",
            )
    variants = [
        v
        for v in state["variants"].values()
        if round_number is None or v["round"] == round_number
    ]
    _require(bool(variants), "no variants to settle")
    registered = {
        _digest(row)
        for row in load_registry(root / "backtest/experiments/registry.jsonl")
        if (row.get("harness") or {}).get("campaign_id") == state["plan"]["campaign_id"]
    }
    for variant in variants:
        _require(
            variant["attempts"],
            "unstarted plan must have a cancelled result before closure",
        )
        for attempt in variant["attempts"]:
            _require(attempt["outcome"] != "running", "running attempt is not settled")
            _require(
                attempt.get("registered") and attempt.get("reported"),
                "attempt must be registered and reported",
            )
            _require(
                attempt["registered"]["event_sha256"] in registered,
                "registered evidence missing from registry",
            )
            for frozen in attempt["evidence"]:
                _require(
                    _fingerprint(root, frozen["path"]) == frozen,
                    "attempt evidence changed",
                )
        # Hubs and the latest detail can be regenerated as the campaign grows.
        # They must still exist; only the latest detail's bytes are frozen here.
        latest = variant["attempts"][-1]
        current_row = _registry_receipt(root, state, variant, latest)
        _require(
            _digest(current_row) == latest["registered"]["event_sha256"],
            "latest registry receipt changed; register and report again",
        )
        detail, hub = latest["reported"]
        _require(
            _fingerprint(root, detail["path"]) == detail, "report evidence changed"
        )
        _require(_path(root, hub["path"]).is_file(), "hub report missing")
        href = report_href(detail["path"], hub["path"], root)
        body = _path(root, hub["path"]).read_text(encoding="utf-8")
        _require(
            href in body or html.escape(href, quote=True) in body,
            "hub report detail link missing",
        )


def _source_file(root, item, path_key, sha_key):
    _require(isinstance(item, dict), "source proof must be an object")
    frozen = _fingerprint(root, _text(item.get(path_key), path_key))
    _require(frozen["sha256"] == item.get(sha_key), f"source SHA mismatch: {path_key}")
    return frozen


def _composite_sources(root, state, variant, doc):
    _require(
        state["plan"]["schema_version"] == 2
        and type(doc.get("schema_version")) is int
        and doc["schema_version"] == 2,
        "composite prediction source requires schema 2",
    )
    _require("sessions" not in doc, "composite sources must not fabricate sessions")
    config_ref = doc["composition_config"]
    config = _source_file(root, config_ref, "path", "sha256")
    _require(
        config in variant["configs"],
        "composition_config is not a frozen variant config",
    )
    blend = _json(_path(root, config["path"]))["signal_blend"]
    _require(
        blend["construction"] == BLEND_CONSTRUCTION, "unsupported blend construction"
    )
    weights = blend["horizon_weights"]
    _require(
        isinstance(weights, dict)
        and bool(weights)
        and all(
            isinstance(h, str) and re.fullmatch(r"[1-9][0-9]*", h) for h in weights
        ),
        "invalid config horizon weights",
    )
    _require(
        all(
            type(w) in (int, float) and math.isfinite(w) and w > 0
            for w in weights.values()
        )
        and math.isclose(sum(weights.values()), 1.0, rel_tol=0, abs_tol=1e-12),
        "config weights must be finite positive and normalized",
    )
    horizons = sorted(int(h) for h in weights)
    groups = doc["seed_groups"]
    _require(
        isinstance(groups, list)
        and len(groups) == 5
        and all(type(group["seed"]) is int for group in groups)
        and [group["seed"] for group in groups] == SEEDS,
        "composite requires all five seed groups in canonical seed order",
    )
    source_files, sessions, group_files = set(), set(), set()
    for group in groups:
        prediction = _source_file(root, group, "prediction_path", "prediction_sha256")
        _require(
            prediction["path"] not in group_files, "duplicate group prediction source"
        )
        group_files.add(prediction["path"])
        components = group["components"]
        _require(
            isinstance(components, list)
            and all(type(leg["label_horizon"]) is int for leg in components)
            and [leg["label_horizon"] for leg in components] == horizons,
            "component horizon set/order differs from frozen config",
        )
        for leg in components:
            _require(
                type(leg["seed"]) is int and leg["seed"] == group["seed"],
                "component seed mixup",
            )
            weight = leg["weight"]
            _require(
                type(weight) in (int, float)
                and math.isfinite(weight)
                and weight > 0
                and math.isclose(
                    weight, weights[str(leg["label_horizon"])], rel_tol=0, abs_tol=1e-12
                ),
                "component weight differs from frozen config",
            )
            session = _identifier(leg["session"], "session")
            session_dir = _path(root, leg["session_dir"])
            _require(
                session_dir.is_relative_to(root / "backtest/result")
                and session_dir.name == session
                and _ref(root, session_dir) not in sessions,
                "duplicate or mismatched real session source",
            )
            sessions.add(_ref(root, session_dir))
            meta = _json(_path(root, str(session_dir / "meta.json")))
            regime = meta["regime_adapt"]
            _require(
                meta["session_name"] == session
                and type(regime["seed"]) is int
                and regime["seed"] == group["seed"]
                and type(regime["label_horizon"]) is int
                and regime["label_horizon"] == leg["label_horizon"],
                "real session metadata seed/horizon/session mismatch",
            )
            model = _source_file(root, leg, "model_path", "model_sha256")
            _require(
                _path(root, model["path"]).is_relative_to(session_dir),
                "model outside real session",
            )
            pred = _source_file(root, leg, "prediction_path", "prediction_sha256")
            for source in (model, pred):
                _require(
                    source["path"] not in source_files, "duplicate component source"
                )
                source_files.add(source["path"])
    _require(
        not group_files.intersection(source_files),
        "group prediction aliases component source",
    )


def _evaluation(root, state, raw, variant):
    """Validate official output presence/finite cells, never recalculate metrics."""
    path = _path(root, _text(raw, "evaluation"))
    doc = _json(path, allow_nonfinite=True)
    try:
        if state["plan"]["track"] == "all_a_model":
            if doc.get("prediction_source_type") == COMPOSITE_SOURCE:
                _composite_sources(root, state, variant, doc)
            else:
                _require(
                    "prediction_source_type" not in doc,
                    "unsupported prediction source type",
                )
                sessions = doc["sessions"]
                _require(
                    isinstance(sessions, list)
                    and len(sessions) == 5
                    and sorted(item["seed"] for item in sessions) == SEEDS,
                    "evaluation requires all five seeds",
                )
                _require(
                    len({_text(item["session"], "session") for item in sessions}) == 5,
                    "evaluation sessions must be distinct",
                )
            _require(
                doc["data_version"] == state["plan"]["data_version"],
                "evaluation data_version mismatch",
            )
            ensemble = doc["pools"]["all"]["ensemble"]
            cell = ensemble["head"]["3"]["5"]
            values = [
                cell[key]
                for key in ("net_ann", "net_ann_vol", "net_sharpe", "ann", "turnover")
            ]
            values.append(ensemble["h5"]["rank_ic_mean"])
        else:
            _require(doc["seeds"] == SEEDS, "evaluation requires all five seeds")
            _require(
                doc["model_ref"] == state["baseline_model_ref"],
                "strategy evaluation model_ref mismatch",
            )
            _require(
                doc["evaluation_mode"] == "historical_selection_window",
                "strategy evaluation mode mismatch",
            )
            cell = doc["ensemble"]["full_period"]
            values = [
                cell[key]
                for key in (
                    "annualized_return",
                    "sharpe_ratio",
                    "max_drawdown",
                    "alpha",
                    "beta",
                    "benchmark_cumulative_return",
                )
            ]
        _require(
            all(type(v) in (int, float) and math.isfinite(v) for v in values),
            "official evaluation metrics must be finite",
        )
        _require(
            type(cell["n_days"]) is int and cell["n_days"] > 0, "evaluation has no days"
        )
    except (KeyError, TypeError) as error:
        raise ValueError(f"official evaluation structure missing: {error}") from error
    return _fingerprint(root, str(path))


def _registry_receipt(root, state, variant, attempt):
    rows = load_registry(root / "backtest/experiments/registry.jsonl")
    row = next(
        (row for row in reversed(rows) if row.get("exp_id") == variant["exp_id"]), None
    )
    _require(row is not None, "experiment not in registry")
    receipt = row.get("harness") or {}
    _require(
        receipt.get("campaign_id") == state["plan"]["campaign_id"]
        and receipt.get("attempt_id") == attempt["attempt_id"],
        "registry attempt identity mismatch",
    )
    _require(
        row.get("baseline_ref") == state["plan"]["baseline_ref"],
        "registered baseline mismatch",
    )
    _require(
        row.get("hypothesis") == variant["hypothesis"], "registered hypothesis changed"
    )
    _require(
        row.get("data_version") == state["plan"]["data_version"],
        "registered data_version mismatch",
    )
    _require(
        not any(
            row.get(key) is True
            for key in (
                "current_baseline",
                "current_bt",
                "current",
                "is_current_baseline",
            )
        ),
        "harness cannot authorize promotion",
    )
    _require(
        not any(
            row.get(key) for key in ("baseline_version", "bt_version", "promoted_to")
        ),
        "harness candidate cannot introduce promotion/version fields",
    )
    if attempt["outcome"] == "succeeded":
        _require(
            row.get("state") == "completed" and row.get("seeds") == SEEDS,
            "completed registration requires full five seeds",
        )
        _require(
            row.get("evaluation_mode") == "historical_selection_window",
            "historical evaluation_mode required",
        )
        raw = row.get("portable_eval_output") or row.get("eval_output")
        _require(
            _evaluation(root, state, raw, variant) == attempt["evaluation"],
            "registered evaluation mismatch",
        )
    else:
        _require(
            row.get("state") in {"failed", "interrupted", "invalid", "cancelled"},
            "failure cannot be registered as completed",
        )
    if state["plan"]["track"] == "all_a_model":
        _require(
            row.get("phase_m_protocol") == "v1",
            "model registration requires phase_m_protocol=v1",
        )
    else:
        _require(
            row.get("report_kind") == "phase_m_v1_bt",
            "strategy registration requires phase_m_v1_bt",
        )
    _text(row.get("detail_report"), "detail_report")
    return row


def _transition(root, state, data):
    kind = _text(data.get("type"), "event type")
    _require(not state["status"].startswith("closed"), "campaign is closed")
    if kind == "plan_variant":
        _launch_guard(root, state)
        exp_id = _identifier(data.get("exp_id"), "exp_id")
        _require(
            exp_id not in state["variants"], "variant already planned; do not overwrite"
        )
        _require(
            len(state["variants"]) < state["plan"]["budget"]["max_variants"],
            "variant budget exhausted",
        )
        round_number = data.get("round")
        _require(
            type(round_number) is int
            and 1 <= round_number <= state["plan"]["budget"]["max_rounds"],
            "round budget exceeded",
        )
        expected_round = len(state["reflections"]) + 1
        _require(
            round_number == expected_round, "round must follow the previous reflection"
        )
        _require(
            data.get("changed_axis") in state["plan"]["allowed_changes"],
            "changed axis is outside allowed_changes",
        )
        archive = _archive(
            root, data.get("archive_path"), exp_id, state["plan"]["track"]
        )
        configs = _files(root, data.get("configs"))
        _require(
            all(
                _path(root, ref["path"]).is_relative_to(archive / "configs")
                for ref in configs
            ),
            "variant configs must belong to its archive",
        )
        state["variants"][exp_id] = {
            "exp_id": exp_id,
            "archive_path": _ref(root, archive),
            "round": round_number,
            "hypothesis": _text(data.get("hypothesis"), "hypothesis"),
            "changed_axis": data["changed_axis"],
            "configs": configs,
            "attempts": [],
        }
    elif kind in {"fit_start", "fit_result"}:
        _require(state["plan"]["schema_version"] == 2, "fit events require schema 2")
        execution = state["plan"].get("execution_budget")
        _require(execution is not None, "fit events require execution_budget")
        jobs = state["jobs"]
        job_id = _identifier(data.get("job_id"), "job_id")
        if kind == "fit_start":
            _launch_guard(root, state)
            exp_id = _identifier(data.get("exp_id"), "exp_id")
            variant = state["variants"].get(exp_id)
            _require(
                variant is not None
                and variant["attempts"]
                and variant["attempts"][-1]["outcome"] == "running",
                "fit_start requires a running parent attempt",
            )
            _require(
                job_id not in jobs, "job_id already used; retries require a new job_id"
            )
            _require(
                not any(job["outcome"] == "running" for job in jobs.values()),
                "fit already running; default serial fit limit is 1",
            )
            _require(
                len(jobs) < execution["max_model_fits"], "model fit budget exhausted"
            )
            _require(
                type(data.get("seed")) is int and data["seed"] in SEEDS,
                "invalid fit seed",
            )
            _require(
                type(data.get("label_horizon")) is int
                and data["label_horizon"] in execution["new_label_horizons"],
                "fit label_horizon is outside execution budget",
            )
            command = data.get("command")
            _require(
                isinstance(command, list)
                and command
                and all(isinstance(v, str) and v for v in command),
                "command must be an argv list; it is recorded, never executed",
            )
            jobs[job_id] = {
                "job_id": job_id,
                "exp_id": exp_id,
                "attempt_id": variant["attempts"][-1]["attempt_id"],
                "seed": data["seed"],
                "label_horizon": data["label_horizon"],
                "command": command,
                "outcome": "running",
                "started_at": _now(),
            }
        else:
            job = jobs.get(job_id)
            _require(
                job is not None and job["outcome"] == "running",
                "no running fit for job_id",
            )
            outcome = _text(data.get("outcome"), "fit outcome")
            _require(
                outcome in {"succeeded", "failed", "interrupted"}, "invalid fit outcome"
            )
            evidence = _files(root, data.get("evidence"))
            archive = _path(root, state["variants"][job["exp_id"]]["archive_path"])
            _require(
                all(
                    _path(root, ref["path"]).is_relative_to(archive)
                    or _path(root, ref["path"]).is_relative_to(root / "backtest/result")
                    for ref in evidence
                ),
                "fit evidence must be in its durable archive or real result model/log paths",
            )
            job.update(outcome=outcome, finished_at=_now(), evidence=evidence)
    elif kind in {"start", "result", "registered", "reported"}:
        variant = state["variants"].get(_identifier(data.get("exp_id"), "exp_id"))
        _require(variant is not None, "unknown variant")
        attempts = variant["attempts"]
        if kind == "start":
            _launch_guard(root, state)
            _require(
                not any(
                    a["outcome"] == "running"
                    for v in state["variants"].values()
                    for a in v["attempts"]
                ),
                "attempt already running; reconcile processes before launching",
            )
            _require(
                len(attempts) < state["plan"]["budget"]["max_attempts_per_variant"],
                "attempt budget exhausted",
            )
            if attempts:
                _require(
                    attempts[-1]["outcome"] in {"failed", "interrupted", "invalid"},
                    "successful/cancelled variant cannot restart",
                )
                _require(
                    attempts[-1].get("registered") and attempts[-1].get("reported"),
                    "previous attempt must be registered and reported",
                )
            _require(
                variant["round"] == len(state["reflections"]) + 1,
                "reflected round is frozen",
            )
            command = data.get("command")
            _require(
                isinstance(command, list)
                and command
                and all(isinstance(v, str) and v for v in command),
                "command must be an argv list; it is recorded, never executed",
            )
            attempts.append(
                {
                    "attempt_id": f"{state['plan']['campaign_id']}/{variant['exp_id']}/a{len(attempts) + 1}",
                    "outcome": "running",
                    "started_at": _now(),
                    "command": command,
                }
            )
        elif kind == "result":
            _require(
                not any(
                    job["outcome"] == "running" and job["exp_id"] == variant["exp_id"]
                    for job in state.get("jobs", {}).values()
                ),
                "running fit must finish before parent attempt result",
            )
            outcome = _text(data.get("outcome"), "outcome")
            _require(outcome in OUTCOMES, "invalid outcome")
            if not attempts and outcome == "cancelled":
                attempts.append(
                    {
                        "attempt_id": f"{state['plan']['campaign_id']}/{variant['exp_id']}/cancelled",
                        "outcome": "running",
                    }
                )
            _require(
                attempts and attempts[-1]["outcome"] == "running",
                "no running attempt to finish",
            )
            evidence = _files(root, data.get("evidence"))
            archive = _path(root, variant["archive_path"])
            _require(
                all(
                    _path(root, ref["path"]).is_relative_to(archive) for ref in evidence
                ),
                "save durable result evidence in the experiment archive",
            )
            if outcome == "succeeded":
                evaluation = _evaluation(root, state, data.get("evaluation"), variant)
                _require(
                    evaluation in evidence,
                    "evaluation must be included in durable evidence",
                )
                attempts[-1]["evaluation"] = evaluation
            if outcome != "succeeded":
                _text(data.get("reason"), "failure reason")
            attempts[-1].update(
                outcome=outcome,
                finished_at=_now(),
                evidence=evidence,
                reason=data.get("reason", ""),
            )
        else:
            _require(
                attempts and attempts[-1]["outcome"] != "running",
                "result required before registration/report",
            )
            attempt = attempts[-1]
            row = _registry_receipt(root, state, variant, attempt)
            if kind == "registered":
                if attempt.get("registered", {}).get("event_sha256") != _digest(row):
                    attempt.pop("reported", None)
                    state["cleanup"] = None
                attempt["registered"] = {
                    "event_sha256": _digest(row),
                    "detail_report": row["detail_report"],
                }
            else:
                _require(
                    attempt.get("registered", {}).get("event_sha256") == _digest(row),
                    "register the latest event before report receipt",
                )
                detail = resolve_research_path(row["detail_report"], root)
                detail = _path(root, str(detail))
                hub = _path(root, HUBS[state["plan"]["track"]])
                refs = _files(root, [str(detail), str(hub)])
                _require(all(ref["size"] > 0 for ref in refs), "empty report")
                target = report_href(detail, hub, root)
                text = hub.read_text(encoding="utf-8")
                _require(
                    target in text or html.escape(target, quote=True) in text,
                    "hub report is missing experiment detail link",
                )
                attempt["reported"] = refs
    elif kind == "reflection":
        number = data.get("round")
        _require(
            type(number) is int and number == len(state["reflections"]) + 1,
            "reflection round out of sequence",
        )
        _settled(root, state, round_number=number)
        for key in ("observations", "interpretation"):
            _text(data.get(key), key)
        _require(type(data.get("new_evidence")) is bool, "new_evidence must be boolean")
        _require(
            isinstance(data.get("next_hypothesis", ""), str),
            "next_hypothesis must be text",
        )
        state["reflections"].append(copy.deepcopy(data))
    elif kind == "stop":
        _require(state["stop"] is None, "stop reason already frozen")
        _require(
            _text(data.get("reason"), "stop reason") in STOP_REASONS,
            "unknown stop reason",
        )
        state["status"] = "stopping"
        state["stop"] = copy.deepcopy(data)
    elif kind == "cleanup":
        _require(state["stop"] is not None, "stop exploration before global cleanup")
        if state["variants"]:
            _settled(root, state)
        outcome = _text(data.get("outcome"), "outcome")
        _require(outcome in {"complete", "blocked"}, "invalid cleanup outcome")
        audit = _path(root, data.get("audit"))
        doc = _json(audit)
        _require(
            doc.get("temporary") in {"cleaned", "blocked"},
            "temporary cleanup evidence required",
        )
        if outcome == "complete":
            _require(
                doc.get("temporary") == "cleaned"
                and doc.get("result") in {"applied", "no_changes"}
                and doc.get("mlruns") in {"handled", "not_applicable"},
                "cleanup audit has pending result/mlruns work",
            )
        else:
            _text(data.get("reason"), "cleanup blocked reason")
        state["cleanup"] = {**data, "audit": _fingerprint(root, str(audit))}
    elif kind == "close":
        _require(state["stop"] is not None, "stop reason required before closure")
        if state["variants"]:
            _settled(root, state)
        _require(
            state["cleanup"] is not None, "cleanup receipt required before closure"
        )
        audit = state["cleanup"]["audit"]
        _require(_fingerprint(root, audit["path"]) == audit, "cleanup audit changed")
        state["status"] = (
            "closed"
            if state["cleanup"]["outcome"] == "complete"
            else "closed_with_pending_cleanup"
        )
    elif kind == "note":
        _text(data.get("text"), "note text")
    else:
        raise ValueError(f"unknown event type: {kind}")


def append_event(
    state_path: Path, event: dict, *, expected_revision: int, repo_root: Path = ROOT
) -> dict:
    """Append one transition, checking revision under lock before atomic write."""
    root = Path(repo_root).resolve()
    path = _path(root, str(state_path))
    _require(isinstance(event, dict), "event must be an object")
    # Reject a foreign state before even creating a lock next to it.
    _load(root, path)
    with _lock(path):
        state = _load(root, path)
        _require(
            type(expected_revision) is int and expected_revision == state["revision"],
            "stale revision; read status and reconcile",
        )
        _transition(root, state, event)
        state["revision"] += 1
        state["events"].append(
            {
                "revision": state["revision"],
                "time": _now(),
                "event": copy.deepcopy(event),
            }
        )
        _save(path, state)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="freeze a real draft's task contract")
    init.add_argument("--plan", type=Path, required=True)
    for command in ("check", "status", "event"):
        child = sub.add_parser(command)
        child.add_argument("--state", type=Path, required=True)
        if command == "event":
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
        print(json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError) as error:
        parser.exit(2, f"harness: {error}\n")


if __name__ == "__main__":
    main()
