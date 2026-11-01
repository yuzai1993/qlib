"""Research lifecycle guards, exercised without running Qlib or live experiments."""

import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def api():
    return importlib.import_module("backtest.scripts.maintenance.experiment_harness")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def research(tmp_path):
    root = tmp_path / "repo"
    for rel in (
        "AGENTS.md",
        "backtest/EXPERIMENT_STANDARD.md",
        "docs/research/experiment_harness.md",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("frozen policy\n", encoding="utf-8")
    base = {
        "exp_id": "baseline/v2",
        "phase_m_protocol": "v1",
        "baseline_version": "v2",
        "current_baseline": True,
        "state": "completed",
        "seeds": [42, 1000, 2000, 3000, 4000],
    }
    rows = [{**base, "exp_id": "baseline/v4", "baseline_version": "v4"}, base]
    reg = root / "backtest/experiments/registry.jsonl"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("".join(json.dumps(row) + "\n" for row in rows))
    archive = "backtest/experiments/models/features/first"
    write_json(
        root / archive / "metadata.json",
        {
            "exp_id": "features/first",
            "archive_path": archive,
            "kind": "model",
            "track": "all_a",
        },
    )
    config = root / "backtest/configs/reference.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("run: {mode: train_only}\n")
    plan = {
        "schema_version": 1,
        "campaign_id": "features-20261101",
        "idea": "检验额外特征",
        "track": "all_a_model",
        "direction": "features",
        "anchor_exp_id": "features/first",
        "archive_path": archive,
        "baseline_ref": "baseline/v2",
        "allowed_changes": ["features"],
        "selection_rule": "按官方主格并列呈现净年化、波动和夏普，不自动晋升",
        "data_version": "2026-07-31",
        "inputs": ["backtest/configs/reference.yaml"],
        "seeds": [42, 1000, 2000, 3000, 4000],
    }
    path = write_json(root / archive / "configs/harness-plan.json", plan)
    return root, path, plan


def initialized(research):
    root, plan_path, _ = research
    state = api().initialize(plan_path, repo_root=root)
    state_path = root / state["state_path"]
    return root, state_path, state


def event(root, path, payload):
    state = api().inspect_state(path, repo_root=root, check_drift=False)
    return api().append_event(
        path, payload, expected_revision=state["revision"], repo_root=root
    )


def plan_variant(root, path, exp_id="features/first", round_number=1):
    name = exp_id.split("/")[-1]
    archive = f"backtest/experiments/models/features/{name}"
    write_json(
        root / archive / "metadata.json",
        {
            "exp_id": exp_id,
            "archive_path": archive,
            "kind": "model",
            "track": "all_a",
        },
    )
    config = root / archive / "configs/model.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("feature: new\n")
    return event(
        root,
        path,
        {
            "type": "plan_variant",
            "exp_id": exp_id,
            "archive_path": archive,
            "round": round_number,
            "hypothesis": "新特征改善排序",
            "changed_axis": "features",
            "configs": [str(config.relative_to(root))],
        },
    )


def start(root, path, exp_id="features/first"):
    return event(
        root,
        path,
        {"type": "start", "exp_id": exp_id, "command": ["python", "train.py"]},
    )


def finish(root, path, outcome="failed", exp_id="features/first"):
    log = (
        root
        / "backtest/experiments/models/features"
        / exp_id.split("/")[-1]
        / "metrics/evidence.json"
    )
    write_json(log, {"reason": "controlled test evidence"})
    return event(
        root,
        path,
        {
            "type": "result",
            "exp_id": exp_id,
            "outcome": outcome,
            "reason": "测试终态",
            "evidence": [str(log.relative_to(root))],
        },
    )


def register_and_report(root, path, exp_id="features/first"):
    state = api().inspect_state(path, repo_root=root, check_drift=False)
    variant = state["variants"][exp_id]
    attempt = variant["attempts"][-1]
    detail = f'{variant["archive_path"]}/reports/detail.html'
    row = {
        "exp_id": exp_id,
        "phase_m_protocol": "v1",
        "state": "completed" if attempt["outcome"] == "succeeded" else "failed",
        "hypothesis": variant["hypothesis"],
        "baseline_ref": "baseline/v2",
        "data_version": "2026-07-31",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "detail_report": detail,
        "harness": {
            "campaign_id": state["plan"]["campaign_id"],
            "attempt_id": attempt["attempt_id"],
        },
    }
    if attempt["outcome"] == "succeeded":
        row.update(
            evaluation_mode="historical_selection_window",
            portable_eval_output=attempt["evaluation"]["path"],
        )
    registry = root / "backtest/experiments/registry.jsonl"
    with registry.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    event(root, path, {"type": "registered", "exp_id": exp_id})
    detail_path = root / detail
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.write_text("<html>实验执行失败；缺失指标</html>")
    hub = root / "backtest/experiments/reports/phase_m_v1_report.html"
    hub.parent.mkdir(parents=True, exist_ok=True)
    from backtest.scripts.maintenance.research_layout import report_href

    hub.write_text(f'<a href="{report_href(detail_path, hub, root)}">失败实验</a>')
    return event(root, path, {"type": "reported", "exp_id": exp_id})


def test_initialization_freezes_latest_explicit_selection_and_never_overwrites(
    research,
):
    root, path, state = initialized(research)
    assert state["plan"]["baseline_ref"] == "baseline/v2"
    assert state["plan"]["budget"]["max_variants"] == 4
    assert state["revision"] == 0
    original = path.read_bytes()
    with pytest.raises(ValueError, match="exists"):
        api().initialize(research[1], repo_root=root)
    assert path.read_bytes() == original


@pytest.mark.parametrize("change", ["policy", "input", "plan", "baseline"])
def test_drift_blocks_launch_but_status_remains_readable(research, change):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    if change == "policy":
        (root / "AGENTS.md").write_text("new policy")
    elif change == "input":
        (root / "backtest/configs/reference.yaml").write_text("changed input")
    elif change == "plan":
        research[1].write_text(research[1].read_text() + "\n")
    else:
        reg = root / "backtest/experiments/registry.jsonl"
        with reg.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "exp_id": "baseline/new",
                        "phase_m_protocol": "v1",
                        "current_baseline": True,
                    }
                )
                + "\n"
            )
    with pytest.raises(ValueError, match="drift|changed"):
        start(root, path)
    assert api().inspect_state(path, repo_root=root, check_drift=False)["revision"] == 1


