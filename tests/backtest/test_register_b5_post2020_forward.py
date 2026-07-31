from __future__ import annotations

import copy
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

from backtest.scripts import compare_b5_post2020_forward as comparator
from backtest.scripts import register_b5_post2020_forward as register


SEEDS = [42, 1000, 2000, 3000, 4000]
POOLS = ["csi1000", "csi300", "csi500"]
METRICS = ["ic_mean", "icir", "rank_ic_mean", "rank_icir"]
PROTOCOL_ID = "post2020-forward-v1"
LABEL = "Ref($close, -2)/Ref($close, -1) - 1"
VALID = ["2023-01-03", "2024-06-28"]
EFFECTIVE_VALID = ["2023-01-03", "2024-06-26"]
TEST = ["2024-07-01", "2026-07-16"]
GROUPS = {
    "rankic-winner-stale": {
        "role": "same-window-control",
        "train_segment": ["2016-01-02", "2020-01-10"],
        "effective_h40_train_end": "2019-11-13",
    },
    "rankic-winner-post2020": {
        "role": "treatment",
        "train_segment": ["2016-01-02", "2022-12-30"],
        "effective_h40_train_end": "2022-11-03",
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=True),
        encoding="utf-8",
    )


def _config_path(repo_root: Path, group: str, seed: int) -> Path:
    return (
        repo_root
        / "backtest"
        / "configs"
        / "train-recency"
        / group
        / f"tr_{group.replace('-', '_')}_s{seed}.yaml"
    )


def _write_configs(repo_root: Path) -> dict[str, dict[str, str]]:
    hashes = {}
    for group in GROUPS:
        hashes[group] = {}
        for seed in SEEDS:
            path = _config_path(repo_root, group, seed)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "model:\n"
                "  kwargs:\n"
                f"    seed: {seed}\n"
                f"    protocol_id: {PROTOCOL_ID}\n",
                encoding="utf-8",
            )
            hashes[group][str(seed)] = _sha256(path)
    return hashes


def _manifest(repo_root: Path) -> dict:
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "frozen_before_training": True,
        "frozen_at": "2026-07-31T20:58:00+08:00",
        "hypothesis": "Adding post-2020 samples may improve forward RankIC.",
        "data_version_at_freeze": "2026-07-31",
        "seeds": SEEDS,
        "train_pool": "csi1000",
        "test_pools": POOLS,
        "training_objective": "H40 CSRankNorm MSE",
        "early_stopping_metric": "fixed_next_day_valid_daily_rank_ic",
        "early_stopping_rounds": 20,
        "eval_label": LABEL,
        "eval_min_count": 20,
        "groups": copy.deepcopy(GROUPS),
        "common_valid_segment": VALID,
        "effective_h1_valid_segment": EFFECTIVE_VALID,
        "common_test_segment": TEST,
        "conclusion_policy": {
            "improve": "all three pools strictly improve",
            "regress": "csi1000 does not improve",
            "inconclusive": "csi1000 improves but a transfer pool does not",
        },
        "evaluation_comparable_to_baseline": False,
        "cleanup_retention_eligible": False,
        "config_hashes": _write_configs(repo_root),
    }


def _write_session(repo_root: Path, group: str, seed: int) -> Path:
    path = repo_root / "backtest" / "result" / f"20260731_{group}_s{seed}"
    path.mkdir(parents=True)
    config = _config_path(repo_root, group, seed).resolve()
    train_segment = GROUPS[group]["train_segment"]
    meta = {
        "session_name": path.name,
        "note": f"tr_{group.replace('-', '_')}_s{seed}",
        "mode": "train_only",
        "created_at": "2026-07-31T21:00:00",
        "n_runs": 1,
        "config_path": str(config),
        "market": "csi1000",
        "benchmark": "SH000852",
        "handler": "Alpha158Technical",
        "segments": {
            "train": train_segment,
            "valid": VALID,
            "test": TEST,
        },
        "generate_figures": False,
        "runs": [
            {
                "run": 1,
                "status": "success",
                "train_experiment_name": f"train-{group}-{seed}",
                "train_experiment_id": f"exp-{group}-{seed}",
                "train_recorder_id": f"rec-{group}-{seed}",
                "backtest_experiment_name": None,
                "backtest_experiment_id": None,
                "backtest_recorder_id": None,
            }
        ],
    }
    _write_json(path / "meta.json", meta)
    return path


