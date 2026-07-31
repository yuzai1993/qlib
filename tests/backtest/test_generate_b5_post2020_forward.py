from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.scripts.generate_b5_post2020_forward import (  # noqa: E402
    FORWARD_PROTOCOL_ID,
    GROUPS,
    SEEDS,
    TEST_SEGMENT,
    VALID_SEGMENT,
    generate_configs,
)


WINNER_TEMPLATE = (
    ROOT
    / "backtest"
    / "configs"
    / "model-hyperparam"
    / "rankic-es-lr010"
    / "mh_rankic_es_lr010_s42.yaml"
)
REGISTRY = ROOT / "backtest" / "experiments" / "registry.jsonl"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _without_identity_and_cutoff(cfg: dict) -> dict:
    normalized = copy.deepcopy(cfg)
    normalized["run"].pop("note")
    normalized["model"]["kwargs"].pop("seed")
    normalized["segments"]["train"][1] = "<TRAIN_END>"
    normalized["data"]["handler"]["fit_end_time"] = "<TRAIN_END>"
    return normalized


def test_generate_configs_writes_fixed_two_by_five_matrix(tmp_path):
    paths = generate_configs(
        winner_cfg=_load(WINNER_TEMPLATE),
        output_root=tmp_path,
    )

    assert SEEDS == [42, 1000, 2000, 3000, 4000]
    assert GROUPS == {
        "rankic-winner-stale": "2020-01-10",
        "rankic-winner-post2020": "2022-12-30",
    }
    assert len(paths) == 10
    assert [path.relative_to(tmp_path).as_posix() for path in paths] == [
        f"{group}/tr_{group.replace('-', '_')}_s{seed}.yaml"
        for group in GROUPS
        for seed in SEEDS
    ]


def test_generated_configs_freeze_winner_and_share_forward_holdout(tmp_path):
    winner = _load(WINNER_TEMPLATE)
    paths = generate_configs(winner_cfg=winner, output_root=tmp_path)

    for path in paths:
        cfg = _load(path)
        group = path.parent.name
        train_end = GROUPS[group]

        assert cfg["run"]["mode"] == "train_only"
        assert cfg["segments"] == {
            "train": ["2016-01-02", train_end],
            "valid": list(VALID_SEGMENT),
            "test": list(TEST_SEGMENT),
        }
        assert cfg["data"]["handler"]["fit_start_time"] == "2016-01-02"
        assert cfg["data"]["handler"]["fit_end_time"] == train_end
        assert cfg["data"]["handler"]["end_time"] == TEST_SEGMENT[1]
        assert cfg["model"]["class"] == "RankICEarlyStoppingDEnsembleModel"
        assert cfg["model"]["module_path"] == "backtest.models.rankic_early_stop"
        assert cfg["model"]["kwargs"]["protocol_id"] == FORWARD_PROTOCOL_ID
        assert cfg["model"]["kwargs"]["valid_segment"] == list(VALID_SEGMENT)
        assert cfg["model"]["kwargs"]["test_segment"] == list(TEST_SEGMENT)

        for key in (
            "base_model",
            "loss",
            "num_models",
            "enable_sr",
            "enable_fs",
            "epochs",
            "early_stopping_rounds",
            "learning_rate",
            "lambda_l1",
            "lambda_l2",
            "max_depth",
            "num_leaves",
        ):
            assert cfg["model"]["kwargs"][key] == winner["model"]["kwargs"][key]
        assert cfg["data"]["handler"]["label"] == winner["data"]["handler"]["label"]
        assert cfg["data"]["handler"]["learn_processors"] == winner["data"]["handler"]["learn_processors"]
        assert cfg["dataset"] == winner["dataset"]
        assert cfg["strategy"] == winner["strategy"]
        assert cfg["backtest"] == winner["backtest"]


def test_groups_differ_only_by_identity_seed_and_train_cutoff(tmp_path):
    paths = generate_configs(
        winner_cfg=_load(WINNER_TEMPLATE),
        output_root=tmp_path,
    )
    configs = {
        (path.parent.name, int(path.stem.rsplit("_s", 1)[1])): _load(path)
        for path in paths
    }

    for seed in SEEDS:
        stale = configs[("rankic-winner-stale", seed)]
        expanded = configs[("rankic-winner-post2020", seed)]
        assert _without_identity_and_cutoff(stale) == _without_identity_and_cutoff(expanded)


def test_rendered_header_uses_v19_and_exact_runnable_command(tmp_path):
    path = generate_configs(
        winner_cfg=_load(WINNER_TEMPLATE),
        output_root=tmp_path,
    )[0]

    assert path.read_text(encoding="utf-8").splitlines()[:2] == [
        "# EXPERIMENT_STANDARD v1.9 | exp: train-recency/rankic-winner-stale | seed 42",
        "# /opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py --config train-recency/rankic-winner-stale/tr_rankic_winner_stale_s42.yaml",
    ]


def test_generation_is_deterministic_and_refuses_changed_existing_file(tmp_path):
    winner = _load(WINNER_TEMPLATE)
    paths = generate_configs(winner_cfg=winner, output_root=tmp_path)
    first = {path: path.read_bytes() for path in paths}

    repeated = generate_configs(winner_cfg=winner, output_root=tmp_path)
    assert repeated == paths
    assert {path: path.read_bytes() for path in repeated} == first

    paths[0].write_text("different: true\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="existing config differs"):
        generate_configs(winner_cfg=winner, output_root=tmp_path)


def test_registry_retains_post2020_configs_and_metrics_without_protocol_manifest():
    rows = [json.loads(line) for line in REGISTRY.read_text(encoding="utf-8").splitlines()]
    by_id = {row["exp_id"]: row for row in rows}

    for group in GROUPS:
        row = by_id[f"train-recency/{group}"]
        assert "protocol_manifest" not in row
        assert "protocol_manifest_sha256" not in row
        assert row["evaluation_comparable_to_baseline"] is False
        assert row["cleanup_retention_eligible"] is False
        assert len(row["configs"]) == 5
        assert row["metrics_summary"]
        assert _sha256(ROOT / row["eval_result"]) == row["eval_result_sha256"]
        for artifact in row["config_hashes"]:
            assert _sha256(ROOT / artifact["path"]) == artifact["sha256"]

    assert not (ROOT / "backtest/experiments/b5_post2020_forward_protocol.json").exists()