@pytest.mark.parametrize("track", ["csi1000_model", "csi1000_strategy"])
def test_retired_csi_tracks_refuse_initialization(research, track):
    root, plan_path, plan = research
    write_json(plan_path, {**plan, "track": track})
    with pytest.raises(ValueError, match="retired"):
        api().initialize(plan_path, repo_root=root)


@pytest.mark.parametrize(
    "budget",
    [
        {"max_variants": 0},
        {"max_rounds": True},
        {"max_wall_hours": float("nan")},
        {"concurrency": 2},
    ],
)
def test_invalid_budgets_do_not_create_state(research, budget):
    root, plan_path, plan = research
    write_json(plan_path, {**plan, "budget": budget})
    with pytest.raises(ValueError):
        api().initialize(plan_path, repo_root=root)
    assert not (plan_path.parent.parent / "docs/harness-state.json").exists()


def test_metadata_path_escape_or_symlink_is_rejected(research):
    root, plan_path, plan = research
    write_json(
        plan_path, {**plan, "archive_path": "backtest/experiments/models/../other"}
    )
    with pytest.raises(ValueError, match="path"):
        api().initialize(plan_path, repo_root=root)
    write_json(plan_path, plan)
    source = root / "backtest/configs/reference.yaml"
    target = root.parent / "outside.yaml"
    target.write_text("outside")
    source.unlink()
    source.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        api().initialize(plan_path, repo_root=root)


def test_stale_revision_and_duplicate_launch_cannot_mutate_state(research):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    with pytest.raises(ValueError, match="revision"):
        api().append_event(
            path,
            {"type": "stop", "reason": "user_stopped"},
            expected_revision=0,
            repo_root=root,
        )
    state = start(root, path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="running"):
        start(root, path)
    assert path.read_bytes() == before
    assert state["variants"]["features/first"]["attempts"][0]["outcome"] == "running"


