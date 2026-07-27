"""Register loss-design experiments: pairwise vs B4 + registry.jsonl rows."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
IC_DIR = BACKTEST_ROOT / "experiments" / "ic"
REGISTRY = BACKTEST_ROOT / "experiments" / "registry.jsonl"
RESULT_ROOT = BACKTEST_ROOT / "result"

SEEDS = [42, 1000, 2000, 3000, 4000]
BASELINE_1D = IC_DIR / "ma_double_ensemble_test_1d.json"  # B4-M
B3_1D = IC_DIR / "fb2_range_test_1d.json"  # architecture control for lambdarank

VARIANTS = {
    "cs-rank-norm": {
        "prefix": "ls_rank_norm",
        "hypothesis": (
            "仅将训练标签的截面处理由CSZScoreNorm改为CSRankNorm（DropnaLabel保留），"
            "模型/特征/H40标签表达式与B4完全相同；MSE回归秩归一化标签≈直接对秩回归，"
            "训练目标从对齐Pearson IC变为对齐RankIC，且秩有界消除z-score厚尾对梯度的主导；"
            "预期CSI1000 RankIC提升、RankICIR不降"
        ),
    },
    "huber": {
        "prefix": "ls_huber",
        "hypothesis": (
            "仅将B4 DoubleEnsemble子模型LGBM的objective由mse改为huber（alpha=0.9，"
            "z-score标签下约0.9σ内二次/外线性），标签处理与其余超参不变；"
            "SR模块只消费逐样本损失的秩（huber与mse同序、行为一致），FS用huber量值；"
            "预期抑制厚尾极端样本主导梯度，RankICIR提升、RankIC持平或小升"
        ),
    },
    "topk-weighted-mse": {
        "prefix": "ls_topk_weighted",
        "hypothesis": (
            "在B4 DoubleEnsemble上对日内标签rank pct>0.8的样本静态线性加权至最高3×"
            "（w=1+2*max(0,(r-0.8)/0.2)，与SR动态权重相乘），其余不变；"
            "损失容量偏向Topk策略实际消费的截面头部，预期CSI1000 RankIC小幅提升；"
            "若RankICIR明显下降说明头部过拟合，判负"
        ),
    },
    "lambdarank": {
        "prefix": "ls_lambdarank",
        "hypothesis": (
            "单LGBM上以lambdarank替代mse回归（按交易日分组、日内标签5档分位等级、"
            "NDCG@100早停），直接优化头部排序；架构对照为B3-M（单LGBM，CSI1000 RankIC "
            "0.04203），registry基线仍为B4，超B4才有采纳意义；树参数沿用B3，若正则与"
            "ranking梯度尺度失配则按valid调整并在note记录"
        ),
        "extra_note": (
            "预注册回退已触发：B3的mse尺度正则(l1=205.7/l2=581.0)使lambdarank全部分裂"
            "被抑制（冒烟s42退化为1树1叶、NDCG冻结），按valid将lambda_l1/l2归零后正常学习；"
            "其余树参数与B3一致"
        ),
        "objective": "lambdarank",
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
    b3_1d = load_ic(B3_1D)
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
        note = (
            ("三池RankIC均超B4" if improve else "未全面超过B4")
            + f"；CSI1000 RankIC {metrics_1d['csi1000']['rank_ic_mean']:.5f}"
            + f"（B4 {base_1d['pools']['csi1000']['seed_mean']['rank_ic_mean']:.5f}）"
            + f"，RankICIR {metrics_1d['csi1000']['rank_icir']:.4f}"
            + f"（B4 {base_1d['pools']['csi1000']['seed_mean']['rank_icir']:.4f}）"
            + f"，逐种子pairwise {pairwise['wins']}/5"
        )
        if variant == "lambdarank":
            note += (
                f"；架构对照B3-M CSI1000 RankIC "
                f"{b3_1d['pools']['csi1000']['seed_mean']['rank_ic_mean']:.5f}"
            )
            note += "。" + spec["extra_note"]
        row = {
            "exp_id": f"loss-design/{variant}",
            "direction": "loss-design",
            "phase": "M",
            "date": today,
            "hypothesis": spec["hypothesis"],
            "baseline_ref": "B4 v1.0",
            "seeds": SEEDS,
            "train_pool": "csi1000",
            "label_kind": "cumulative_return",
            "label_horizon": 40,
            "label": "Ref($close,-41)/Ref($close,-1)-1",
            "purge_trading_days": 41,
            "feature_groups": ["range"],
            "feature_count": 6,
            "model": (
                "LGBRanker" if variant == "lambdarank"
                else "DEnsembleModel"
            ),
            "primary_test_pool": "csi1000",
            "test_pools": ["csi1000", "csi300", "csi500"],
            "data_version": doc_1d["data_version"],
            "configs": [
                f"backtest/configs/loss-design/{variant}/{prefix}_s{seed}.yaml"
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
            "pairwise_csi1000_rankic_vs_b4": pairwise,
            "conclusion": "improve" if improve else "regress",
            "note": note,
        }
        rows.append(row)

    with REGISTRY.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                f"registered {row['exp_id']}: conclusion={row['conclusion']}, "
                f"csi1000 rank_ic={row['metrics_summary']['csi1000']['rank_ic_mean']:.5f}, "
                f"pairwise wins={row['pairwise_csi1000_rankic_vs_b4']['wins']}/5"
            )


if __name__ == "__main__":
    main()