def _seed_rows(rank_ic: float, *, expanded: bool, pool: str) -> dict:
    centered = [-0.002, -0.001, 0.0, 0.001, 0.002]
    paired = [0.001, -0.002, 0.002, -0.0015, 0.0005]
    rows = {}
    for index, (seed, offset) in enumerate(zip(SEEDS, centered, strict=True)):
        paired_delta = paired[index] if expanded and pool == "csi1000" else 0.0
        value = rank_ic + offset + paired_delta
        rows[str(seed)] = {
            "n_days": 500,
            "ic_mean": value - 0.004,
            "icir": value * 8.0,
            "rank_ic_mean": value,
            "rank_icir": value * 10.0,
            "yearly": {
                "2024": {"n_days": 125, "rank_ic_mean": value - 0.003},
                "2025": {"n_days": 242, "rank_ic_mean": value},
                "2026": {"n_days": 133, "rank_ic_mean": value + 0.003},
            },
        }
    return rows


def _eval_result(
    group: str,
    sessions: list[Path],
    *,
    expanded: bool,
) -> dict:
    bases = {"csi1000": 0.030, "csi300": 0.020, "csi500": 0.010}
    pools = {}
    for pool, base in bases.items():
        rows = _seed_rows(
            base + (0.001 if expanded else 0.0),
            expanded=expanded,
            pool=pool,
        )
        pools[pool] = {
            "seeds": rows,
            "seed_mean": {
                metric: sum(row[metric] for row in rows.values()) / len(rows)
                for metric in METRICS
            },
        }
        pools[pool]["seed_mean"]["rank_ic_mean_std"] = statistics.stdev(
            row["rank_ic_mean"] for row in rows.values()
        )
    return {
        "config": str(_config_path(sessions[0].parents[2], group, 42).resolve()),
        "eval_label": LABEL,
        "eval_label_role": "fixed_1d",
        "eval_segment_name": "test",
        "eval_segment": TEST,
        "effective_eval_segment": TEST,
        "test_segment": TEST,
        "sessions": [
            {"session": str(path.resolve()), "seed": seed}
            for path, seed in zip(sessions, SEEDS, strict=True)
        ],
        "data_version": "2026-07-31",
        "pools": pools,
    }


def _existing_registry_bytes() -> bytes:
    return (
        b'  {"exp_id":"baseline/b5-m","direction":"baseline",'
        b'"phase":"M","baseline_ref":"B5 v1.0",'
        b'"conclusion":"baseline"}  \r\n'
        b"\n"
        b' { "exp_id": "bystander", "note": "\\u4fdd\\u7559" }'
    )


def _setup(tmp_path: Path) -> dict:
    repo_root = tmp_path / "repo"
    manifest = _manifest(repo_root)
    manifest_path = repo_root / "backtest" / "experiments" / "protocol.json"
    _write_json(manifest_path, manifest)

    sessions = {
        group: [_write_session(repo_root, group, seed) for seed in SEEDS]
        for group in GROUPS
    }
    control = _eval_result(
        "rankic-winner-stale",
        sessions["rankic-winner-stale"],
        expanded=False,
    )
    expanded = _eval_result(
        "rankic-winner-post2020",
        sessions["rankic-winner-post2020"],
        expanded=True,
    )
    ic_dir = repo_root / "backtest" / "experiments" / "ic"
    control_path = ic_dir / "control.json"
    expanded_path = ic_dir / "expanded.json"
    comparison_path = ic_dir / "comparison.json"
    _write_json(control_path, control)
    _write_json(expanded_path, expanded)
    comparison = comparator.compare_results(
        manifest_path=manifest_path,
        control_path=control_path,
        expanded_path=expanded_path,
        output_path=comparison_path,
    )
    registry_path = repo_root / "backtest" / "experiments" / "registry.jsonl"
    registry_path.write_bytes(_existing_registry_bytes())
    return {
        "repo_root": repo_root,
        "manifest_path": manifest_path,
        "control_path": control_path,
        "expanded_path": expanded_path,
        "comparison_path": comparison_path,
        "comparison": comparison,
        "sessions": sessions,
        "registry_path": registry_path,
        "report_path": repo_root / "backtest" / "experiments" / "report.html",
    }


