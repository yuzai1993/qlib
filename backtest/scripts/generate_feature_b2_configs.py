"""生成 feature-b2 方向的 train_only 配置（B2 模板 + Alpha158Technical 特征组）。

用法:
    /opt/anaconda3/envs/qlib/bin/python backtest/scripts/generate_feature_b2_configs.py
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = BACKTEST_ROOT / "configs/label-design/cum-h40/ld_cum_h40_lgbm_s42.yaml"
SEEDS = [42, 1000, 2000, 3000, 4000]

VARIANTS = {
    "ratio": {"feature_groups": ["ratio"]},
    "mastruct": {"feature_groups": ["mastruct"]},
    "range": {"feature_groups": ["range"]},
    "combined": {"feature_groups": ["ratio", "mastruct", "range"]},
    "combined-l1low": {
        "feature_groups": ["ratio", "mastruct", "range"],
        "lambda_l1": 51.425,
    },
}


def main() -> None:
    template = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    for variant, spec in VARIANTS.items():
        slug = variant.replace("-", "_")
        out_dir = BACKTEST_ROOT / "configs/feature-b2" / variant
        out_dir.mkdir(parents=True, exist_ok=True)
        for seed in SEEDS:
            cfg = copy.deepcopy(template)
            note = f"fb2_{slug}_lgbm_s{seed}"
            cfg["run"]["note"] = note
            handler = cfg["data"]["handler"]
            handler["class"] = "Alpha158Technical"
            handler["module_path"] = "backtest.features.technical"
            handler["feature_groups"] = list(spec["feature_groups"])
            cfg["model"]["kwargs"]["seed"] = seed
            if "lambda_l1" in spec:
                cfg["model"]["kwargs"]["lambda_l1"] = spec["lambda_l1"]
            rel = f"feature-b2/{variant}/{note}.yaml"
            header = (
                f"# EXPERIMENT_STANDARD v1.5 | exp: feature-b2/{variant} | seed {seed}\n"
                f"# /opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py"
                f" --config {rel}\n"
            )
            path = out_dir / f"{note}.yaml"
            path.write_text(
                header + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            print("written:", path.relative_to(BACKTEST_ROOT.parent))


if __name__ == "__main__":
    main()
