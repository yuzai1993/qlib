"""Generate the paired post-2020 train-recency experiment configs.

Both groups use the frozen ``rankic-es-lr010`` winner.  The only treatment
difference is the train/handler fit end date; valid and test are shared.
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Optional, Sequence

import yaml


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WINNER_CONFIG = (
    BACKTEST_ROOT
    / "configs"
    / "model-hyperparam"
    / "rankic-es-lr010"
    / "mh_rankic_es_lr010_s42.yaml"
)
DEFAULT_OUTPUT_ROOT = BACKTEST_ROOT / "configs" / "train-recency"

SEEDS = [42, 1000, 2000, 3000, 4000]
GROUPS = {
    "rankic-winner-stale": "2020-01-10",
    "rankic-winner-post2020": "2022-12-30",
}
TRAIN_START = "2016-01-02"
VALID_SEGMENT = ("2023-01-03", "2024-06-28")
TEST_SEGMENT = ("2024-07-01", "2026-07-16")
FORWARD_PROTOCOL_ID = "post2020-forward-v1"

_FROZEN_WINNER_KWARGS = {
    "base_model": "gbm",
    "loss": "mse",
    "num_models": 3,
    "enable_sr": True,
    "enable_fs": True,
    "epochs": 200,
    "early_stopping_rounds": 20,
    "learning_rate": 0.1,
    "colsample_bytree": 0.8879,
    "subsample": 0.8789,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 8,
    "num_leaves": 210,
}


def _note(group: str, seed: int) -> str:
    return f"tr_{group.replace('-', '_')}_s{seed}"


def _validate_winner_config(cfg: dict) -> None:
    model = cfg.get("model") or {}
    if (
        model.get("class") != "RankICEarlyStoppingDEnsembleModel"
        or model.get("module_path") != "backtest.models.rankic_early_stop"
    ):
        raise ValueError("template is not the frozen RankIC early-stop winner")
    kwargs = model.get("kwargs") or {}
    for key, expected in _FROZEN_WINNER_KWARGS.items():
        if kwargs.get(key) != expected:
            raise ValueError(
                f"winner template changed at model.kwargs.{key}: "
                f"expected {expected!r}, got {kwargs.get(key)!r}"
            )
    if (cfg.get("segments") or {}).get("train") != [TRAIN_START, "2020-01-10"]:
        raise ValueError("winner template train segment changed")
    handler = ((cfg.get("data") or {}).get("handler") or {})
    if handler.get("fit_start_time") != TRAIN_START or handler.get("fit_end_time") != "2020-01-10":
        raise ValueError("winner template handler fit segment changed")
    dataset = cfg.get("dataset") or {}
    if (
        dataset.get("class") != "PurgedHorizonDataset"
        or (dataset.get("kwargs") or {}).get("label_horizon") != 40
    ):
        raise ValueError("winner template must retain the purged H40 dataset")


def _render_config(cfg: dict, *, group: str, seed: int) -> str:
    note = _note(group, seed)
    exp_id = f"train-recency/{group}"
    relative_path = f"{exp_id}/{note}.yaml"
    header = (
        f"# EXPERIMENT_STANDARD v1.9 | exp: {exp_id} | seed {seed}\n"
        "# /opt/anaconda3/envs/qlib/bin/python "
        "backtest/scripts/run_backtest.py "
        f"--config {relative_path}\n"
    )
    return header + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)


def generate_configs(*, winner_cfg: dict, output_root: Path) -> list[Path]:
    """Write the two-group, five-seed forward matrix in stable order."""

    _validate_winner_config(winner_cfg)
    output_root = Path(output_root)
    paths: list[Path] = []
    for group, train_end in GROUPS.items():
        group_dir = output_root / group
        group_dir.mkdir(parents=True, exist_ok=True)
        for seed in SEEDS:
            cfg = copy.deepcopy(winner_cfg)
            cfg["run"]["mode"] = "train_only"
            cfg["run"]["note"] = _note(group, seed)
            cfg["segments"] = {
                "train": [TRAIN_START, train_end],
                "valid": list(VALID_SEGMENT),
                "test": list(TEST_SEGMENT),
            }
            handler = cfg["data"]["handler"]
            handler["fit_start_time"] = TRAIN_START
            handler["fit_end_time"] = train_end
            handler["end_time"] = TEST_SEGMENT[1]

            kwargs = cfg["model"]["kwargs"]
            kwargs["seed"] = seed
            kwargs["protocol_id"] = FORWARD_PROTOCOL_ID
            kwargs["valid_segment"] = list(VALID_SEGMENT)
            kwargs["test_segment"] = list(TEST_SEGMENT)

            path = group_dir / f"{_note(group, seed)}.yaml"
            rendered = _render_config(cfg, group=group, seed=seed)
            if path.exists() and path.read_text(encoding="utf-8") != rendered:
                raise FileExistsError(f"existing config differs: {path}")
            path.write_text(rendered, encoding="utf-8")
            paths.append(path)
    return paths


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate paired B5 winner post-2020 forward configs"
    )
    parser.add_argument(
        "--winner-config",
        type=Path,
        default=DEFAULT_WINNER_CONFIG,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    winner_cfg = yaml.safe_load(args.winner_config.read_text(encoding="utf-8"))
    paths = generate_configs(
        winner_cfg=winner_cfg,
        output_root=args.output_root,
    )
    print(f"written: {len(paths)} configs under {args.output_root}")


if __name__ == "__main__":
    main()
