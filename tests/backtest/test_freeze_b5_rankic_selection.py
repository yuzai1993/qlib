from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from backtest.scripts import eval_b5_rankic_valid as valid_cli
from backtest.scripts import eval_frozen_b5_rankic as frozen_cli
from backtest.scripts import freeze_b5_rankic_selection as freeze


SEEDS = [42, 1000, 2000, 3000, 4000]
CANDIDATES = [
    "rankic-es-base",
    "rankic-es-l1low",
    "rankic-es-lr010",
    "rankic-es-leaves128",
]


def _candidate_config(candidate: str, seed: int) -> dict:
    source = (
        Path(__file__).resolve().parents[2]
        / "backtest"
        / "configs"
        / "model-hyperparam"
        / candidate
        / f"mh_{candidate.replace('-', '_')}_s{seed}.yaml"
    )
    return yaml.safe_load(source.read_text(encoding="utf-8"))


def _write_matrix(tmp_path: Path) -> dict[str, list[Path]]:
    paths: dict[str, list[Path]] = {}
    for candidate in CANDIDATES:
        candidate_dir = tmp_path / "configs" / "model-hyperparam" / candidate
        candidate_dir.mkdir(parents=True)
        paths[candidate] = []
        for seed in SEEDS:
            path = candidate_dir / f"mh_{candidate.replace('-', '_')}_s{seed}.yaml"
            path.write_text(
                yaml.safe_dump(_candidate_config(candidate, seed), sort_keys=False),
                encoding="utf-8",
            )
            paths[candidate].append(path)
    return paths


