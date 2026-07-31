from __future__ import annotations

import hashlib
import json
import pickle
import shutil
import statistics
import sys
from copy import deepcopy
from pathlib import Path

import pytest
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import promote_b6_baseline as promotion  # noqa: E402
from backtest.models.rankic_early_stop import (  # noqa: E402
    RankICEarlyStoppingDEnsembleModel,
)


SEEDS = [42, 1000, 2000, 3000, 4000]
POOLS = ["csi1000", "csi300", "csi500"]
CANDIDATES = [
    "rankic-es-base",
    "rankic-es-l1low",
    "rankic-es-lr010",
    "rankic-es-leaves128",
]
WINNER = "rankic-es-lr010"
BEST_ITERATIONS = {
    42: [96, 88, 65],
    1000: [99, 72, 68],
    2000: [73, 94, 116],
    3000: [68, 96, 76],
    4000: [111, 64, 74],
}
METRICS = ("ic_mean", "icir", "rank_ic_mean", "rank_icir")


class Booster:
    def __init__(self, best_iteration: int):
        self.best_iteration = best_iteration


def _fake_model(seed: int) -> RankICEarlyStoppingDEnsembleModel:
    model = RankICEarlyStoppingDEnsembleModel.__new__(
        RankICEarlyStoppingDEnsembleModel
    )
    model.ensemble = [Booster(value) for value in BEST_ITERATIONS[seed]]
    model.params = {
        "objective": "mse",
        "colsample_bytree": 0.8879,
        "learning_rate": 0.1,
        "subsample": 0.8789,
        "lambda_l1": 205.6999,
        "lambda_l2": 580.9768,
        "max_depth": 8,
        "num_leaves": 210,
        "num_threads": 8,
        "seed": seed,
    }
    model.epochs = 200
    model.early_stopping_rounds = 20
    model.num_models = 3
    return model


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _relative(repo: Path, path: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def _metric_pool(base: float, *, n_days: int) -> dict:
    seeds = {}
    for index, seed in enumerate(SEEDS):
        seeds[str(seed)] = {
            "n_days": n_days,
            "ic_mean": base + 0.001 + index * 0.0001,
            "ic_std": 0.15,
            "icir": base + 0.002 + index * 0.0002,
            "rank_ic_mean": base + 0.003 + index * 0.0003,
            "rank_ic_std": 0.20,
            "rank_icir": base + 0.004 + index * 0.0004,
        }
    seed_mean = {
        key: sum(row[key] for row in seeds.values()) / len(SEEDS)
        for key in METRICS
    }
    seed_mean["rank_ic_mean_std"] = statistics.stdev(
        row["rank_ic_mean"] for row in seeds.values()
    )
    return {"seeds": seeds, "seed_mean": seed_mean}


def _evaluation(repo: Path, sessions: list[dict], *, self_label: bool) -> dict:
    result = {
        "generated_at": "2026-07-31T12:00:00",
        "config": str(
            repo
            / "backtest/configs/model-hyperparam/rankic-es-lr010"
            / "mh_rankic_es_lr010_s42.yaml"
        ),
        "eval_label": (
            "Ref($close, -41)/Ref($close, -1)-1"
            if self_label
            else "Ref($close, -2)/Ref($close, -1) - 1"
        ),
        "eval_label_role": "self" if self_label else "fixed_1d",
        "eval_segment_name": "test",
        "eval_segment": ["2021-07-16", "2026-07-16"],
        "effective_eval_segment": ["2021-07-16", "2026-07-16"],
        "test_segment": ["2021-07-16", "2026-07-16"],
        "sessions": (
            [{"session": Path(row["session"]).name, "seed": row["seed"]} for row in sessions]
            if self_label
            else sessions
        ),
        "data_version": "2026-07-31" if self_label else "2026-07-30",
        "pools": {
            pool: _metric_pool(
                (0.08 if self_label else 0.04) + index * 0.01,
                n_days=1181 if self_label else 1211,
            )
            for index, pool in enumerate(POOLS)
        },
    }
    if not self_label:
        result.update(
            {
                "min_count": 20,
                "selected_candidate": WINNER,
            }
        )
    return result


def _copy_configs(repo: Path) -> list[dict]:
    rows = []
    for candidate in CANDIDATES:
        for seed in SEEDS:
            name = f"mh_{candidate.replace('-', '_')}_s{seed}.yaml"
            source = ROOT / "backtest/configs/model-hyperparam" / candidate / name
            target = repo / "backtest/configs/model-hyperparam" / candidate / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            rows.append(
                {
                    "candidate": candidate,
                    "seed": seed,
                    "path": str(target.resolve()),
                    "sha256": _sha256(target),
                }
            )
    return rows


def _winner_sessions(repo: Path, config_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    sessions = []
    provenance = []
    configs = {row["seed"]: Path(row["path"]) for row in config_rows if row["candidate"] == WINNER}
    for seed in SEEDS:
        session = repo / "backtest/result" / f"winner-s{seed}"
        run_dir = session / "run_01"
        run_dir.mkdir(parents=True)
        experiment_id = f"exp-{seed}"
        recorder_id = f"rec-{seed}"
        experiment_name = f"train-winner-s{seed}"
        artifact_root = repo / "mlruns" / experiment_id / recorder_id
        model = artifact_root / "artifacts/trained_model"
        model.parent.mkdir(parents=True)
        model.write_bytes(pickle.dumps(_fake_model(seed)))
        meta = {
            "session_name": session.name,
            "note": f"mh_rankic_es_lr010_s{seed}",
            "mode": "train_only",
            "n_runs": 1,
            "config_path": str(configs[seed].resolve()),
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
                    "train_experiment_name": experiment_name,
                    "train_experiment_id": experiment_id,
                    "train_recorder_id": recorder_id,
                    "backtest_experiment_id": None,
                    "backtest_recorder_id": None,
                }
            ],
        }
        link = {
            "train_experiment_name": experiment_name,
            "train_experiment_id": experiment_id,
            "train_recorder_id": recorder_id,
            "train_artifacts": _relative(repo, artifact_root),
        }
        meta_path = session / "meta.json"
        link_path = run_dir / "mlruns_link.json"
        _write_json(meta_path, meta)
        _write_json(link_path, link)
        sessions.append({"session": str(session.resolve()), "seed": seed})
        provenance.append(
            {
                "session": str(session.resolve()),
                "seed": seed,
                "meta_path": str(meta_path.resolve()),
                "meta_sha256": _sha256(meta_path),
                "mlruns_link_path": str(link_path.resolve()),
                "mlruns_link_sha256": _sha256(link_path),
                "trained_model_path": str(model.resolve()),
                "trained_model_sha256": _sha256(model),
                "train_experiment_name": experiment_name,
                "train_experiment_id": experiment_id,
                "train_recorder_id": recorder_id,
            }
        )
    return sessions, provenance


