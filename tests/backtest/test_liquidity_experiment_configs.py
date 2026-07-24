from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = ROOT / "backtest" / "configs" / "train-data"
SEEDS = [42, 1000, 2000, 3000, 4000]
ARMS = {
    "csi1000-full-v2": None,
    "csi1000-random-third": "random",
    "csi1000-liquidity-high": "high",
    "csi1000-liquidity-mid": "mid",
    "csi1000-liquidity-low": "low",
}


def _configs(arm: str) -> list[dict]:
    paths = sorted((CONFIG_ROOT / arm).glob("*.yaml"))
    assert len(paths) == 5
    return [yaml.safe_load(path.read_text(encoding="utf-8")) for path in paths]


def test_each_arm_has_fixed_five_seeds_and_frozen_model_protocol():
    for arm, bucket in ARMS.items():
        configs = _configs(arm)
        assert sorted(cfg["model"]["kwargs"]["seed"] for cfg in configs) == SEEDS
        for cfg in configs:
            assert cfg["data"]["instruments"] == "csi1000"
            assert cfg["segments"]["train"] == ["2016-01-02", "2020-01-10"]
            assert cfg["segments"]["valid"] == ["2020-01-13", "2021-07-15"]
            assert cfg["segments"]["test"] == ["2021-07-16", "2026-07-16"]
            assert cfg["strategy"]["topk"] == 10
            assert cfg["strategy"]["n_drop"] == 2
            assert cfg["backtest"]["exchange_kwargs"]["open_cost"] == 0.00021
            assert cfg["backtest"]["exchange_kwargs"]["close_cost"] == 0.00071
            if bucket is None:
                assert "dataset" not in cfg
            else:
                dataset = cfg["dataset"]
                assert dataset["class"] == "LiquiditySegmentDatasetH"
                assert dataset["module_path"] == "backtest.sample_dataset"
                assert dataset["kwargs"] == {
                    "liquidity_bucket": bucket,
                    "n_buckets": 3,
                    "lookback": 20,
                    "lag": 1,
                    "random_salt": "csi1000-liquidity-v1",
                }


def test_only_seed_note_and_dataset_bucket_vary_across_arms():
    full_by_seed = {
        cfg["model"]["kwargs"]["seed"]: cfg for cfg in _configs("csi1000-full-v2")
    }
    for arm, bucket in ARMS.items():
        for cfg in _configs(arm):
            seed = cfg["model"]["kwargs"]["seed"]
            full = full_by_seed[seed]
            assert cfg["data"] == full["data"]
            assert cfg["segments"] == full["segments"]
            assert cfg["model"] == full["model"]
            assert cfg["strategy"] == full["strategy"]
            assert cfg["backtest"] == full["backtest"]
            assert cfg["run"]["note"] != full["run"]["note"] or bucket is None