def _write_sessions(
    result_root: Path,
    config_paths: dict[str, list[Path]],
) -> dict[str, list[tuple[str, int]]]:
    result_root.mkdir(parents=True)
    sessions: dict[str, list[tuple[str, int]]] = {}
    for candidate, paths in config_paths.items():
        sessions[candidate] = []
        for seed, config_path in zip(SEEDS, paths, strict=True):
            session = result_root / f"20260731_{candidate.replace('-', '_')}_s{seed}"
            session.mkdir()
            meta = {
                "session_name": session.name,
                "note": f"mh_{candidate.replace('-', '_')}_s{seed}",
                "mode": "train_only",
                "n_runs": 1,
                "config_path": str(config_path.resolve()),
                "market": "csi1000",
                "segments": {
                    "train": ["2016-01-02", "2020-01-10"],
                    "valid": ["2020-01-13", "2021-07-15"],
                    "test": ["2021-07-16", "2026-07-16"],
                },
                "runs": [
                    {
                        "run": 1,
                        "status": "success",
                        "train_experiment_id": f"exp-{candidate}-{seed}",
                        "train_recorder_id": f"rec-{candidate}-{seed}",
                        "backtest_experiment_id": None,
                        "backtest_recorder_id": None,
                    }
                ],
            }
            (session / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            sessions[candidate].append((str(session.resolve()), seed))
    return sessions


def _valid_result(
    candidate: str,
    sessions: list[tuple[str, int]],
    *,
    rank_ic: float,
    rank_icir: float,
    config_path: Path,
) -> dict:
    seeds = {
        str(seed): {
            "n_days": 360,
            "ic_mean": rank_ic - 0.001,
            "icir": rank_icir - 0.01,
            "rank_ic_mean": rank_ic + offset * 0.0001,
            "rank_icir": rank_icir + offset * 0.001,
        }
        for offset, (_, seed) in enumerate(sessions)
    }
    return {
        "generated_at": "2026-07-31T00:00:00",
        "config": str(config_path.resolve()),
        "eval_label": "Ref($close, -2)/Ref($close, -1) - 1",
        "eval_label_role": "fixed_1d",
        "eval_segment_name": "valid",
        "eval_segment": ["2020-01-13", "2021-07-15"],
        "effective_eval_segment": ["2020-01-13", "2021-07-13"],
        "sessions": [
            {"session": session, "seed": seed} for session, seed in sessions
        ],
        "data_version": "2026-07-16",
        "min_count": 20,
        "pools": {
            "csi1000": {
                "seeds": seeds,
                "seed_mean": {
                    "rank_ic_mean": sum(
                        row["rank_ic_mean"] for row in seeds.values()
                    )
                    / 5,
                    "rank_icir": sum(row["rank_icir"] for row in seeds.values())
                    / 5,
                },
            }
        },
        "candidate": candidate,
    }


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    config_paths = _write_matrix(tmp_path)
    result_root = tmp_path / "result"
    sessions = _write_sessions(result_root, config_paths)
    monkeypatch.setattr(freeze, "RESULT_ROOT", result_root)
    results = {
        "rankic-es-base": _valid_result(
            "rankic-es-base",
            sessions["rankic-es-base"],
            rank_ic=0.04,
            rank_icir=0.40,
            config_path=config_paths["rankic-es-base"][0],
        ),
        "rankic-es-l1low": _valid_result(
            "rankic-es-l1low",
            sessions["rankic-es-l1low"],
            rank_ic=0.05,
            rank_icir=0.49,
            config_path=config_paths["rankic-es-l1low"][0],
        ),
        "rankic-es-lr010": _valid_result(
            "rankic-es-lr010",
            sessions["rankic-es-lr010"],
            rank_ic=0.05,
            rank_icir=0.48,
            config_path=config_paths["rankic-es-lr010"][0],
        ),
        "rankic-es-leaves128": _valid_result(
            "rankic-es-leaves128",
            sessions["rankic-es-leaves128"],
            rank_ic=0.03,
            rank_icir=0.35,
            config_path=config_paths["rankic-es-leaves128"][0],
        ),
    }
    artifact_paths = {}
    for candidate, result in results.items():
        path = tmp_path / f"mh_{candidate.replace('-', '_')}_valid_1d.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        artifact_paths[candidate] = path
    return config_paths, sessions, results, artifact_paths


def test_controlled_valid_evaluator_uses_only_safe_fixed_protocol(
    experiment, tmp_path, monkeypatch
):
    config_paths, sessions, _, _ = experiment
    output = tmp_path / "valid.json"
    calls = []
    monkeypatch.setattr(valid_cli.evaluator, "_init_qlib", lambda cfg: calls.append(("init", cfg)))

    def fake_evaluate(cfg, passed_sessions, pools, **kwargs):
        calls.append(("evaluate", cfg, passed_sessions, pools, kwargs))
        return _valid_result(
            "rankic-es-base",
            sessions["rankic-es-base"],
            rank_ic=0.04,
            rank_icir=0.4,
            config_path=config_paths["rankic-es-base"][0],
        )

    monkeypatch.setattr(valid_cli.evaluator, "evaluate", fake_evaluate)
    result = valid_cli.run_valid_evaluation(
        config=str(config_paths["rankic-es-base"][0]),
        sessions=[f"{path}:{seed}" for path, seed in sessions["rankic-es-base"]],
        output=output,
    )

    assert [row[0] for row in calls] == ["init", "evaluate"]
    _, _, passed_sessions, pools, kwargs = calls[1]
    assert passed_sessions == sessions["rankic-es-base"]
    assert pools == ["csi1000"]
    assert kwargs == {
        "segment": "valid",
        "eval_label_expr": "Ref($close, -2)/Ref($close, -1) - 1",
        "eval_label_role": "fixed_1d",
        "eval_end": "2021-07-13",
        "min_count": 20,
    }
    assert result["min_count"] == 20
    assert json.loads(output.read_text(encoding="utf-8")) == result


def test_controlled_valid_rejects_cli_overrides_and_existing_output(
    experiment, tmp_path, monkeypatch
):
    config_paths, sessions, _, _ = experiment
    common = [
        "--config",
        str(config_paths["rankic-es-base"][0]),
        "--sessions",
        *[f"{path}:{seed}" for path, seed in sessions["rankic-es-base"]],
        "--output",
        str(tmp_path / "out.json"),
    ]
    with pytest.raises(SystemExit):
        valid_cli.parse_args([*common, "--eval-end", "2021-07-15"])

    output = tmp_path / "exists.json"
    output.write_text("sentinel", encoding="utf-8")
    called = []
    monkeypatch.setattr(valid_cli.evaluator, "_init_qlib", lambda cfg: called.append("init"))
    with pytest.raises(FileExistsError):
        valid_cli.run_valid_evaluation(
            config=str(config_paths["rankic-es-base"][0]),
            sessions=[f"{path}:{seed}" for path, seed in sessions["rankic-es-base"]],
            output=output,
        )
    assert called == []
    assert output.read_text(encoding="utf-8") == "sentinel"


def test_selection_recomputes_seed_means_and_uses_declared_tie_break(
    experiment,
):
    config_paths, _, results, artifact_paths = experiment
    selected = freeze.select_candidate(results, config_paths, artifact_paths)

    assert selected["selected_candidate"] == "rankic-es-l1low"
    assert selected["selection_metric"] == "csi1000.valid.rank_ic_mean"
    assert selected["tie_breaker"] == ["rank_icir", "candidate_id"]
    assert selected["selected_seeds"] == SEEDS
    assert len(selected["selected_sessions"]) == 5
    assert len(selected["config_hashes"]) == 20
    assert set(selected["valid_result_hashes"]) == set(CANDIDATES)

    tied = copy.deepcopy(results)
    tied["rankic-es-lr010"]["pools"]["csi1000"] = copy.deepcopy(
        tied["rankic-es-l1low"]["pools"]["csi1000"]
    )
    artifact_paths["rankic-es-lr010"].write_text(
        json.dumps(tied["rankic-es-lr010"]), encoding="utf-8"
    )
    selected = freeze.select_candidate(tied, config_paths, artifact_paths)
    assert selected["selected_candidate"] == "rankic-es-l1low"


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda r: r.update(eval_segment_name="test"), "valid"),
        (lambda r: r.update(eval_segment=["2020-01-13", "2021-07-13"]), "official"),
        (
            lambda r: r.update(
                effective_eval_segment=["2020-01-13", "2021-07-15"]
            ),
            "effective",
        ),
        (lambda r: r.update(eval_label="Ref($close, -3)/Ref($close, -1)-1"), "label"),
        (lambda r: r.update(eval_label_role="self"), "label"),
        (lambda r: r.update(min_count=19), "min_count"),
        (
            lambda r: r["pools"].update(csi300=copy.deepcopy(r["pools"]["csi1000"])),
            "pool",
        ),
        (lambda r: r.update(test_segment=["2021-07-16", "2026-07-16"]), "test"),
        (lambda r: r["sessions"].pop(), "seed"),
        (lambda r: r["sessions"].append(copy.deepcopy(r["sessions"][0])), "seed"),
        (lambda r: r["pools"]["csi1000"].pop("seed_mean"), "seed_mean"),
        (
            lambda r: r["pools"]["csi1000"]["seeds"]["42"].update(
                rank_ic_mean=float("nan")
            ),
            "finite",
        ),
        (
            lambda r: r["pools"]["csi1000"]["seeds"]["42"].update(
                rank_icir=float("inf")
            ),
            "finite",
        ),
        (
            lambda r: r["pools"]["csi1000"]["seed_mean"].update(
                rank_ic_mean=999.0
            ),
            "seed_mean",
        ),
    ],
)
def test_selection_fails_closed_on_protocol_drift(
    experiment, mutation, match
):
    config_paths, _, results, artifact_paths = experiment
    bad = copy.deepcopy(results)
    mutation(bad["rankic-es-base"])
    artifact_paths["rankic-es-base"].write_text(
        json.dumps(bad["rankic-es-base"], allow_nan=True), encoding="utf-8"
    )
    with pytest.raises(ValueError, match=match):
        freeze.select_candidate(bad, config_paths, artifact_paths)


