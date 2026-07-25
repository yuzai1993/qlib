"""Generate the explicit five-arm CSI1000 liquidity experiment configs."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = BACKTEST_ROOT / "configs" / "train-data"
SOURCE_ROOT = CONFIG_ROOT / "csi1000"
SEEDS = (42, 1000, 2000, 3000, 4000)
ARMS = {
    "csi1000-full-v2": None,
    "csi1000-random-third": "random",
    "csi1000-liquidity-high": "high",
    "csi1000-liquidity-mid": "mid",
    "csi1000-liquidity-low": "low",
}


def generate() -> list[Path]:
    written: list[Path] = []
    for arm, bucket in ARMS.items():
        output_dir = CONFIG_ROOT / arm
        output_dir.mkdir(parents=True, exist_ok=True)
        for seed in SEEDS:
            source = SOURCE_ROOT / f"td_csi1000_lgbm_s{seed}.yaml"
            cfg = yaml.safe_load(source.read_text(encoding="utf-8"))
            cfg = copy.deepcopy(cfg)
            note = f"td_{arm.replace('-', '_')}_lgbm_s{seed}"
            cfg["run"]["note"] = note
            if bucket is not None:
                cfg["dataset"] = {
                    "class": "LiquiditySegmentDatasetH",
                    "module_path": "backtest.datasets.liquidity_segment",
                    "kwargs": {
                        "liquidity_bucket": bucket,
                        "n_buckets": 3,
                        "lookback": 20,
                        "lag": 1,
                        "random_salt": "csi1000-liquidity-v1",
                    },
                }
            output = output_dir / f"{note}.yaml"
            header = (
                f"# EXPERIMENT_STANDARD v1.1 | exp: train-data/{arm} | seed {seed}\n"
                f"# /opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py "
                f"--config train-data/{arm}/{output.name}\n"
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
