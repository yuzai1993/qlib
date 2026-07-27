"""Register model-arch-nn/tra: pairwise vs B5 + registry.jsonl row."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
IC_DIR = BACKTEST_ROOT / "experiments" / "ic"
REGISTRY = BACKTEST_ROOT / "experiments" / "registry.jsonl"
RESULT_ROOT = BACKTEST_ROOT / "result"

SEEDS = [42, 1000, 2000, 3000, 4000]
BASELINE_1D = IC_DIR / "ls_rank_norm_test_1d.json"  # B5-M
PREFIX = "ma_tra"

HYPOTHESIS = (
    "在与B5完全相同的数据/特征/标签口径下（CSI1000、Alpha158+range 164特征、"
    "H40累计收益标签、DropnaLabel+CSRankNorm标签处理、41交易日purge），"
    "用TRA（LSTM 256x2+attention骨干、num_states=3、OT router、LR_TPE记忆）替换"
    "DoubleEnsemble：时序骨干可利用60日序列上下文而非单日截面快照，"
    "TRA的多预测头按记忆状态路由可自适应不同市场状态（趋势/震荡）下的因子失效，"
    "预期CSI1000 RankIC超过B5"
)

NOTE_DEVIATIONS = (
    "实现口径：特征加RobustZScoreNorm+Fillna（NN必需，统计量拟合于训练期2016-2020，无泄漏）；"
    "handler起点2014-01-02（因果窗口下2016起特征值与2003起点完全一致，内存减半）；"
    "训练在MPS上（CPU LSTM因lightgbm/torch的libomp冲突段错误），LeanMTSDatasetH+"
    "empty_cache补丁控制内存；epochs预算化（n_epochs 40、early_stop 10、"
    "max_steps_per_epoch 150，官方骨干超参不变）；评估为冷启动记忆单遍推理，"
    "DropnaLabel使test末41交易日无预测（三池一致，约3%天数）"
)


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
    doc_1d = load_ic(IC_DIR / f"{PREFIX}_test_1d.json")
    doc_self = load_ic(IC_DIR / f"{PREFIX}_test_self.json")
    metrics_1d = seed_mean_block(doc_1d)
    pairwise = pairwise_vs_baseline(doc_1d, base_1d)
    improve = all(
        metrics_1d[pool]["rank_ic_mean"]
        > base_1d["pools"][pool]["seed_mean"]["rank_ic_mean"]
        for pool in ("csi300", "csi500", "csi1000")
    )
    note = (
        ("三池RankIC均超B5" if improve else "未全面超过B5")
        + f"；CSI1000 RankIC {metrics_1d['csi1000']['rank_ic_mean']:.5f}"
        + f"（B5 {base_1d['pools']['csi1000']['seed_mean']['rank_ic_mean']:.5f}）"
        + f"，RankICIR {metrics_1d['csi1000']['rank_icir']:.4f}"
        + f"（B5 {base_1d['pools']['csi1000']['seed_mean']['rank_icir']:.4f}）"
        + f"，逐种子pairwise {pairwise['wins']}/5。"
        + NOTE_DEVIATIONS
    )
    row = {
        "exp_id": "model-arch-nn/tra",
        "direction": "model-arch-nn",
        "phase": "M",
        "date": date.today().isoformat(),
        "hypothesis": HYPOTHESIS,
        "baseline_ref": "B5 v1.0",
        "seeds": SEEDS,
        "train_pool": "csi1000",
        "label_kind": "cumulative_return",
        "label_horizon": 40,
        "label": "Ref($close,-41)/Ref($close,-1)-1",
        "purge_trading_days": 41,
        "feature_groups": ["range"],
        "feature_count": 164,
        "model": "TRAModel(LSTM256x2+attn, num_states=3, router, LR_TPE)",
        "primary_test_pool": "csi1000",
        "test_pools": ["csi1000", "csi300", "csi500"],
        "data_version": doc_1d["data_version"],
        "configs": [
            f"backtest/configs/model-arch-nn/tra/{PREFIX}_s{seed}.yaml" for seed in SEEDS
        ],
        "result_dirs": result_dirs(PREFIX)
        + [
            f"backtest/experiments/ic/{PREFIX}_test_1d.json",
            f"backtest/experiments/ic/{PREFIX}_test_self.json",
        ],
        "metrics_summary": metrics_1d,
        "metrics_by_eval_label": {
            "eval_1d": metrics_1d,
            "eval_self": seed_mean_block(doc_self),
        },
        "pairwise_csi1000_rankic_vs_b5": pairwise,
        "conclusion": "improve" if improve else "regress",
        "note": note,
    }

    with REGISTRY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        f"registered {row['exp_id']}: conclusion={row['conclusion']}, "
        f"csi1000 rank_ic={metrics_1d['csi1000']['rank_ic_mean']:.5f}, "
        f"pairwise wins={pairwise['wins']}/5"
    )


if __name__ == "__main__":
    main()
