from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.scripts.generate_b5_rankic_hyperparams import (  # noqa: E402
    SEEDS,
    VARIANTS,
    generate_configs,
)


B5_TEMPLATE = (
    ROOT
    / "backtest"
    / "configs"
    / "loss-design"
    / "cs-rank-norm"
    / "ls_rank_norm_s42.yaml"
)
B5_SEGMENTS = {
    "train": ["2016-01-02", "2020-01-10"],
    "valid": ["2020-01-13", "2021-07-15"],
    "test": ["2021-07-16", "2026-07-16"],
}
EXPECTED_VARIANTS = {
    "rankic-es-base": {},
    "rankic-es-l1low": {"lambda_l1": 51.425},
    "rankic-es-lr010": {"learning_rate": 0.1},
    "rankic-es-leaves128": {"num_leaves": 128},
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _without_candidate_identity(cfg: dict, override: dict) -> dict:
    normalized = copy.deepcopy(cfg)
    normalized["run"].pop("note")
    normalized["model"]["kwargs"].pop("seed")
    for key in override:
        normalized["model"]["kwargs"].pop(key)
    return normalized


def test_generate_configs_writes_fixed_four_by_five_matrix(tmp_path):
    base_cfg = _load(B5_TEMPLATE)

    paths = generate_configs(base_cfg=base_cfg, output_root=tmp_path)

    assert SEEDS == [42, 1000, 2000, 3000, 4000]
    assert VARIANTS == EXPECTED_VARIANTS
    assert len(paths) == 20
    assert [path.relative_to(tmp_path).as_posix() for path in paths] == [
        f"{variant}/mh_{variant.replace('-', '_')}_s{seed}.yaml"
        for variant in EXPECTED_VARIANTS
        for seed in SEEDS
    ]


def test_generated_configs_preserve_b5_data_and_use_rankic_early_stopping(tmp_path):
    base_cfg = _load(B5_TEMPLATE)
    paths = generate_configs(base_cfg=base_cfg, output_root=tmp_path)

    for path in paths:
        cfg = _load(path)
        assert cfg["run"]["mode"] == "train_only"
        assert cfg["segments"] == B5_SEGMENTS
        assert cfg["data"] == base_cfg["data"]
        assert cfg["dataset"] == base_cfg["dataset"]
        assert cfg["strategy"] == base_cfg["strategy"]
        assert cfg["backtest"] == base_cfg["backtest"]
        assert cfg["model"]["class"] == "RankICEarlyStoppingDEnsembleModel"
        assert cfg["model"]["module_path"] == "backtest.models.rankic_early_stop"
        assert cfg["model"]["kwargs"]["loss"] == "mse"
        assert cfg["model"]["kwargs"]["epochs"] == 200
        assert cfg["model"]["kwargs"]["early_stopping_rounds"] == 20
        assert cfg["model"]["kwargs"]["valid_segment"] == B5_SEGMENTS["valid"]
        assert cfg["model"]["kwargs"]["test_segment"] == B5_SEGMENTS["test"]


def test_each_variant_differs_from_base_only_by_declared_override(tmp_path):
    base_cfg = _load(B5_TEMPLATE)
    paths = generate_configs(base_cfg=base_cfg, output_root=tmp_path)
    configs = {
        (path.parent.name, int(path.stem.rsplit("_s", 1)[1])): _load(path)
        for path in paths
    }

    for seed in SEEDS:
        base = configs[("rankic-es-base", seed)]
        for variant, override in EXPECTED_VARIANTS.items():
            cfg = configs[(variant, seed)]
            assert _without_candidate_identity(
                cfg, override
            ) == _without_candidate_identity(base, override)
            for key, value in override.items():
                assert cfg["model"]["kwargs"][key] == value


def test_rendered_header_is_an_exact_runnable_command(tmp_path):
    paths = generate_configs(
        base_cfg=_load(B5_TEMPLATE),
        output_root=tmp_path,
    )

    path = paths[0]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == [
        "# EXPERIMENT_STANDARD v1.8 | exp: model-hyperparam/rankic-es-base | seed 42",
        "# /opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py --config model-hyperparam/rankic-es-base/mh_rankic_es_base_s42.yaml",
    ]


def test_generation_is_deterministic_and_refuses_different_existing_content(
    tmp_path,
):
    base_cfg = _load(B5_TEMPLATE)
    paths = generate_configs(base_cfg=base_cfg, output_root=tmp_path)
    first_render = {path: path.read_bytes() for path in paths}

    repeated = generate_configs(base_cfg=base_cfg, output_root=tmp_path)

    assert repeated == paths
    assert {path: path.read_bytes() for path in repeated} == first_render

    paths[0].write_text("different: true\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="existing config differs"):
        generate_configs(base_cfg=base_cfg, output_root=tmp_path)