def test_selection_rejects_test_artifact_basename(experiment, tmp_path):
    config_paths, _, results, artifact_paths = experiment
    bad_path = tmp_path / "mh_rankic_es_base_test_1d.json"
    bad_path.write_text(json.dumps(results["rankic-es-base"]), encoding="utf-8")
    artifact_paths["rankic-es-base"] = bad_path
    with pytest.raises(ValueError, match="test"):
        freeze.select_candidate(results, config_paths, artifact_paths)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda cfg: cfg["segments"].update(valid=["2020-01-13", "2021-07-13"]),
        lambda cfg: cfg["run"].update(mode="train_backtest"),
        lambda cfg: cfg["data"].update(instruments="csi300"),
        lambda cfg: cfg["model"].update({"class": "DEnsembleModel"}),
        lambda cfg: cfg["model"]["kwargs"].update(seed=999),
        lambda cfg: cfg["model"]["kwargs"].update(lambda_l2=1.0),
    ],
)
def test_selection_rejects_config_drift(experiment, mutate):
    config_paths, _, results, artifact_paths = experiment
    path = config_paths["rankic-es-base"][0]
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(cfg)
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ValueError, match="config"):
        freeze.select_candidate(results, config_paths, artifact_paths)


def test_selection_rejects_session_meta_drift(experiment):
    config_paths, sessions, results, artifact_paths = experiment
    meta_path = Path(sessions["rankic-es-base"][0][0]) / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["runs"][0]["status"] = "failed"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="session"):
        freeze.select_candidate(results, config_paths, artifact_paths)


def test_freeze_manifest_refuses_overwrite(experiment, tmp_path):
    config_paths, _, results, artifact_paths = experiment
    output = tmp_path / "selection.json"
    first = freeze.freeze_selection(
        valid_results=results,
        config_paths=config_paths,
        valid_result_paths=artifact_paths,
        output=output,
    )
    with pytest.raises(FileExistsError):
        freeze.freeze_selection(
            valid_results=results,
            config_paths=config_paths,
            valid_result_paths=artifact_paths,
            output=output,
        )
    assert json.loads(output.read_text(encoding="utf-8")) == first