def _build_source_repo(repo: Path) -> dict[str, Path | str]:
    config_rows = _copy_configs(repo)
    winner_sessions, winner_provenance = _winner_sessions(repo, config_rows)

    candidates = {}
    valid_hashes = {}
    candidate_scores = {
        "rankic-es-base": (0.0489, 0.4369),
        "rankic-es-l1low": (0.0486, 0.4279),
        WINNER: (0.0491, 0.4293),
        "rankic-es-leaves128": (0.0487, 0.4365),
    }
    for candidate in CANDIDATES:
        candidate_sessions = (
            winner_sessions
            if candidate == WINNER
            else [
                {
                    "session": str((repo / "backtest/result" / f"deleted-{candidate}-s{seed}").resolve()),
                    "seed": seed,
                }
                for seed in SEEDS
            ]
        )
        score, score_ir = candidate_scores[candidate]
        valid_pool = _metric_pool(score - 0.0036, n_days=363)
        for index, seed in enumerate(SEEDS):
            valid_pool["seeds"][str(seed)]["rank_ic_mean"] = score + (index - 2) * 0.0001
            valid_pool["seeds"][str(seed)]["rank_icir"] = score_ir + (index - 2) * 0.0002
        valid_pool["seed_mean"]["rank_ic_mean"] = score
        valid_pool["seed_mean"]["rank_icir"] = score_ir
        valid_pool["seed_mean"]["rank_ic_mean_std"] = statistics.stdev(
            row["rank_ic_mean"] for row in valid_pool["seeds"].values()
        )
        first_config = next(
            row["path"]
            for row in config_rows
            if row["candidate"] == candidate and row["seed"] == SEEDS[0]
        )
        valid = {
            "candidate": candidate,
            "config": first_config,
            "eval_label": "Ref($close, -2)/Ref($close, -1) - 1",
            "eval_label_role": "fixed_1d",
            "eval_segment_name": "valid",
            "eval_segment": ["2020-01-13", "2021-07-15"],
            "effective_eval_segment": ["2020-01-13", "2021-07-13"],
            "sessions": candidate_sessions,
            "data_version": "2026-07-30",
            "min_count": 20,
            "pools": {"csi1000": valid_pool},
        }
        valid_path = repo / "backtest/experiments/ic" / f"{candidate}-valid.json"
        _write_json(valid_path, valid)
        valid_hashes[candidate] = {
            "path": str(valid_path.resolve()),
            "sha256": _sha256(valid_path),
        }
        candidates[candidate] = {
            "rank_ic_mean": score,
            "rank_icir": score_ir,
            "data_version": "2026-07-30",
            "seeds": SEEDS,
            "sessions": candidate_sessions,
            "session_provenance": (
                winner_provenance
                if candidate == WINNER
                else [
                    {"session": row["session"], "seed": row["seed"]}
                    for row in candidate_sessions
                ]
            ),
        }

    manifest = {
        "schema_version": 1,
        "generated_at": "2026-07-31T08:00:00",
        "frozen_before_test": True,
        "test_metrics_opened": False,
        "selection_segment": "valid",
        "selection_metric": "csi1000.valid.rank_ic_mean",
        "tie_breaker": ["rank_icir", "candidate_id"],
        "eval_label": "Ref($close, -2)/Ref($close, -1) - 1",
        "eval_label_role": "fixed_1d",
        "min_count": 20,
        "data_version": "2026-07-30",
        "official_valid_segment": ["2020-01-13", "2021-07-15"],
        "effective_valid_segment": ["2020-01-13", "2021-07-13"],
        "seeds": SEEDS,
        "candidate_order": [WINNER, "rankic-es-base", "rankic-es-leaves128", "rankic-es-l1low"],
        "candidates": candidates,
        "selected_candidate": WINNER,
        "selected_seeds": SEEDS,
        "selected_sessions": winner_sessions,
        "valid_result_hashes": valid_hashes,
        "config_hashes": config_rows,
    }
    manifest_path = repo / "backtest/experiments/b5_rankic_hyperparam_selection.json"
    _write_json(manifest_path, manifest)

    formal_path = repo / "backtest/experiments/ic/mh_valid_rankic_selected_test_1d.json"
    self_path = repo / "backtest/experiments/ic/mh_valid_rankic_selected_test_self.json"
    formal_result = _evaluation(repo, winner_sessions, self_label=False)
    formal_result["selection_manifest_sha256"] = _sha256(manifest_path)
    _write_json(formal_path, formal_result)
    _write_json(self_path, _evaluation(repo, winner_sessions, self_label=True))

    b5 = {
        "exp_id": "baseline/b5-m",
        "direction": "baseline",
        "phase": "M",
        "baseline_ref": "B5 v1.0",
        "conclusion": "baseline",
        "seeds": SEEDS,
        "test_pools": POOLS,
        "data_version": "2026-07-30",
        "metrics_summary": {pool: _metric_pool(0.02, n_days=1211)["seed_mean"] for pool in POOLS},
    }
    source = {
        "exp_id": "model-hyperparam/valid-rankic-search-v1",
        "direction": "model-hyperparam",
        "phase": "M",
        "baseline_ref": "B5 v1.0",
        "seeds": SEEDS,
        "train_pool": "csi1000",
        "label_kind": "cumulative_return",
        "label_horizon": 40,
        "label": "Ref($close,-41)/Ref($close,-1)-1",
        "purge_trading_days": 41,
        "feature_groups": ["range"],
        "model": "RankICEarlyStoppingDEnsembleModel",
        "training_objective": "H40 CSRankNorm MSE",
        "learn_processors": ["DropnaLabel", "CSRankNorm(label)"],
        "early_stopping_metric": "fixed_next_day_valid_daily_rank_ic",
        "early_stopping_rounds": 20,
        "selection_candidates": CANDIDATES,
        "config_count": 20,
        "configs": [_relative(repo, Path(row["path"])) for row in config_rows],
        "config_hashes": [
            {**row, "path": _relative(repo, Path(row["path"]))} for row in config_rows
        ],
        "variant_overrides": {
            "rankic-es-base": {},
            "rankic-es-l1low": {"lambda_l1": 51.425},
            WINNER: {"learning_rate": 0.1},
            "rankic-es-leaves128": {"num_leaves": 128},
        },
        "selection_segment": "valid",
        "selection_official_segment": ["2020-01-13", "2021-07-15"],
        "selection_effective_segment": ["2020-01-13", "2021-07-13"],
        "selection_label": "Ref($close, -2)/Ref($close, -1) - 1",
        "selection_label_role": "fixed_1d",
        "selection_min_count": 20,
        "selection_metric": "csi1000.valid.rank_ic_mean",
        "selection_tie_breaker": ["rank_icir", "candidate_id"],
        "selected_candidate": WINNER,
        "winner_result_dirs": [_relative(repo, Path(row["session"])) for row in winner_sessions],
        "selection_manifest": _relative(repo, manifest_path),
        "selection_manifest_sha256": _sha256(manifest_path),
        "test_result": _relative(repo, formal_path),
        "test_result_sha256": _sha256(formal_path),
        "primary_test_pool": "csi1000",
        "test_pools": POOLS,
        "test_segment": ["2021-07-16", "2026-07-16"],
        "test_label": "Ref($close, -2)/Ref($close, -1) - 1",
        "test_label_role": "fixed_1d",
        "test_min_count": 20,
        "test_policy": "freeze_valid_winner_then_test_once",
        "data_version": "2026-07-30",
        "metrics_summary": {
            pool: deepcopy(formal_result["pools"][pool]["seed_mean"])
            for pool in POOLS
        },
        "metrics_by_eval_label": {
            "eval_1d": {
                pool: deepcopy(formal_result["pools"][pool]["seed_mean"])
                for pool in POOLS
            }
        },
        "conclusion": "improve",
    }
    registry = repo / "backtest/experiments/registry.jsonl"
    registry.parent.mkdir(parents=True, exist_ok=True)
    original_registry = (
        b' {"legacy":"bytes-preserved"} \r\n'
        + json.dumps(b5, ensure_ascii=False).encode("utf-8")
        + b"\n"
        + json.dumps(source, ensure_ascii=False).encode("utf-8")
        + b"\n"
    )
    registry.write_bytes(original_registry)
    report = repo / "backtest/experiments/report.html"
    report.write_bytes(b"old-report")
    return {
        "repo": repo,
        "registry": registry,
        "manifest": manifest_path,
        "formal": formal_path,
        "self": self_path,
        "freeze": repo / "backtest/experiments/b6_model_freeze.json",
        "report": report,
        "original_registry": original_registry,
    }


