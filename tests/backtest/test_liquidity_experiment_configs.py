from __future__ import annotations

import json

import pytest
import yaml

SEEDS = [42, 1000, 2000, 3000, 4000]
ARMS = {
    "csi1000-full-v2": None,
    "csi1000-random-third": "random",
    "csi1000-liquidity-high": "high",
    "csi1000-liquidity-mid": "mid",
    "csi1000-liquidity-low": "low",
}


@pytest.fixture
def generated_configs(tmp_path, monkeypatch):
    from backtest.scripts import generate_liquidity_configs as generator

    source_root = tmp_path / "backtest/configs/source"
    config_root = tmp_path / "backtest/configs/generated"
    monkeypatch.setattr(generator, "BACKTEST_ROOT", tmp_path / "backtest")
    monkeypatch.setattr(generator, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(generator, "CONFIG_ROOT", config_root)
    source_root.mkdir(parents=True)
    sources = {}
    for seed in SEEDS:
        source = {
            "run": {"mode": "train_backtest", "note": f"source-s{seed}", "n_runs": 1},
            "data": {"instruments": "synthetic", "handler": {"class": "SyntheticHandler"}},
            "segments": {"train": ["2000-01-01", "2000-01-31"]},
            "model": {"class": "LGBModel", "kwargs": {"seed": seed}},
            "strategy": {"topk": 3, "n_drop": 1},
            "backtest": {"account": 1000, "exchange_kwargs": {"min_cost": 1}},
        }
        sources[seed] = source
        (source_root / f"td_csi1000_lgbm_s{seed}.yaml").write_text(yaml.safe_dump(source))
    written = generator.generate()
    return config_root, sources, written


def _configs(config_root, arm: str) -> list[dict]:
    paths = sorted((config_root / arm).glob("*.yaml"))
    assert len(paths) == 5
    return [yaml.safe_load(path.read_text(encoding="utf-8")) for path in paths]


def test_generator_writes_five_seed_configs_and_expected_sampling_for_each_arm(generated_configs):
    config_root, _, written = generated_configs
    assert len(written) == 25
    for arm, bucket in ARMS.items():
        configs = _configs(config_root, arm)
        assert sorted(cfg["model"]["kwargs"]["seed"] for cfg in configs) == SEEDS
        for cfg in configs:
            assert cfg["run"]["mode"] == "train_only"
            if bucket is None:
                assert "dataset" not in cfg
            else:
                dataset = cfg["dataset"]
                assert dataset["class"] == "LiquiditySegmentDatasetH"
                assert dataset["module_path"] == (
                    "backtest.datasets.liquidity_segment"
                )
                assert dataset["kwargs"] == {
                    "liquidity_bucket": bucket,
                    "n_buckets": 3,
                    "lookback": 20,
                    "lag": 1,
                    "random_salt": "csi1000-liquidity-v1",
                }


def test_generator_preserves_source_fields_outside_mode_note_and_sampling(generated_configs):
    config_root, sources, _ = generated_configs
    for arm in ARMS:
        for cfg in _configs(config_root, arm):
            seed = cfg["model"]["kwargs"]["seed"]
            source = sources[seed]
            for section in ("data", "segments", "model", "strategy", "backtest"):
                assert cfg[section] == source[section]
            assert cfg["run"] == {
                **source["run"],
                "mode": "train_only",
                "note": f"td_{arm.replace('-', '_')}_lgbm_s{seed}",
            }


@pytest.mark.parametrize("trim", [False, True])
def test_generator_resolves_source_and_output_without_recreating_old_directories(tmp_path, monkeypatch, trim):
    from backtest.scripts import generate_liquidity_configs, generate_liquidity_trim_configs

    generator = generate_liquidity_trim_configs if trim else generate_liquidity_configs
    monkeypatch.setattr(generator, "BACKTEST_ROOT", tmp_path / "backtest")
    monkeypatch.setattr(generator, "SEEDS", (42,))
    arm = "drop-low-10pct" if trim else "csi1000-liquidity-high"
    monkeypatch.setattr(generator, "ARMS", {arm: 0.1 if trim else "high"})
    source_root = tmp_path / "backtest/configs/source"
    output_root = tmp_path / "backtest/configs/output"
    monkeypatch.setattr(generator, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(generator, "OUTPUT_ROOT" if trim else "CONFIG_ROOT", output_root)
    source_name = "td_csi1000_full_v2_lgbm_s42.yaml" if trim else "td_csi1000_lgbm_s42.yaml"
    output_name = f"{'lt' if trim else 'td'}_{arm.replace('-', '_')}_lgbm_s42.yaml"
    source = tmp_path / "backtest/experiments/models/source/configs" / source_name
    output = tmp_path / "backtest/experiments/models/output/configs" / output_name
    source.parent.mkdir(parents=True)
    source.write_text("run: {}\n")
    mappings = {
        str((source_root / source_name).relative_to(tmp_path)): str(source.relative_to(tmp_path)),
        str((output_root / arm / output_name).relative_to(tmp_path)): str(output.relative_to(tmp_path)),
    }
    mapping_path = tmp_path / "backtest/experiments/_catalog/path_map.json"
    mapping_path.parent.mkdir(parents=True)
    mapping_path.write_text(json.dumps({"schema_version": 1, "paths": mappings}))

    assert generator.generate() == [output]
    assert yaml.safe_load(output.read_text())["run"]["mode"] == "train_only"
    assert not output_root.exists()
    assert not source_root.exists()