def _register(bundle: dict):
    return register.register_results(
        manifest_path=bundle["manifest_path"],
        control_eval_path=bundle["control_path"],
        expanded_eval_path=bundle["expanded_path"],
        comparison_path=bundle["comparison_path"],
        control_sessions=bundle["sessions"]["rankic-winner-stale"],
        expanded_sessions=bundle["sessions"]["rankic-winner-post2020"],
        registry_path=bundle["registry_path"],
        report_path=bundle["report_path"],
        repo_root=bundle["repo_root"],
    )


def _rewrite_comparison(bundle: dict, mutation) -> None:
    comparison = json.loads(bundle["comparison_path"].read_text(encoding="utf-8"))
    mutation(comparison)
    _write_json(bundle["comparison_path"], comparison)


def _refresh_source_sha(bundle: dict, source: str) -> None:
    paths = {
        "manifest": bundle["manifest_path"],
        "control": bundle["control_path"],
        "expanded": bundle["expanded_path"],
    }

    def mutation(comparison):
        comparison["sources"][source]["sha256"] = _sha256(paths[source])

    _rewrite_comparison(bundle, mutation)


def _rewrite_eval_sessions(bundle: dict, target: str, transform) -> None:
    path = bundle[f"{target}_path"]
    result = json.loads(path.read_text(encoding="utf-8"))
    for row in result["sessions"]:
        row["session"] = transform(Path(row["session"]))
    _write_json(path, result)
    _refresh_source_sha(bundle, target)


def test_register_appends_control_then_expanded_and_builds_report(tmp_path):
    bundle = _setup(tmp_path)
    original = bundle["registry_path"].read_bytes()

    control_row, expanded_row = _register(bundle)

    raw = bundle["registry_path"].read_bytes()
    assert raw.startswith(original + b"\n")
    stored = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert [row["exp_id"] for row in stored[-2:]] == [
        "train-recency/rankic-winner-stale",
        "train-recency/rankic-winner-post2020",
    ]
    assert stored[-2:] == [control_row, expanded_row]
    for row, comparison_group in (
        (control_row, "control"),
        (expanded_row, "expanded"),
    ):
        for pool in POOLS:
            assert {
                metric: row["metrics_summary"][pool][metric] for metric in METRICS
            } == bundle["comparison"]["seed_mean"][comparison_group][pool]
    assert control_row["conclusion"] == "control"
    assert expanded_row["direct_control_ref"] == control_row["exp_id"]
    assert expanded_row["metric_deltas"] == bundle["comparison"]["metric_deltas"]
    assert (
        expanded_row["pairwise_csi1000_rankic_vs_control"]
        == bundle["comparison"]["csi1000_same_seed_rankic"]
    )
    assert (
        expanded_row["yearly_rank_ic_delta_vs_control"]
        == bundle["comparison"]["yearly_rankic_delta"]
    )
    assert expanded_row["conclusion"] == bundle["comparison"]["conclusion"]
    assert "2020-01-10" in control_row["hypothesis"]
    assert "2022-12-30" not in control_row["hypothesis"]
    assert "延长" not in control_row["hypothesis"]
    for row in (control_row, expanded_row):
        assert row["baseline_ref"] == "B5 v1.0"
        assert row["evaluation_comparable_to_baseline"] is False
        assert row["cleanup_retention_eligible"] is False
        assert row["seeds"] == SEEDS
        assert len(row["configs"]) == 5
        assert len(row["session_dirs"]) == 5
    for row, result in (
        (control_row, json.loads(bundle["control_path"].read_text(encoding="utf-8"))),
        (expanded_row, json.loads(bundle["expanded_path"].read_text(encoding="utf-8"))),
    ):
        for pool in POOLS:
            expected_std = statistics.stdev(
                result["pools"][pool]["seeds"][str(seed)]["rank_ic_mean"]
                for seed in SEEDS
            )
            assert row["metrics_summary"][pool]["rank_ic_mean_std"] == expected_std
    html = bundle["report_path"].read_text(encoding="utf-8")
    assert control_row["exp_id"] in html
    assert expanded_row["exp_id"] in html


