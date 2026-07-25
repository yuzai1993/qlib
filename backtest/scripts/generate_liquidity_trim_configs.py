"""Generate CSI1000 configs that remove cumulative low-liquidity train tails."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = BACKTEST_ROOT / "configs"
SOURCE_ROOT = CONFIG_ROOT / "train-data" / "csi1000-full-v2"
OUTPUT_ROOT = CONFIG_ROOT / "liquidity-trim"
SEEDS = (42, 1000, 2000, 3000, 4000)
ARMS = {
    "drop-low-10pct": 0.1,
    "drop-low-20pct": 0.2,
    "drop-low-third": 1.0 / 3.0,
}


def generate() -> list[Path]:
    written: list[Path] = []
    for arm, cutoff in ARMS.items():
        output_dir = OUTPUT_ROOT / arm
        output_dir.mkdir(parents=True, exist_ok=True)
        arm_note = arm.replace("-", "_")
        for seed in SEEDS:
            source = SOURCE_ROOT / f"td_csi1000_full_v2_lgbm_s{seed}.yaml"
            cfg = copy.deepcopy(yaml.safe_load(source.read_text(encoding="utf-8")))
            cfg["run"]["mode"] = "train_only"
            note = f"lt_{arm_note}_lgbm_s{seed}"
            cfg["run"]["note"] = note
            cfg["dataset"] = {
                "class": "LiquiditySegmentDatasetH",
                "module_path": "backtest.datasets.liquidity_segment",
                "kwargs": {
                    "min_liquidity_pct": cutoff,
                    "lookback": 20,
                    "lag": 1,
                },
            }
            output = output_dir / f"{note}.yaml"
            header = (
                f"# EXPERIMENT_STANDARD v1.2 | exp: liquidity-trim/{arm} | "
                f"seed {seed}\n"
                f"# /opt/anaconda3/envs/qlib/bin/python "
                f"backtest/scripts/run_backtest.py "
                f"--config liquidity-trim/{arm}/{output.name}\n"
            )
            output.write_text(
                header + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            written.append(output)
    return written


if __name__ == "__main__":
    paths = generate()
    print(f"written {len(paths)} configs")