def test_changed_variant_config_refuses_start(research):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    config = root / "backtest/experiments/models/features/first/configs/model.yaml"
    config.write_text("feature: another")
    with pytest.raises(ValueError, match="drift|changed"):
        start(root, path)


def test_budget_limits_count_variants_attempts_and_elapsed_time(research, monkeypatch):
    root, plan_path, plan = research
    write_json(
        plan_path,
        {**plan, "budget": {"max_variants": 1, "max_attempts_per_variant": 1}},
    )
    root, path, state = initialized(research)
    plan_variant(root, path)
    with pytest.raises(ValueError, match="variant.*budget"):
        plan_variant(root, path, "features/second")
    start(root, path)
    finish(root, path)
    register_and_report(root, path)
    with pytest.raises(ValueError, match="attempt.*budget"):
        start(root, path)
    monkeypatch.setattr(api(), "_now", lambda: state["started_at"] + 9 * 3600)
    with pytest.raises(ValueError, match="time.*budget"):
        api().inspect_state(path, repo_root=root)


def test_interrupted_attempt_requires_registration_and_preserves_retry_history(
    research,
):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    start(root, path)
    finish(root, path, "interrupted")
    with pytest.raises(ValueError, match="register|report"):
        start(root, path)
    register_and_report(root, path)
    state = start(root, path)
    attempts = state["variants"]["features/first"]["attempts"]
    assert [item["outcome"] for item in attempts] == ["interrupted", "running"]
    assert attempts[0]["attempt_id"] != attempts[1]["attempt_id"]


def test_registration_must_belong_to_current_attempt_and_cannot_promote(research):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    start(root, path)
    finish(root, path)
    with pytest.raises(ValueError, match="registry|register"):
        event(root, path, {"type": "registered", "exp_id": "features/first"})
    register_and_report(root, path)
    start(root, path)
    finish(root, path)
    with pytest.raises(ValueError, match="attempt"):
        event(root, path, {"type": "registered", "exp_id": "features/first"})


def test_report_requires_actual_hub_link(research):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    start(root, path)
    finish(root, path)
    register_and_report(root, path)
    hub = root / "backtest/experiments/reports/phase_m_v1_report.html"
    hub.write_text("<html>stale report</html>")
    with pytest.raises(ValueError, match="report|link"):
        event(root, path, {"type": "reported", "exp_id": "features/first"})


def test_drift_does_not_prevent_failure_documentation_and_pending_cleanup_closure(
    research,
):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    start(root, path)
    (root / "AGENTS.md").write_text("policy changed during run")
    finish(root, path)
    register_and_report(root, path)
    event(root, path, {"type": "stop", "reason": "execution_blocked"})
    with pytest.raises(ValueError, match="cleanup"):
        event(root, path, {"type": "close"})
    audit = root / research[2]["archive_path"] / "docs/cleanup-audit.json"
    write_json(
        audit, {"result": "blocked", "mlruns": "blocked", "temporary": "cleaned"}
    )
    event(
        root,
        path,
        {
            "type": "cleanup",
            "outcome": "blocked",
            "audit": str(audit.relative_to(root)),
            "reason": "没有适用的 mlruns 清理方案",
        },
    )
    state = event(root, path, {"type": "close"})
    assert state["status"] == "closed_with_pending_cleanup"
    assert state["cleanup"]["reason"] == "没有适用的 mlruns 清理方案"
    with pytest.raises(ValueError, match="closed"):
        event(root, path, {"type": "note", "text": "cannot restart a closed campaign"})


def test_reflection_requires_settled_attempt_and_consecutive_empty_rounds_stop_launch(
    research,
):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    start(root, path)
    reflection = {
        "type": "reflection",
        "round": 1,
        "observations": "未增加证据",
        "interpretation": "机制未获支持",
        "next_hypothesis": "改变同一研究轴",
        "new_evidence": False,
    }
    with pytest.raises(ValueError, match="running|settled|report"):
        event(root, path, reflection)
    finish(root, path)
    register_and_report(root, path)
    event(root, path, reflection)
    plan_variant(root, path, "features/second", 2)
    start(root, path, "features/second")
    finish(root, path, exp_id="features/second")
    register_and_report(root, path, "features/second")
    event(root, path, {**reflection, "round": 2})
    with pytest.raises(ValueError, match="evidence"):
        plan_variant(root, path, "features/third", 3)


