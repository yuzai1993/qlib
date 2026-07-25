from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = ROOT / "backtest" / "configs"
SEEDS = [42, 1000, 2000, 3000, 4000]
ARMS = {
    "drop-low-10pct": 0.1,
    "drop-low-20pct": 0.2,
    "drop-low-third": 1.0 / 3.0,
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_trim_arms_have_five_frozen_b1_configs():
    for arm, cutoff in ARMS.items():
        paths = sorted((CONFIG_ROOT / "liquidity-trim" / arm).glob("*.yaml"))
        assert len(paths) == 5
        configs = [_load(path) for path in paths]
        assert sorted(cfg["model"]["kwargs"]["seed"] for cfg in configs) == SEEDS

        for cfg in configs:
            seed = cfg["model"]["kwargs"]["seed"]
            source = _load(
                CONFIG_ROOT
                / "train-data"
                / "csi1000-full-v2"
                / f"td_csi1000_full_v2_lgbm_s{seed}.yaml"
            )
            assert cfg["data"] == source["data"]
            assert cfg["segments"] == source["segments"]
            assert cfg["model"] == source["model"]
            assert cfg["strategy"] == source["strategy"]
            assert cfg["backtest"] == source["backtest"]
            assert cfg["run"]["note"] != source["run"]["note"]
            assert cfg["dataset"] == {
                "class": "LiquiditySegmentDatasetH",
                "module_path": "backtest.datasets.liquidity_segment",
                "kwargs": {
                    "min_liquidity_pct": cutoff,
                    "lookback": 20,
                    "lag": 1,
                },
            }