@pytest.fixture
def source_repo(tmp_path: Path) -> dict[str, Path | str]:
    return _build_source_repo(tmp_path / "repo")


def _promote(paths: dict[str, Path | str]) -> dict:
    return promotion.promote_b6_baseline(
        repo_root=Path(paths["repo"]),
        registry_path=Path(paths["registry"]),
        selection_manifest_path=Path(paths["manifest"]),
        formal_result_path=Path(paths["formal"]),
        self_result_path=Path(paths["self"]),
        freeze_path=Path(paths["freeze"]),
        report_path=Path(paths["report"]),
        expected_self_sha256=_sha256(Path(paths["self"])),
    )


def test_promotion_appends_one_b6_row_and_freezes_only_verified_winner(source_repo):
    selection_before = Path(source_repo["manifest"]).read_bytes()
    result = _promote(source_repo)

    registry = Path(source_repo["registry"])
    original = source_repo["original_registry"]
    assert registry.read_bytes().startswith(original)
    appended = registry.read_bytes()[len(original) :]
    assert appended.endswith(b"\n") and appended.count(b"\n") == 1
    row = json.loads(appended)
    assert row["exp_id"] == "baseline/b6-m"
    assert row["direction"] == "baseline"
    assert row["phase"] == "M"
    assert row["baseline_ref"] == "B6 v1.0"
    assert row["conclusion"] == "baseline"
    assert row["promoted_from"] == "model-hyperparam/valid-rankic-search-v1"
    assert row["seeds"] == SEEDS
    assert row["test_pools"] == POOLS
    assert row["selection_min_count"] == 20
    assert row["data_version"] == "2026-07-30"
    assert row["self_data_version"] == "2026-07-31"
    assert row["metrics_summary"] == row["metrics_by_eval_label"]["eval_1d"]
    assert row["freeze_manifest_sha256"] == _sha256(Path(source_repo["freeze"]))
    assert row["metrics_by_eval_label"]["eval_self"]["csi1000"]["rank_ic_mean"] == pytest.approx(
        0.08 + 0.003 + 0.0006
    )
    assert len(row["configs"]) == len(row["session_dirs"]) == len(row["models"]) == 5
    assert all(not Path(path).is_absolute() for path in row["configs"] + row["session_dirs"] + row["models"])

    freeze = json.loads(Path(source_repo["freeze"]).read_text(encoding="utf-8"))
    assert freeze == result["freeze_manifest"]
    assert freeze["selected_candidate"] == WINNER
    assert freeze["seeds"] == SEEDS
    assert freeze["test_pools"] == POOLS
    assert len(freeze["artifacts"]) == 5
    assert [item["best_iterations"] for item in freeze["artifacts"]] == [
        BEST_ITERATIONS[seed] for seed in SEEDS
    ]
    assert all(
        not Path(item[key]).is_absolute()
        for item in freeze["artifacts"]
        for key in ("config", "session", "meta", "mlruns_link", "model")
    )
    assert "baseline/b6-m" in Path(source_repo["report"]).read_text(encoding="utf-8")
    assert Path(source_repo["manifest"]).read_bytes() == selection_before

    rows = [json.loads(line) for line in registry.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row.get("exp_id") for row in rows].count("baseline/b6-m") == 1
    historical = next(row for row in rows if row.get("exp_id") == "model-hyperparam/valid-rankic-search-v1")
    assert historical["baseline_ref"] == "B5 v1.0"