def test_state_tampering_is_detected_without_side_effects(research):
    root, path, state = initialized(research)
    broken = copy.deepcopy(state)
    broken["revision"] = 0
    broken["plan"]["budget"]["max_variants"] = 100
    write_json(path, broken)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="checksum"):
        api().inspect_state(path, repo_root=root, check_drift=False)
    assert path.read_bytes() == before


def test_strategy_track_uses_strategy_selection_and_archive_kind(research):
    root, _, plan = research
    archive = "backtest/experiments/strategies/topk-dropout/first"
    write_json(
        root / archive / "metadata.json",
        {
            "exp_id": "strategy/first",
            "archive_path": archive,
            "kind": "strategy",
            "track": "all_a",
        },
    )
    row = {
        "exp_id": "baseline/s5",
        "report_kind": "phase_m_v1_bt_baseline",
        "current_bt": True,
        "model_ref": "baseline/v2",
    }
    with (root / "backtest/experiments/registry.jsonl").open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    plan.update(
        track="all_a_strategy",
        anchor_exp_id="strategy/first",
        archive_path=archive,
        baseline_ref="baseline/s5",
    )
    source = write_json(root / archive / "configs/harness-plan.json", plan)
    state = api().initialize(source, repo_root=root)
    assert state["plan"]["baseline_ref"] == "baseline/s5"
    path = root / state["state_path"]
    config = root / archive / "configs/strategy.yaml"
    config.write_text("topk: 6\n")
    event(
        root,
        path,
        {
            "type": "plan_variant",
            "exp_id": "strategy/first",
            "archive_path": archive,
            "round": 1,
            "hypothesis": "增大持仓降低波动",
            "changed_axis": "features",
            "configs": [str(config.relative_to(root))],
        },
    )
    start(root, path, "strategy/first")
    evaluation = root / archive / "metrics/evaluation.json"
    doc = {
        "seeds": [42, 1000, 2000, 3000, 4000],
        "model_ref": "baseline/wrong",
        "evaluation_mode": "historical_selection_window",
        "ensemble": {
            "full_period": {
                "n_days": 100,
                "annualized_return": 0.2,
                "sharpe_ratio": 0.6,
                "max_drawdown": -0.3,
                "alpha": 0.1,
                "beta": 0.9,
                "benchmark_cumulative_return": 0.4,
            }
        },
    }
    write_json(evaluation, doc)
    result = {
        "type": "result",
        "exp_id": "strategy/first",
        "outcome": "succeeded",
        "evaluation": str(evaluation.relative_to(root)),
        "evidence": [str(evaluation.relative_to(root))],
    }
    with pytest.raises(ValueError, match="model_ref"):
        event(root, path, result)
    doc["model_ref"] = "baseline/v2"
    write_json(evaluation, doc)
    state = event(root, path, result)
    assert state["variants"]["strategy/first"]["attempts"][-1]["outcome"] == "succeeded"


def test_out_of_scope_axis_is_rejected(research):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    archive = "backtest/experiments/models/features/other"
    write_json(
        root / archive / "metadata.json",
        {
            "exp_id": "features/other",
            "archive_path": archive,
            "kind": "model",
            "track": "all_a",
        },
    )
    config = root / archive / "configs/model.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("feature: unchanged")
    candidate = {
        "type": "plan_variant",
        "exp_id": "features/other",
        "archive_path": archive,
        "round": 1,
        "hypothesis": "wrong axis",
        "changed_axis": "strategy",
        "configs": [str(config.relative_to(root))],
    }
    with pytest.raises(ValueError, match="axis"):
        event(root, path, candidate)