def test_register_rejects_rankic_sample_std_drift(tmp_path):
    bundle = _setup(tmp_path)
    control = json.loads(bundle["control_path"].read_text(encoding="utf-8"))
    control["pools"]["csi1000"]["seed_mean"]["rank_ic_mean_std"] = 999.0
    _write_json(bundle["control_path"], control)
    _refresh_source_sha(bundle, "control")

    with pytest.raises(ValueError, match="rank_ic_mean_std"):
        _register(bundle)


def test_register_resolves_real_eval_basename_sessions_under_result_root(tmp_path):
    bundle = _setup(tmp_path)
    for target in ("control", "expanded"):
        _rewrite_eval_sessions(bundle, target, lambda path: path.name)

    control_row, expanded_row = _register(bundle)

    assert control_row["session_dirs"] == [
        f"backtest/result/20260731_rankic-winner-stale_s{seed}" for seed in SEEDS
    ]
    assert expanded_row["session_dirs"] == [
        f"backtest/result/20260731_rankic-winner-post2020_s{seed}" for seed in SEEDS
    ]


def test_register_keeps_support_for_repo_relative_eval_session_paths(tmp_path):
    bundle = _setup(tmp_path)
    _rewrite_eval_sessions(
        bundle,
        "control",
        lambda path: f"backtest/result/{path.name}",
    )

    control_row, _ = _register(bundle)

    assert len(control_row["session_dirs"]) == 5


@pytest.mark.parametrize(
    "kind", ["missing-basename", "relative-escape", "absolute-outside"]
)
def test_register_rejects_unknown_or_out_of_root_eval_session_paths(tmp_path, kind):
    bundle = _setup(tmp_path)
    if kind == "missing-basename":
        bad_session = "does-not-exist"
    elif kind == "relative-escape":
        outside = bundle["repo_root"].parent / "outside-relative"
        outside.mkdir()
        bad_session = "../outside-relative"
    else:
        outside = tmp_path / "outside-absolute"
        outside.mkdir()
        bad_session = str(outside.resolve())
    path = bundle["control_path"]
    result = json.loads(path.read_text(encoding="utf-8"))
    result["sessions"][0]["session"] = bad_session
    _write_json(path, result)
    _refresh_source_sha(bundle, "control")
    original = bundle["registry_path"].read_bytes()

    with pytest.raises(ValueError, match="eval session path"):
        _register(bundle)

    assert bundle["registry_path"].read_bytes() == original


@pytest.mark.parametrize("kind", ["nested-symlink", "outside-symlink"])
def test_register_rejects_symlink_aliases_to_valid_sessions(tmp_path, kind):
    bundle = _setup(tmp_path)
    target = bundle["sessions"]["rankic-winner-stale"][0].resolve()
    if kind == "nested-symlink":
        nested = bundle["repo_root"] / "backtest" / "result" / "nested"
        nested.mkdir()
        alias = nested / "alias"
        alias.symlink_to(target, target_is_directory=True)
        raw_session = "backtest/result/nested/alias"
    else:
        alias = tmp_path / "outside-alias"
        alias.symlink_to(target, target_is_directory=True)
        raw_session = str(alias)
    path = bundle["control_path"]
    result = json.loads(path.read_text(encoding="utf-8"))
    result["sessions"][0]["session"] = raw_session
    _write_json(path, result)
    _refresh_source_sha(bundle, "control")
    original = bundle["registry_path"].read_bytes()

    with pytest.raises(ValueError, match="eval session path"):
        _register(bundle)

    assert bundle["registry_path"].read_bytes() == original


