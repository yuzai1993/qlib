"""Generate train-only configs from a frozen label-horizon manifest."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Optional, Sequence

import yaml

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = (
    BACKTEST_ROOT
    / "configs"
    / "train-data"
    / "csi1000-full-v2"
    / "td_csi1000_full_v2_lgbm_s42.yaml"
)
DEFAULT_MANIFEST = BACKTEST_ROOT / "experiments" / "label_horizon_manifest.json"
DEFAULT_OUTPUT_ROOT = BACKTEST_ROOT / "configs" / "label-design"


def _render_config(cfg: dict, *, exp_id: str, seed: int) -> str:
    command_path = (
        f"{exp_id}/"
        f"ld_{exp_id.rsplit('/', 1)[-1].replace('-', '_')}_lgbm_s{seed}.yaml"
    )
    header = (
        f"# EXPERIMENT_STANDARD v1.4 | exp: {exp_id} | seed {seed}\n"
        "# /opt/anaconda3/envs/qlib/bin/python "
        "backtest/scripts/run_backtest.py "
        f"--config {command_path}\n"
    )
    return header + yaml.safe_dump(
        cfg,
        allow_unicode=True,
        sort_keys=False,
    )


def generate_configs(
    manifest: dict,
    *,
    base_cfg: dict,
    output_root: Path,
    force: bool = False,
) -> list[Path]:
    output_root = Path(output_root)
    paths: list[Path] = []
    for candidate in manifest["candidates"]:
        variant = str(candidate["variant"])
        exp_id = str(candidate["exp_id"])
        horizon = int(candidate["label_horizon"])
        label = str(candidate["label"])
        variant_dir = output_root / variant
        variant_dir.mkdir(parents=True, exist_ok=True)

        for seed_value in manifest["seeds"]:
            seed = int(seed_value)
            cfg = copy.deepcopy(base_cfg)
            note = f"ld_{variant.replace('-', '_')}_lgbm_s{seed}"
            cfg["run"]["mode"] = "train_only"
            cfg["run"]["note"] = note
            cfg["model"]["kwargs"]["seed"] = seed
            cfg["data"]["handler"]["label"] = [[label], ["LABEL0"]]
            cfg["dataset"] = {
                "class": "PurgedHorizonDataset",
                "module_path": "backtest.label_design.dataset",
                "kwargs": {"label_horizon": horizon},
            }
            path = variant_dir / f"{note}.yaml"
            rendered = _render_config(cfg, exp_id=exp_id, seed=seed)
            if path.exists() and path.read_text(encoding="utf-8") != rendered:
                if not force:
                    raise FileExistsError(
                        f"existing config differs; use --force: {path}"
                    )
            path.write_text(rendered, encoding="utf-8")
            paths.append(path)
    return paths


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate label-design train-only configs"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--base-config", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    base_cfg = yaml.safe_load(args.base_config.read_text(encoding="utf-8"))
    paths = generate_configs(
        manifest,
        base_cfg=base_cfg,
        output_root=args.output_root,
        force=args.force,
    )
    print(f"written: {len(paths)} configs under {args.output_root}")


if __name__ == "__main__":
    main()
