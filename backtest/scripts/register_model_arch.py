"""Register model-arch experiments: pairwise vs B3 + registry.jsonl rows."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
IC_DIR = BACKTEST_ROOT / "experiments" / "ic"
REGISTRY = BACKTEST_ROOT / "experiments" / "registry.jsonl"
RESULT_ROOT = BACKTEST_ROOT / "result"

SEEDS = [42, 1000, 2000, 3000, 4000]
BASELINE_1D = IC_DIR / "fb2_range_test_1d.json"

VARIANTS = {
    "xgboost": {
        "prefix": "ma_xgboost",
        "hypothesis": (
            "仅将B3-M的LGBM替换为XGBoost（qlib官方Alpha158 benchmark超参：eta=0.0421、"
            "max_depth=8、colsample=0.8879、subsample=0.8789、hist），特征/标签/数据处理不变；"
            "官方benchmark中XGBoost RankIC 0.0505>LightGBM 0.0469，预期不同boosting实现的"
            "归纳偏置差异带来主池RankIC提升"
        ),
    },
    "catboost": {
        "prefix": "ma_catboost",
        "hypothesis": (
            "仅将B3-M的LGBM替换为CatBoost（qlib官方超参：lr=0.0421、depth=6、num_leaves=100、"
            "Lossguide；CPU限制bootstrap由Poisson改Bernoulli）；官方benchmark RankIC略低于LGBM"
            "（0.0454 vs 0.0469），作为不同boosting家族对照，预期与基线相当，"
            "主要检验ordered boosting在H40标签上的稳定性（RankICIR）"
        ),
    },
    "double-ensemble": {
        "prefix": "ma_double_ensemble",
        "hypothesis": (
            "将B3-M的LGBM包进DoubleEnsemble（3子模型、样本重加权SR+特征选择FS、官方epochs=28、"
            "decay=0.5，LGB超参与B3完全相同）；官方Alpha158 benchmark中DoubleEnsemble IC最高"
            "（0.0521）、RankIC 0.0502>LGBM，预期集成降方差使RankIC/RankICIR同步提升"
        ),
    },
}


def load_ic(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def seed_mean_block(doc: dict) -> dict:
    return {pool: doc["pools"][pool]["seed_mean"] for pool in ("csi300", "csi500", "csi1000")}


def pairwise_vs_baseline(cand: dict, base: dict, pool: str = "csi1000") -> dict:
    diffs = []
    wins = 0
    for seed in SEEDS:
        c = cand["pools"][pool]["seeds"][str(seed)]["rank_ic_mean"]
        b = base["pools"][pool]["seeds"][str(seed)]["rank_ic_mean"]
        d = c - b
        diffs.append(d)
        if d > 0:
            wins += 1
    return {
        "n": len(SEEDS),
        "wins": wins,
        "diff_mean": sum(diffs) / len(diffs),
        "diffs": diffs,
    }


def result_dirs(prefix: str) -> list[str]:
    dirs = []
    for seed in SEEDS:
        matches = sorted(RESULT_ROOT.glob(f"*_{prefix}_s{seed}"))
        if not matches:
            raise FileNotFoundError(f"missing result session for {prefix}_s{seed}")
        dirs.append(f"backtest/result/{matches[-1].name}")
    return dirs


def main() -> None:
    base_1d = load_ic(BASELINE_1D)
    today = date.today().isoformat()
    rows = []
    for variant, spec in VARIANTS.items():
        prefix = spec["prefix"]
        doc_1d = load_ic(IC_DIR / f"{prefix}_test_1d.json")
        doc_self = load_ic(IC_DIR / f"{prefix}_test_self.json")
        metrics_1d = seed_mean_block(doc_1d)
        pairwise = pairwise_vs_baseline(doc_1d, base_1d)
        improve = all(
            metrics_1d[pool]["rank_ic_mean"]
            > base_1d["pools"][pool]["seed_mean"]["rank_ic_mean"]
            for pool in ("csi300", "csi500", "csi1000")
        )
        row = {
            "exp_id": f"model-arch/{variant}",
            "direction": "model-arch",
            "phase": "M",
            "date": today,
            "hypothesis": spec["hypothesis"],
            "baseline_ref": "B3 v1.0",
            "seeds": SEEDS,
            "train_pool": "csi1000",
            "label_kind": "cumulative_return",
            "label_horizon": 40,
            "label": "Ref($close,-41)/Ref($close,-1)-1",
            "purge_trading_days": 41,
            "feature_groups": ["range"],
            "feature_count": 6,
            "primary_test_pool": "csi1000",
            "test_pools": ["csi1000", "csi300", "csi500"],
            "data_version": doc_1d["data_version"],
            "configs": [
                f"backtest/configs/model-arch/{variant}/{prefix}_s{seed}.yaml"
                for seed in SEEDS
            ],
            "result_dirs": result_dirs(prefix)
            + [
                f"backtest/experiments/ic/{prefix}_test_1d.json",
                f"backtest/experiments/ic/{prefix}_test_self.json",
            ],
            "metrics_summary": metrics_1d,
            "metrics_by_eval_label": {
                "eval_1d": metrics_1d,
                "eval_self": seed_mean_block(doc_self),
            },
            "pairwise_csi1000_rankic_vs_b3": pairwise,
            "conclusion": "improve" if improve else "regress",
            "note": (
                ("三池RankIC均超B3" if improve else "未全面超过B3")
                + f"；CSI1000 RankIC {metrics_1d['csi1000']['rank_ic_mean']:.5f}"
                + f"（B3 {base_1d['pools']['csi1000']['seed_mean']['rank_ic_mean']:.5f}）"
                + f"，RankICIR {metrics_1d['csi1000']['rank_icir']:.4f}"
                + f"（B3 {base_1d['pools']['csi1000']['seed_mean']['rank_icir']:.4f}）"
                + f"，逐种子pairwise {pairwise['wins']}/5"
            ),
        }
        rows.append(row)

    with REGISTRY.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                f"registered {row['exp_id']}: conclusion={row['conclusion']}, "
                f"csi1000 rank_ic={row['metrics_summary']['csi1000']['rank_ic_mean']:.5f}, "
                f"pairwise wins={row['pairwise_csi1000_rankic_vs_b3']['wins']}/5"
            )


if __name__ == "__main__":
    main()
