"""Generate model-arch experiment configs (EXPERIMENT_STANDARD v1.6, baseline B3 v1.0).

Variants keep the exact B3-M data/feature/label/dataset settings and only swap
the model block:
  - model-arch/xgboost          qlib official Alpha158 benchmark params
  - model-arch/catboost         qlib official params (Poisson->Bernoulli on CPU)
  - model-arch/double-ensemble  qlib official recipe, lgb params identical to B3
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = BACKTEST_ROOT / "configs"
BASELINE_CONFIG = CONFIGS_DIR / "feature-b2/range/fb2_range_lgbm_s42.yaml"

SEEDS = [42, 1000, 2000, 3000, 4000]

VARIANTS = {
    "xgboost": {
        "prefix": "ma_xgboost",
        "model": {
            "class": "XGBModel",
            "module_path": "qlib.contrib.model.xgboost",
            "kwargs": {
                "eval_metric": "rmse",
                "colsample_bytree": 0.8879,
                "eta": 0.0421,
                "max_depth": 8,
                "subsample": 0.8789,
                "tree_method": "hist",
                "nthread": 8,
            },
        },
        "seed_key": "seed",
    },
    "catboost": {
        "prefix": "ma_catboost",
        "model": {
            "class": "CatBoostModel",
            "module_path": "qlib.contrib.model.catboost_model",
            "kwargs": {
                "loss": "RMSE",
                "learning_rate": 0.0421,
                "subsample": 0.8789,
                "max_depth": 6,
                "num_leaves": 100,
                "grow_policy": "Lossguide",
                "bootstrap_type": "Bernoulli",
                "thread_count": 8,
            },
        },
        "seed_key": "random_seed",
    },
    "double-ensemble": {
        "prefix": "ma_double_ensemble",
        "model": {
            "class": "DEnsembleModel",
            "module_path": "qlib.contrib.model.double_ensemble",
            "kwargs": {
                "base_model": "gbm",
                "loss": "mse",
                "num_models": 3,
                "enable_sr": True,
                "enable_fs": True,
                "alpha1": 1,
                "alpha2": 1,
                "bins_sr": 10,
                "bins_fs": 5,
                "decay": 0.5,
                "sample_ratios": [0.8, 0.7, 0.6, 0.5, 0.4],
                "sub_weights": [1, 1, 1],
                "epochs": 28,
                "colsample_bytree": 0.8879,
                "learning_rate": 0.2,
                "subsample": 0.8789,
                "lambda_l1": 205.6999,
                "lambda_l2": 580.9768,
                "max_depth": 8,
                "num_leaves": 210,
                "num_threads": 8,
            },
        },
        "seed_key": "seed",
    },
}


def main() -> None:
    base = yaml.safe_load(BASELINE_CONFIG.read_text(encoding="utf-8"))
    for variant, spec in VARIANTS.items():
        out_dir = CONFIGS_DIR / "model-arch" / variant
        out_dir.mkdir(parents=True, exist_ok=True)
        for seed in SEEDS:
            cfg = copy.deepcopy(base)
            note = f"{spec['prefix']}_s{seed}"
            cfg["run"]["note"] = note
            model = copy.deepcopy(spec["model"])
            model["kwargs"][spec["seed_key"]] = seed
            cfg["model"] = model
            rel = f"model-arch/{variant}/{note}.yaml"
            header = (
                f"# EXPERIMENT_STANDARD v1.6 | exp: model-arch/{variant} | seed {seed}\n"
                f"# /opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py --config {rel}\n"
            )
            (out_dir / f"{note}.yaml").write_text(
                header + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            print(f"wrote {rel}")


if __name__ == "__main__":
    main()
