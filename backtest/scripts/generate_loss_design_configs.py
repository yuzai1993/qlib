"""Generate loss-design experiment configs (EXPERIMENT_STANDARD v1.7, baseline B4 v1.0).

All variants derive from the B4-M config (model-arch/double-ensemble) and
change exactly one variable each:

  - loss-design/cs-rank-norm       learn label processor CSZScoreNorm -> CSRankNorm
  - loss-design/huber              LGBM objective mse -> huber (alpha=0.9)
  - loss-design/topk-weighted-mse  static head sample weights (top 20% -> up to 3x)
  - loss-design/lambdarank         single LGBM lambdarank (architecture control: B3-M)
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = BACKTEST_ROOT / "configs"
BASELINE_CONFIG = CONFIGS_DIR / "model-arch/double-ensemble/ma_double_ensemble_s42.yaml"

SEEDS = [42, 1000, 2000, 3000, 4000]

# B3/B4 sub-model LGB tree params (kept identical for the lambdarank variant).
B3_TREE_PARAMS = {
    "colsample_bytree": 0.8879,
    "learning_rate": 0.2,
    "subsample": 0.8789,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 8,
    "num_leaves": 210,
    "num_threads": 8,
}

RANK_NORM_LEARN_PROCESSORS = [
    {"class": "DropnaLabel"},
    {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
]


def _apply_cs_rank_norm(cfg: dict, seed: int) -> None:
    cfg["data"]["handler"]["learn_processors"] = copy.deepcopy(RANK_NORM_LEARN_PROCESSORS)
    cfg["model"]["kwargs"]["seed"] = seed


def _apply_huber(cfg: dict, seed: int) -> None:
    cfg["model"]["class"] = "HuberDEnsembleModel"
    cfg["model"]["module_path"] = "backtest.models.loss_design"
    cfg["model"]["kwargs"]["loss"] = "huber"
    cfg["model"]["kwargs"]["huber_alpha"] = 0.9
    cfg["model"]["kwargs"]["seed"] = seed


def _apply_topk_weighted(cfg: dict, seed: int) -> None:
    cfg["model"]["class"] = "HeadWeightedDEnsembleModel"
    cfg["model"]["module_path"] = "backtest.models.loss_design"
    cfg["model"]["kwargs"]["head_quantile"] = 0.8
    cfg["model"]["kwargs"]["head_weight_gain"] = 2.0
    cfg["model"]["kwargs"]["seed"] = seed


def _apply_lambdarank(cfg: dict, seed: int) -> None:
    # B3's mse-scale L1/L2 (205.7/581.0) suppress every split under lambdarank's
    # bounded gradients (smoke s42: 1 tree / 1 leaf, NDCG frozen from iter 1).
    # Valid-based adjustment per the pre-registered fallback: reset both to the
    # LightGBM ranking default 0.0; all other tree params stay identical to B3.
    tree_params = {**B3_TREE_PARAMS, "lambda_l1": 0.0, "lambda_l2": 0.0}
    cfg["model"] = {
        "class": "LGBRanker",
        "module_path": "backtest.models.lgbm_ranker",
        "kwargs": {
            "num_grades": 5,
            "ndcg_eval_at": 100,
            "early_stopping_rounds": 50,
            "num_boost_round": 1000,
            **tree_params,
            "seed": seed,
        },
    }


VARIANTS = {
    "cs-rank-norm": {"prefix": "ls_rank_norm", "apply": _apply_cs_rank_norm},
    "huber": {"prefix": "ls_huber", "apply": _apply_huber},
    "topk-weighted-mse": {"prefix": "ls_topk_weighted", "apply": _apply_topk_weighted},
    "lambdarank": {"prefix": "ls_lambdarank", "apply": _apply_lambdarank},
}


def main() -> None:
    base = yaml.safe_load(BASELINE_CONFIG.read_text(encoding="utf-8"))
    for variant, spec in VARIANTS.items():
        out_dir = CONFIGS_DIR / "loss-design" / variant
        out_dir.mkdir(parents=True, exist_ok=True)
        for seed in SEEDS:
            cfg = copy.deepcopy(base)
            note = f"{spec['prefix']}_s{seed}"
            cfg["run"]["note"] = note
            spec["apply"](cfg, seed)
            rel = f"loss-design/{variant}/{note}.yaml"
            header = (
                f"# EXPERIMENT_STANDARD v1.7 | exp: loss-design/{variant} | seed {seed}\n"
                f"# /opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py --config {rel}\n"
            )
            (out_dir / f"{note}.yaml").write_text(
                header + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            print(f"wrote {rel}")


if __name__ == "__main__":
    main()