def test_success_requires_five_seed_official_ensemble_not_a_log(research):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    start(root, path)
    with pytest.raises(ValueError, match="evaluation"):
        finish(root, path, "succeeded")
    evaluation = root / research[2]["archive_path"] / "metrics/eval.json"
    doc = {
        "data_version": "2026-07-31",
        "sessions": [
            {"session": f"train_s{seed}", "seed": seed}
            for seed in [42, 1000, 2000, 3000, 4000]
        ],
        "pools": {
            "all": {
                "ensemble": {
                    "head": {
                        "3": {
                            "5": {
                                "net_ann": 0.2,
                                "net_ann_vol": 0.5,
                                "net_sharpe": 0.4,
                                "ann": 0.25,
                                "turnover": 0.1,
                                "n_days": 100,
                            }
                        }
                    },
                    "h5": {"rank_ic_mean": 0.05},
                }
            }
        },
    }
    write_json(evaluation, doc)
    data = {
        "type": "result",
        "exp_id": "features/first",
        "outcome": "succeeded",
        "evaluation": str(evaluation.relative_to(root)),
        "evidence": [str(evaluation.relative_to(root))],
    }
    incomplete = copy.deepcopy(doc)
    incomplete["sessions"] = incomplete["sessions"][:2]
    write_json(evaluation, incomplete)
    with pytest.raises(ValueError, match="five|seed"):
        event(root, path, data)
    invalid = copy.deepcopy(doc)
    invalid["pools"]["all"]["ensemble"]["head"]["3"]["5"]["net_sharpe"] = None
    write_json(evaluation, invalid)
    with pytest.raises(ValueError, match="finite"):
        event(root, path, data)
    write_json(evaluation, doc)
    state = event(root, path, data)
    assert state["variants"]["features/first"]["attempts"][-1]["outcome"] == "succeeded"
    state = register_and_report(root, path)
    assert state["variants"]["features/first"]["attempts"][-1]["registered"]


def test_cleanup_rechecks_saved_evidence_before_accepting_a_receipt(research):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    start(root, path)
    finish(root, path)
    register_and_report(root, path)
    event(root, path, {"type": "stop", "reason": "hypotheses_exhausted"})
    (root / "backtest/experiments/models/features/first/reports/detail.html").unlink()
    audit = write_json(
        root / research[2]["archive_path"] / "docs/cleanup-audit.json",
        {"result": "no_changes", "mlruns": "not_applicable", "temporary": "cleaned"},
    )
    with pytest.raises(ValueError, match="missing|evidence"):
        event(
            root,
            path,
            {
                "type": "cleanup",
                "outcome": "complete",
                "audit": str(audit.relative_to(root)),
            },
        )


