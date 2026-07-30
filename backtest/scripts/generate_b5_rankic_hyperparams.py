"""Generate the fixed B5 valid-RankIC early-stopping hyperparameter grid."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Optional, Sequence

import yaml


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_CONFIG = (
    BACKTEST_ROOT / "configs" / "loss-design" / "cs-rank-norm" / "ls_rank_norm_s42.yaml"
)
DEFAULT_OUTPUT_ROOT = BACKTEST_ROOT / "configs" / "model-hyperparam"

SEEDS = [42, 1000, 2000, 3000, 4000]
VARIANTS = {
    "rankic-es-base": {},
    "rankic-es-l1low": {"lambda_l1": 51.425},
    "rankic-es-lr010": {"learning_rate": 0.1},
    "rankic-es-leaves128": {"num_leaves": 128},
}


def _note(variant: str, seed: int) -> str:
    return f"mh_{variant.replace('-', '_')}_s{seed}"


def _render_config(cfg: dict, *, variant: str, seed: int) -> str:
    note = _note(variant, seed)
    exp_id = f"model-hyperparam/{variant}"
    relative_path = f"{exp_id}/{note}.yaml"
    header = (
        f"# EXPERIMENT_STANDARD v1.8 | exp: {exp_id} | seed {seed}\n"
        "# /opt/anaconda3/envs/qlib/bin/python "
        "backtest/scripts/run_backtest.py "
        f"--config {relative_path}\n"
    )
    return header + yaml.safe_dump(
        cfg,
        allow_unicode=True,
        sort_keys=False,
    )


def generate_configs(
    *,
    base_cfg: dict,
    output_root: Path,
) -> list[Path]:
    """Write the four-candidate, five-seed matrix in stable order."""

    output_root = Path(output_root)
    paths: list[Path] = []
    for variant, override in VARIANTS.items():
        variant_dir = output_root / variant
        variant_dir.mkdir(parents=True, exist_ok=True)
        for seed in SEEDS:
            cfg = copy.deepcopy(base_cfg)
            cfg["run"]["mode"] = "train_only"
            cfg["run"]["note"] = _note(variant, seed)
            cfg["model"]["class"] = "RankICEarlyStoppingDEnsembleModel"
            cfg["model"]["module_path"] = "backtest.models.rankic_early_stop"
            kwargs = cfg["model"]["kwargs"]
            kwargs["epochs"] = 200
            kwargs["early_stopping_rounds"] = 20
            kwargs["valid_segment"] = copy.deepcopy(cfg["segments"]["valid"])
            kwargs["test_segment"] = copy.deepcopy(cfg["segments"]["test"])
            kwargs["seed"] = seed
            kwargs.update(override)

            path = variant_dir / f"{_note(variant, seed)}.yaml"
            rendered = _render_config(
                cfg,
                variant=variant,
                seed=seed,
            )
            if path.exists() and path.read_text(encoding="utf-8") != rendered:
                raise FileExistsError(f"existing config differs: {path}")
            path.write_text(rendered, encoding="utf-8")
            paths.append(path)
    return paths


def parse_args(
    argv: Optional[Sequence[str]] = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate fixed B5 RankIC early-stopping configs"
    )
    parser.add_argument(
        "--base-config",
        type=Path,
        default=DEFAULT_BASE_CONFIG,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    base_cfg = yaml.safe_load(args.base_config.read_text(encoding="utf-8"))
    paths = generate_configs(
        base_cfg=base_cfg,
        output_root=args.output_root,
    )
    print(f"written: {len(paths)} configs under {args.output_root}")


if __name__ == "__main__":
    main()