def test_selected_only_freeze_verifies_without_loser_sessions_or_selection_sources(source_repo):
    _promote(source_repo)
    manifest = json.loads(Path(source_repo["manifest"]).read_text(encoding="utf-8"))
    for candidate in CANDIDATES:
        if candidate == WINNER:
            continue
        assert all(not Path(row["session"]).exists() for row in manifest["candidates"][candidate]["sessions"])
        Path(manifest["valid_result_hashes"][candidate]["path"]).unlink()
    Path(source_repo["manifest"]).unlink()

    verified = promotion.verify_b6_freeze_manifest(
        Path(source_repo["freeze"]), repo_root=Path(source_repo["repo"])
    )
    assert verified["selected_candidate"] == WINNER
    assert [item["seed"] for item in verified["artifacts"]] == SEEDS


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("selection_source", "path", "/tmp/absolute-selection.json"),
        ("selection_source", "sha256", "not-a-sha256"),
        ("formal_fixed_1d", "eval_label", "wrong-label"),
        ("formal_fixed_1d", "eval_label_role", "self"),
        ("formal_fixed_1d", "data_version", "2026-07-31"),
        ("formal_fixed_1d", "segment", ["2021-07-17", "2026-07-16"]),
        ("formal_fixed_1d", "min_count", 19),
        ("diagnostic_self", "eval_label", "wrong-self-label"),
        ("diagnostic_self", "eval_label_role", "fixed_1d"),
        ("diagnostic_self", "data_version", "2026-07-30"),
        ("diagnostic_self", "segment", ["2021-07-16", "2026-07-15"]),
    ],
)
def test_selected_only_verifier_rejects_embedded_metadata_drift(
    source_repo,
    section,
    field,
    value,
):
    _promote(source_repo)
    freeze = json.loads(Path(source_repo["freeze"]).read_text(encoding="utf-8"))
    if section == "selection_source":
        freeze[section][field] = value
    else:
        freeze["evaluations"][section][field] = value

    with pytest.raises(ValueError):
        promotion.verify_b6_freeze_manifest(
            freeze,
            repo_root=Path(source_repo["repo"]),
        )