def test_concurrent_state_lock_refuses_update_without_changing_revision(research):
    import fcntl

    root, path, state = initialized(research)
    before = path.read_bytes()
    lock = path.with_name(f".{path.name}.lock")
    with lock.open("r+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="concurrent"):
            api().append_event(
                path,
                {"type": "stop", "reason": "user_stopped"},
                expected_revision=state["revision"],
                repo_root=root,
            )
    assert path.read_bytes() == before


def test_cli_runs_from_another_directory_and_is_read_only_on_invalid_event(
    research, tmp_path
):
    import subprocess

    root, plan_path, _ = research
    script = Path(api().__file__).resolve()
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo-root",
            str(root),
            "init",
            "--plan",
            str(plan_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    state_path = root / state["state_path"]
    before = state_path.read_bytes()
    bad = write_json(tmp_path / "event.json", {"type": "close"})
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo-root",
            str(root),
            "event",
            "--state",
            str(state_path),
            "--event",
            str(bad),
            "--expected-revision",
            "0",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert "stop reason" in result.stderr
    assert state_path.read_bytes() == before


def test_complete_cleanup_closes_without_erasing_attempts(research):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    start(root, path)
    finish(root, path)
    register_and_report(root, path)
    event(
        root,
        path,
        {
            "type": "reflection",
            "round": 1,
            "observations": "未改善",
            "interpretation": "假设判负",
            "next_hypothesis": "",
            "new_evidence": True,
        },
    )
    event(root, path, {"type": "stop", "reason": "hypotheses_exhausted"})
    audit = write_json(
        root / research[2]["archive_path"] / "docs/cleanup-audit.json",
        {"result": "no_changes", "mlruns": "not_applicable", "temporary": "cleaned"},
    )
    event(
        root,
        path,
        {
            "type": "cleanup",
            "outcome": "complete",
            "audit": str(audit.relative_to(root)),
        },
    )
    state = event(root, path, {"type": "close"})
    assert state["status"] == "closed"
    assert len(state["variants"]["features/first"]["attempts"]) == 1
    assert state["stop"]["reason"] == "hypotheses_exhausted"


def test_campaign_cannot_be_reinitialized_under_another_anchor_to_reset_budget(
    research,
):
    root, _, _ = initialized(research)
    plan = copy.deepcopy(research[2])
    archive = "backtest/experiments/models/features/another"
    plan.update(anchor_exp_id="features/another", archive_path=archive)
    write_json(
        root / archive / "metadata.json",
        {
            "exp_id": "features/another",
            "archive_path": archive,
            "kind": "model",
            "track": "all_a",
        },
    )
    source = write_json(root / archive / "configs/harness-plan.json", plan)
    with pytest.raises(ValueError, match="campaign.*exists"):
        api().initialize(source, repo_root=root)
    assert not (source.parent.parent / "docs/harness-state.json").exists()


@pytest.mark.parametrize(
    "payload",
    [{"type": []}, {"type": "start", "exp_id": []}, {"type": "stop", "reason": []}],
)
def test_malformed_event_is_a_validation_error_and_keeps_state(research, payload):
    root, path, state = initialized(research)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        api().append_event(
            path, payload, expected_revision=state["revision"], repo_root=root
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("changed", ["cleanup_audit", "hub_link", "registry_event"])
def test_closure_revalidates_latest_receipts(research, changed):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    start(root, path)
    finish(root, path)
    register_and_report(root, path)
    event(root, path, {"type": "stop", "reason": "hypotheses_exhausted"})
    audit = write_json(
        root / research[2]["archive_path"] / "docs/cleanup-audit.json",
        {"result": "no_changes", "mlruns": "not_applicable", "temporary": "cleaned"},
    )
    event(
        root,
        path,
        {
            "type": "cleanup",
            "outcome": "complete",
            "audit": str(audit.relative_to(root)),
        },
    )
    if changed == "cleanup_audit":
        audit.unlink()
    elif changed == "hub_link":
        (root / "backtest/experiments/reports/phase_m_v1_report.html").write_text(
            "<html>stale hub</html>"
        )
    else:
        registry = root / "backtest/experiments/registry.jsonl"
        row = json.loads(registry.read_text().splitlines()[-1])
        row["hypothesis"] = "a conflicting later event"
        with registry.open("a") as stream:
            stream.write(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="missing|link|hypothesis|receipt|changed"):
        event(root, path, {"type": "close"})


def test_blocked_temporary_cleanup_can_be_truthfully_recorded(research):
    root, path, _ = initialized(research)
    event(root, path, {"type": "stop", "reason": "user_stopped"})
    audit = write_json(
        root / research[2]["archive_path"] / "docs/cleanup-audit.json",
        {"result": "blocked", "mlruns": "blocked", "temporary": "blocked"},
    )
    event(
        root,
        path,
        {
            "type": "cleanup",
            "outcome": "blocked",
            "audit": str(audit.relative_to(root)),
            "reason": "临时目录仍被使用，稍后处理",
        },
    )
    state = event(root, path, {"type": "close"})
    assert state["status"] == "closed_with_pending_cleanup"


def test_updated_registration_invalidates_the_previous_report_receipt(research):
    root, path, _ = initialized(research)
    plan_variant(root, path)
    start(root, path)
    finish(root, path)
    register_and_report(root, path)
    registry = root / "backtest/experiments/registry.jsonl"
    row = json.loads(registry.read_text().splitlines()[-1])
    row["note"] = "补充执行失败说明"
    with registry.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    state = event(root, path, {"type": "registered", "exp_id": "features/first"})
    assert "reported" not in state["variants"]["features/first"]["attempts"][-1]
    with pytest.raises(ValueError, match="report"):
        event(
            root,
            path,
            {
                "type": "reflection",
                "round": 1,
                "observations": "故障",
                "interpretation": "无研究结论",
                "new_evidence": False,
            },
        )
