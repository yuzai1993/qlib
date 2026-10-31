from __future__ import annotations

from copy import deepcopy

import yaml

SEEDS = [42, 1000, 2000, 3000, 4000]
ARMS = {
    "drop-low-10pct": 0.1,
    "drop-low-20pct": 0.2,
    "drop-low-third": 1.0 / 3.0,
}


def test_trim_generator_changes_only_mode_note_and_cutoff_for_each_seed(tmp_path, monkeypatch):
    from backtest.scripts import generate_liquidity_trim_configs as generator

    source_root = tmp_path / "backtest/configs/source"
    output_root = tmp_path / "backtest/configs/generated"
    monkeypatch.setattr(generator, "BACKTEST_ROOT", tmp_path / "backtest")
    monkeypatch.setattr(generator, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(generator, "OUTPUT_ROOT", output_root)
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
        (source_root / f"td_csi1000_full_v2_lgbm_s{seed}.yaml").write_text(yaml.safe_dump(source))

    assert len(generator.generate()) == 15

    for arm, cutoff in ARMS.items():
        paths = sorted((output_root / arm).glob("*.yaml"))
        assert len(paths) == 5
        configs = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in paths]
        assert sorted(cfg["model"]["kwargs"]["seed"] for cfg in configs) == SEEDS

        for cfg in configs:
            seed = cfg["model"]["kwargs"]["seed"]
            expected = deepcopy(sources[seed])
            expected["run"].update(
                mode="train_only", note=f"lt_{arm.replace('-', '_')}_lgbm_s{seed}"
            )
            expected["dataset"] = {
                "class": "LiquiditySegmentDatasetH",
                "module_path": "backtest.datasets.liquidity_segment",
                "kwargs": {
                    "min_liquidity_pct": cutoff,
                    "lookback": 20,
                    "lag": 1,
                },
            }
            assert cfg == expected