@pytest.mark.parametrize(
    "duplicate",
    [
        "train-recency/rankic-winner-stale",
        "train-recency/rankic-winner-post2020",
    ],
)
def test_register_rejects_either_duplicate_without_touching_registry(
    tmp_path, duplicate
):
    bundle = _setup(tmp_path)
    original = bundle["registry_path"].read_bytes() + (
        b"\n" + json.dumps({"exp_id": duplicate}).encode() + b"\n"
    )
    bundle["registry_path"].write_bytes(original)

    with pytest.raises(ValueError, match="already exists"):
        _register(bundle)

    assert bundle["registry_path"].read_bytes() == original
    assert not bundle["report_path"].exists()


def test_register_requires_the_single_b5_registry_anchor(tmp_path):
    bundle = _setup(tmp_path)
    original = b'{"exp_id":"bystander"}\n'
    bundle["registry_path"].write_bytes(original)

    with pytest.raises(ValueError, match="B5 registry anchor"):
        _register(bundle)

    assert bundle["registry_path"].read_bytes() == original


@pytest.mark.parametrize("source", ["manifest", "control", "expanded"])
def test_register_rechecks_all_three_comparison_source_hashes(tmp_path, source):
    bundle = _setup(tmp_path)
    original = bundle["registry_path"].read_bytes()
    _rewrite_comparison(
        bundle,
        lambda comparison: comparison["sources"][source].update(sha256="0" * 64),
    )

    with pytest.raises(ValueError, match="source SHA-256"):
        _register(bundle)

    assert bundle["registry_path"].read_bytes() == original


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda manifest: manifest.update(frozen_before_training=False), "frozen"),
        (
            lambda manifest: manifest.update(evaluation_comparable_to_baseline=True),
            "evaluation_comparable",
        ),
        (
            lambda manifest: manifest.update(cleanup_retention_eligible=True),
            "cleanup_retention",
        ),
        (lambda manifest: manifest["seeds"].pop(), "five seeds"),
        (
            lambda manifest: manifest["groups"]["rankic-winner-stale"].update(
                train_segment=["2016-01-02", "2020-01-09"]
            ),
            "train segment",
        ),
        (
            lambda manifest: manifest.update(
                common_valid_segment=["2023-01-04", "2024-06-28"]
            ),
            "valid segment",
        ),
    ],
)
def test_register_rejects_manifest_protocol_date_or_flag_drift(
    tmp_path, mutation, match
):
    bundle = _setup(tmp_path)
    original = bundle["registry_path"].read_bytes()
    manifest = json.loads(bundle["manifest_path"].read_text(encoding="utf-8"))
    mutation(manifest)
    _write_json(bundle["manifest_path"], manifest)
    _refresh_source_sha(bundle, "manifest")

    with pytest.raises(ValueError, match=match):
        _register(bundle)

    assert bundle["registry_path"].read_bytes() == original


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda meta: meta.update(mode="train_backtest"), "train_only"),
        (lambda meta: meta["runs"][0].update(status="failed"), "success"),
        (lambda meta: meta.update(config_path="/tmp/wrong.yaml"), "config path"),
        (
            lambda meta: meta["segments"].update(valid=["2023-01-04", "2024-06-28"]),
            "valid segment",
        ),
        (
            lambda meta: meta["runs"][0].update(train_recorder_id=None),
            "success provenance",
        ),
    ],
)
def test_register_rejects_session_meta_config_or_success_drift(
    tmp_path, mutation, match
):
    bundle = _setup(tmp_path)
    original = bundle["registry_path"].read_bytes()
    meta_path = bundle["sessions"]["rankic-winner-stale"][0] / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    mutation(meta)
    _write_json(meta_path, meta)

    with pytest.raises(ValueError, match=match):
        _register(bundle)

    assert bundle["registry_path"].read_bytes() == original