def test_frozen_evaluator_rehashes_before_qlib_and_calls_exact_test_protocol(
    experiment, tmp_path, monkeypatch
):
    config_paths, sessions, results, artifact_paths = experiment
    manifest_path = tmp_path / "selection.json"
    manifest = freeze.freeze_selection(
        valid_results=results,
        config_paths=config_paths,
        valid_result_paths=artifact_paths,
        output=manifest_path,
    )
    output = tmp_path / "test-result.json"
    calls = []
    monkeypatch.setattr(
        frozen_cli.evaluator,
        "_init_qlib",
        lambda cfg: calls.append(("init", cfg)),
    )

    def fake_evaluate(cfg, passed_sessions, pools, **kwargs):
        calls.append(("evaluate", cfg, passed_sessions, pools, kwargs))
        return {
            "config": cfg["_config_path"],
            "eval_label": "Ref($close, -2)/Ref($close, -1) - 1",
            "eval_label_role": "fixed_1d",
            "eval_segment_name": "test",
            "eval_segment": ["2021-07-16", "2026-07-16"],
            "effective_eval_segment": ["2021-07-16", "2026-07-16"],
            "test_segment": ["2021-07-16", "2026-07-16"],
            "sessions": [
                {"session": path, "seed": seed}
                for path, seed in sessions[manifest["selected_candidate"]]
            ],
            "pools": {
                pool: {
                    "seeds": {
                        str(seed): {
                            "n_days": 1000,
                            "rank_ic_mean": 0.04,
                            "rank_icir": 0.4,
                        }
                        for seed in SEEDS
                    },
                    "seed_mean": {"rank_ic_mean": 0.04, "rank_icir": 0.4},
                }
                for pool in ["csi1000", "csi300", "csi500"]
            },
        }

    monkeypatch.setattr(frozen_cli.evaluator, "evaluate", fake_evaluate)
    result = frozen_cli.run_frozen_evaluation(manifest=manifest_path, output=output)

    assert [row[0] for row in calls] == ["init", "evaluate"]
    _, _, passed_sessions, pools, kwargs = calls[1]
    assert passed_sessions == sessions[manifest["selected_candidate"]]
    assert pools == ["csi1000", "csi300", "csi500"]
    assert kwargs == {
        "segment": "test",
        "eval_label_expr": "Ref($close, -2)/Ref($close, -1) - 1",
        "eval_label_role": "fixed_1d",
        "eval_end": None,
        "min_count": 20,
    }
    assert result["selection_manifest_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    assert result["min_count"] == 20


@pytest.mark.parametrize("tamper", ["config", "valid", "winner"])
def test_frozen_guard_tampering_causes_zero_external_calls(
    experiment, tmp_path, monkeypatch, tamper
):
    config_paths, _, results, artifact_paths = experiment
    manifest_path = tmp_path / "selection.json"
    freeze.freeze_selection(
        valid_results=results,
        config_paths=config_paths,
        valid_result_paths=artifact_paths,
        output=manifest_path,
    )
    if tamper == "config":
        config_paths["rankic-es-base"][0].write_text("tampered", encoding="utf-8")
    elif tamper == "valid":
        artifact_paths["rankic-es-base"].write_text("{}", encoding="utf-8")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["selected_candidate"] = "rankic-es-base"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    calls = []
    monkeypatch.setattr(frozen_cli.evaluator, "_init_qlib", lambda cfg: calls.append("init"))
    monkeypatch.setattr(frozen_cli.evaluator, "evaluate", lambda *a, **k: calls.append("eval"))
    with pytest.raises((ValueError, yaml.YAMLError, json.JSONDecodeError)):
        frozen_cli.run_frozen_evaluation(
            manifest=manifest_path,
            output=tmp_path / "out.json",
        )
    assert calls == []


def test_frozen_cli_rejects_overrides_and_existing_output(
    experiment, tmp_path, monkeypatch
):
    with pytest.raises(SystemExit):
        frozen_cli.parse_args(
            [
                "--manifest",
                "selection.json",
                "--output",
                "out.json",
                "--pools",
                "csi1000",
            ]
        )

    output = tmp_path / "exists.json"
    output.write_text("sentinel", encoding="utf-8")
    calls = []
    monkeypatch.setattr(frozen_cli.evaluator, "_init_qlib", lambda cfg: calls.append("init"))
    with pytest.raises(FileExistsError):
        frozen_cli.run_frozen_evaluation(
            manifest=tmp_path / "missing.json",
            output=output,
        )
    assert calls == []
    assert output.read_text(encoding="utf-8") == "sentinel"