@pytest.mark.parametrize(
    "corruption",
    [
        "formal_seed",
        "formal_pool",
        "formal_sample_std",
        "self_role",
        "self_sample_std",
        "selection_winner",
        "config_hash",
        "meta_hash",
        "link_hash",
        "model_hash",
        "best_iterations",
    ],
)
def test_validation_failures_leave_all_outputs_untouched(source_repo, corruption):
    manifest_path = Path(source_repo["manifest"])
    formal_path = Path(source_repo["formal"])
    self_path = Path(source_repo["self"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    self_result = json.loads(self_path.read_text(encoding="utf-8"))

    if corruption == "formal_seed":
        del formal["pools"]["csi1000"]["seeds"]["4000"]
        _write_json(formal_path, formal)
    elif corruption == "formal_pool":
        formal["pools"]["all"] = formal["pools"].pop("csi500")
        _write_json(formal_path, formal)
    elif corruption == "formal_sample_std":
        formal["pools"]["csi300"]["seed_mean"]["rank_ic_mean_std"] += 1
        _write_json(formal_path, formal)
    elif corruption == "self_role":
        self_result["eval_label_role"] = "fixed_1d"
        _write_json(self_path, self_result)
    elif corruption == "self_sample_std":
        self_result["pools"]["csi500"]["seed_mean"]["rank_ic_mean_std"] += 1
        _write_json(self_path, self_result)
    elif corruption == "selection_winner":
        manifest["selected_candidate"] = "rankic-es-base"
        _write_json(manifest_path, manifest)
    elif corruption == "config_hash":
        Path(manifest["config_hashes"][10]["path"]).write_bytes(b"changed-config")
    elif corruption == "meta_hash":
        Path(manifest["candidates"][WINNER]["session_provenance"][0]["meta_path"]).write_bytes(b"changed-meta")
    elif corruption == "link_hash":
        Path(manifest["candidates"][WINNER]["session_provenance"][0]["mlruns_link_path"]).write_bytes(b"changed-link")
    elif corruption == "model_hash":
        Path(manifest["candidates"][WINNER]["session_provenance"][0]["trained_model_path"]).write_bytes(b"changed-model")
    elif corruption == "best_iterations":
        model_path = Path(manifest["candidates"][WINNER]["session_provenance"][0]["trained_model_path"])
        model = pickle.loads(model_path.read_bytes())
        model.ensemble[0].best_iteration = 1
        model_path.write_bytes(pickle.dumps(model))
        manifest["candidates"][WINNER]["session_provenance"][0]["trained_model_sha256"] = _sha256(model_path)
        _write_json(manifest_path, manifest)
        selection_sha = _sha256(manifest_path)
        formal["selection_manifest_sha256"] = selection_sha
        _write_json(formal_path, formal)
        rows = [
            json.loads(line)
            for line in Path(source_repo["registry"]).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        source = next(row for row in rows if row.get("exp_id") == "model-hyperparam/valid-rankic-search-v1")
        source["selection_manifest_sha256"] = selection_sha
        source["test_result_sha256"] = _sha256(formal_path)
        Path(source_repo["registry"]).write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )

    registry_before = Path(source_repo["registry"]).read_bytes()
    report_before = Path(source_repo["report"]).read_bytes()
    with pytest.raises((ValueError, FileNotFoundError, pickle.UnpicklingError)):
        _promote(source_repo)
    assert Path(source_repo["registry"]).read_bytes() == registry_before
    assert Path(source_repo["report"]).read_bytes() == report_before
    assert not Path(source_repo["freeze"]).exists()


def test_duplicate_b6_row_is_rejected_without_touching_outputs(source_repo):
    registry = Path(source_repo["registry"])
    registry.write_bytes(registry.read_bytes() + b'{"exp_id":"baseline/b6-m"}\n')
    before = registry.read_bytes()

    with pytest.raises(ValueError, match="already exists"):
        _promote(source_repo)

    assert registry.read_bytes() == before
    assert Path(source_repo["report"]).read_bytes() == b"old-report"
    assert not Path(source_repo["freeze"]).exists()


def test_report_render_failure_is_atomic(source_repo, monkeypatch):
    registry_before = Path(source_repo["registry"]).read_bytes()
    report_before = Path(source_repo["report"]).read_bytes()

    def fail_render(_rows):
        raise RuntimeError("render failed")

    monkeypatch.setattr(promotion.report_builder, "build_html", fail_render)
    with pytest.raises(RuntimeError, match="render failed"):
        _promote(source_repo)

    assert Path(source_repo["registry"]).read_bytes() == registry_before
    assert Path(source_repo["report"]).read_bytes() == report_before
    assert not Path(source_repo["freeze"]).exists()


def test_registry_change_during_staging_is_not_overwritten(source_repo, monkeypatch):
    registry = Path(source_repo["registry"])
    report = Path(source_repo["report"])
    concurrent = registry.read_bytes() + b'{"concurrent":true}\n'
    original_stage = promotion._stage
    calls = 0

    def racing_stage(path, payload):
        nonlocal calls
        staged = original_stage(path, payload)
        calls += 1
        if calls == 3:
            registry.write_bytes(concurrent)
        return staged

    monkeypatch.setattr(promotion, "_stage", racing_stage)
    with pytest.raises(ValueError, match="changed while staging"):
        _promote(source_repo)

    assert registry.read_bytes() == concurrent
    assert report.read_bytes() == b"old-report"
    assert not Path(source_repo["freeze"]).exists()


def test_freeze_appearing_during_staging_is_never_overwritten(
    source_repo,
    monkeypatch,
):
    freeze = Path(source_repo["freeze"])
    original_stage = promotion._stage
    calls = 0

    def racing_stage(path, payload):
        nonlocal calls
        staged = original_stage(path, payload)
        calls += 1
        if calls == 3:
            freeze.write_bytes(b"concurrent-freeze")
        return staged

    monkeypatch.setattr(promotion, "_stage", racing_stage)
    with pytest.raises(ValueError, match="changed while staging"):
        _promote(source_repo)

    assert freeze.read_bytes() == b"concurrent-freeze"
    assert Path(source_repo["report"]).read_bytes() == b"old-report"
    assert Path(source_repo["registry"]).read_bytes() == source_repo["original_registry"]


def test_replace_failure_rolls_back_all_published_destinations(
    source_repo,
    monkeypatch,
):
    registry = Path(source_repo["registry"])
    report = Path(source_repo["report"])
    original_replace = promotion.os.replace
    failed = False

    def fail_registry_once(source, destination):
        nonlocal failed
        if Path(destination) == registry and not failed:
            failed = True
            raise OSError("injected registry replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(promotion.os, "replace", fail_registry_once)
    with pytest.raises(OSError, match="injected registry"):
        _promote(source_repo)

    assert registry.read_bytes() == source_repo["original_registry"]
    assert report.read_bytes() == b"old-report"
    assert not Path(source_repo["freeze"]).exists()