def test_register_rejects_changed_frozen_config_and_session_set(tmp_path):
    bundle = _setup(tmp_path)
    original = bundle["registry_path"].read_bytes()
    config = _config_path(bundle["repo_root"], "rankic-winner-stale", 42)
    config.write_bytes(config.read_bytes() + b"# changed\n")

    with pytest.raises(ValueError, match="config hash"):
        _register(bundle)
    assert bundle["registry_path"].read_bytes() == original

    config.write_bytes(config.read_bytes().removesuffix(b"# changed\n"))
    bundle["sessions"]["rankic-winner-stale"].pop()
    with pytest.raises(ValueError, match="five session"):
        _register(bundle)
    assert bundle["registry_path"].read_bytes() == original


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda comparison: comparison["seed_mean"]["control"]["csi1000"].update(
                rank_ic_mean=999.0
            ),
            "control seed_mean",
        ),
        (
            lambda comparison: comparison["metric_deltas"]["csi300"].update(
                rank_ic_mean=999.0
            ),
            "metric_deltas",
        ),
        (lambda comparison: comparison.update(conclusion="regress"), "conclusion"),
        (
            lambda comparison: comparison["groups"].update(
                control="rankic-winner-post2020"
            ),
            "group",
        ),
    ],
)
def test_register_rejects_comparison_metric_or_conclusion_drift(
    tmp_path, mutation, match
):
    bundle = _setup(tmp_path)
    original = bundle["registry_path"].read_bytes()
    _rewrite_comparison(bundle, mutation)

    with pytest.raises(ValueError, match=match):
        _register(bundle)

    assert bundle["registry_path"].read_bytes() == original


@pytest.mark.parametrize(
    ("target", "mutation", "match"),
    [
        ("expanded", lambda result: result.update(eval_label_role="self"), "fixed_1d"),
        (
            "expanded",
            lambda result: result.update(test_segment=["2024-07-01", "2026-07-15"]),
            "test segment",
        ),
        ("control", lambda result: result["sessions"].pop(), "five seeds"),
    ],
)
def test_register_rejects_eval_protocol_or_session_drift(
    tmp_path, target, mutation, match
):
    bundle = _setup(tmp_path)
    original = bundle["registry_path"].read_bytes()
    path = bundle[f"{target}_path"]
    result = json.loads(path.read_text(encoding="utf-8"))
    mutation(result)
    _write_json(path, result)
    _refresh_source_sha(bundle, target)

    with pytest.raises(ValueError, match=match):
        _register(bundle)

    assert bundle["registry_path"].read_bytes() == original


def test_cli_registers_only_the_supplied_temporary_registry(tmp_path, monkeypatch):
    bundle = _setup(tmp_path)
    untouched_registry = tmp_path / "must-not-touch.jsonl"
    untouched_registry.write_bytes(b"sentinel")
    monkeypatch.setattr(register, "DEFAULT_REGISTRY", untouched_registry)
    argv = [
        "--manifest",
        str(bundle["manifest_path"]),
        "--control-eval",
        str(bundle["control_path"]),
        "--expanded-eval",
        str(bundle["expanded_path"]),
        "--comparison",
        str(bundle["comparison_path"]),
        "--control-sessions",
        *map(str, bundle["sessions"]["rankic-winner-stale"]),
        "--expanded-sessions",
        *map(str, bundle["sessions"]["rankic-winner-post2020"]),
        "--registry",
        str(bundle["registry_path"]),
        "--report-output",
        str(bundle["report_path"]),
        "--repo-root",
        str(bundle["repo_root"]),
    ]

    register.main(argv)

    assert untouched_registry.read_bytes() == b"sentinel"
    assert bundle["report_path"].is_file()
    stored = [
        json.loads(line)
        for line in bundle["registry_path"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["exp_id"] for row in stored[-2:]] == [
        "train-recency/rankic-winner-stale",
        "train-recency/rankic-winner-post2020",
    ]


def test_cli_entrypoint_can_load_from_outside_the_repository(tmp_path):
    script = Path(register.__file__).resolve()

    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--control-sessions" in completed.stdout
