"""Pre-register and finalize the B5 rolling/vol-scaled Phase-M experiments."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Sequence

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = BACKTEST_ROOT / "experiments" / "registry.jsonl"
IC_DIR = BACKTEST_ROOT / "experiments" / "ic"
RESULT_ROOT = BACKTEST_ROOT / "result"
BASELINE_IC = IC_DIR / "ls_rank_norm_test_1d.json"
BASELINE_YEARLY_IC = IC_DIR / "ls_rank_norm_test_1d_yearly.json"
SEEDS = [42, 1000, 2000, 3000, 4000]
POOLS = ["csi1000", "csi300", "csi500"]
VOL_LABEL = (
    "(Ref($close,-41)/Ref($close,-1)-1)"
    "/If(Gt(Std($close/Ref($close,1)-1,20),0.005),"
    "Std($close/Ref($close,1)-1,20),0.005)"
)

VOL_HYPOTHESIS = (
    "在B5-M的H40累计未来收益上，仅以预测时点可得的过去20日个股日收益波动率做缩放"
    "（波动率下限0.5%/日），随后保持DropnaLabel+CSRankNorm；该处理改变目标为风险调整"
    "收益排序，可能强化测试期有效的低波风格并提高CSI1000固定次日RankIC，但若只是放大"
    "B5既有低波暴露则三池RankIC或RankICIR不升"
)
ROLLING_HYPOTHESIS = (
    "在B5-M模型、特征、H40+CSRankNorm标签与超参完全冻结的前提下，每252个交易日重训"
    "一次；train起点固定2016-01-02且终点逐fold扩展，valid按同样步长平移，train/valid"
    "均保留41交易日purge，仅拼接每个fold的纯样本外预测；预期纳入当时可得的新样本可"
    "缓解2024-2026分布漂移，使CSI1000全测试段RankIC与RankICIR高于静态B5且跨池不恶化"
)
ROLLING_ES5_HYPOTHESIS = (
    "在年度expanding滚动重训方案上仅加入early_stopping_rounds=5，最大boosting轮数仍为"
    "28并由每折valid L2选择best_iteration；若更新样本增多后固定28轮造成过拟合，则"
    "早停应提高相对无早停滚动对照的CSI1000五种子平均RankIC且逐种子至少3/5胜出；"
    "参数在test评估前锁定"
)

EXPERIMENTS = {
    "vol20": {
        "exp_id": "label-risk-adjustment/vol20-scaled",
        "direction": "label-risk-adjustment",
        "prefix": "lra_vol20_scaled",
        "ic_file": "lra_vol20_scaled_test_1d.json",
        "hypothesis": VOL_HYPOTHESIS,
        "config_dir": "label-risk-adjustment/vol20-scaled",
    },
    "rolling": {
        "exp_id": "train-schedule/expanding-annual",
        "direction": "train-schedule",
        "prefix": "ts_expanding_annual",
        "ic_file": "ts_expanding_annual_test_1d.json",
        "hypothesis": ROLLING_HYPOTHESIS,
        "config_dir": "train-schedule/expanding-annual",
        "rolling": True,
    },
    "rolling_es5": {
        "exp_id": "train-schedule/expanding-annual-es5",
        "direction": "train-schedule",
        "prefix": "ts_expanding_annual_es5",
        "ic_file": "ts_expanding_annual_es5_test_1d.json",
        "hypothesis": ROLLING_ES5_HYPOTHESIS,
        "config_dir": "train-schedule/expanding-annual-es5",
        "rolling": True,
        "direct_control_file": "ts_expanding_annual_test_1d.json",
    },
}


def upsert_registry_row(path: Path, row: dict) -> None:
    rows = []
    if path.is_file():
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    replaced = False
    for index, existing in enumerate(rows):
        if existing.get("exp_id") == row["exp_id"]:
            rows[index] = row
            replaced = True
            break
    if not replaced:
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows),
        encoding="utf-8",
    )


def _common_pending(spec: dict, today: str) -> dict:
    return {
        "exp_id": spec["exp_id"],
        "direction": spec["direction"],
        "phase": "M",
        "date": today,
        "hypothesis": spec["hypothesis"],
        "baseline_ref": "B5 v1.0",
        "seeds": SEEDS,
        "train_pool": "csi1000",
        "label_horizon": 40,
        "purge_trading_days": 41,
        "feature_groups": ["range"],
        "feature_count": 6,
        "model": "DEnsembleModel",
        "learn_processors": ["DropnaLabel", "CSRankNorm(label)"],
        "primary_test_pool": "csi1000",
        "test_pools": POOLS,
        "data_version": "2026-07-27",
        "configs": [
            (
                f"backtest/configs/{spec['config_dir']}/"
                f"{spec['prefix']}_s{seed}.yaml"
            )
            for seed in SEEDS
        ],
        "result_dirs": [],
        "metrics_summary": {},
        "conclusion": "pending",
    }


def pending_rows(today: str | None = None) -> list[dict]:
    today = today or date.today().isoformat()
    vol = _common_pending(EXPERIMENTS["vol20"], today)
    vol.update(
        {
            "label_kind": "vol_scaled_cumulative_return",
            "label": VOL_LABEL,
            "volatility_window": 20,
            "volatility_floor": 0.005,
            "volatility_timing": "past_only",
            "note": (
                "实验运行前预登记；固定评测标签为次日收益，禁止按test调整"
                "波动率窗口或下限。"
            ),
        }
    )
    rolling = _common_pending(EXPERIMENTS["rolling"], today)
    rolling.update(
        {
            "label_kind": "cumulative_return",
            "label": "Ref($close,-41)/Ref($close,-1)-1",
            "rolling_type": "expanding",
            "rolling_step_trading_days": 252,
            "note": (
                "实验运行前预登记；年度频率、expanding窗口和所有模型参数"
                "已锁定，不按test结果调整。"
            ),
        }
    )
    rolling_es5 = _common_pending(EXPERIMENTS["rolling_es5"], today)
    rolling_es5.update(
        {
            "label_kind": "cumulative_return",
            "label": "Ref($close,-41)/Ref($close,-1)-1",
            "rolling_type": "expanding",
            "rolling_step_trading_days": 252,
            "max_boosting_rounds": 28,
            "early_stopping_rounds": 5,
            "control_ref": "train-schedule/expanding-annual",
            "note": (
                "实验运行前预登记；仅加入early_stopping_rounds=5，最大轮数28；"
                "直接对照为无早停年度滚动实验，不按test调整patience。"
            ),
        }
    )
    return [vol, rolling, rolling_es5]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _metrics(doc: dict) -> dict:
    return {pool: doc["pools"][pool]["seed_mean"] for pool in POOLS}


def _pairwise(candidate: dict, baseline: dict) -> dict:
    diffs = [
        (
            candidate["pools"]["csi1000"]["seeds"][str(seed)][
                "rank_ic_mean"
            ]
            - baseline["pools"]["csi1000"]["seeds"][str(seed)][
                "rank_ic_mean"
            ]
        )
        for seed in SEEDS
    ]
    return {
        "n": len(diffs),
        "wins": sum(value > 0 for value in diffs),
        "diff_mean": sum(diffs) / len(diffs),
        "diffs": diffs,
    }


def _early_stopping_assessment(candidate: dict, control: dict) -> dict:
    pairwise = _pairwise(candidate, control)
    candidate_mean = candidate["pools"]["csi1000"]["seed_mean"][
        "rank_ic_mean"
    ]
    control_mean = control["pools"]["csi1000"]["seed_mean"]["rank_ic_mean"]
    diagnostics = candidate["rolling"]["model_diagnostics"]
    triggered = int(diagnostics["triggered_count"])
    if triggered == 0:
        verdict = "redundant_at_current_round_cap"
    elif candidate_mean > control_mean and pairwise["wins"] >= 3:
        verdict = "improve"
    else:
        verdict = "not_beneficial"
    return {
        "verdict": verdict,
        "csi1000_rank_ic_delta": candidate_mean - control_mean,
        "pairwise_csi1000_rankic_vs_no_es": pairwise,
        "triggered_count": triggered,
        "booster_count": int(diagnostics["booster_count"]),
    }


def _yearly_rank_ic_delta(candidate: dict, baseline: dict) -> dict:
    first_pool = POOLS[0]
    first_seed = next(iter(candidate["pools"][first_pool]["seeds"].values()))
    years = sorted(first_seed["yearly"])
    output = {}
    for year in years:
        output[year] = {}
        for pool in POOLS:
            candidate_values = [
                row["yearly"][year]["rank_ic_mean"]
                for row in candidate["pools"][pool]["seeds"].values()
            ]
            baseline_values = [
                row["yearly"][year]["rank_ic_mean"]
                for row in baseline["pools"][pool]["seeds"].values()
            ]
            output[year][pool] = (
                sum(candidate_values) / len(candidate_values)
                - sum(baseline_values) / len(baseline_values)
            )
    return output


def _result_sessions(prefix: str) -> list[str]:
    sessions = []
    for seed in SEEDS:
        matches = sorted(RESULT_ROOT.glob(f"*_{prefix}_s{seed}"))
        if not matches:
            raise FileNotFoundError(f"missing result session: {prefix}_s{seed}")
        sessions.append(f"backtest/result/{matches[-1].name}")
    return sessions


def finalize_row(pending: dict, spec: dict) -> dict:
    candidate = _load_json(IC_DIR / spec["ic_file"])
    baseline = _load_json(BASELINE_IC)
    metrics = _metrics(candidate)
    pairwise = _pairwise(candidate, baseline)
    improve = all(
        metrics[pool]["rank_ic_mean"]
        > baseline["pools"][pool]["seed_mean"]["rank_ic_mean"]
        for pool in POOLS
    )
    row = dict(pending)
    row.update(
        {
            "data_version": candidate["data_version"],
            "result_dirs": _result_sessions(spec["prefix"])
            + [f"backtest/experiments/ic/{spec['ic_file']}"],
            "metrics_summary": metrics,
            "pairwise_csi1000_rankic_vs_b5": pairwise,
            "conclusion": "improve" if improve else "regress",
            "note": (
                ("三池RankIC均超B5" if improve else "未全面超过B5")
                + f"；CSI1000 RankIC {metrics['csi1000']['rank_ic_mean']:.5f}"
                + "（B5 "
                + f"{baseline['pools']['csi1000']['seed_mean']['rank_ic_mean']:.5f}）"
                + f"，RankICIR {metrics['csi1000']['rank_icir']:.4f}"
                + "（B5 "
                + f"{baseline['pools']['csi1000']['seed_mean']['rank_icir']:.4f}）"
                + f"，逐种子pairwise {pairwise['wins']}/5。"
            ),
        }
    )
    if spec.get("rolling"):
        row["rolling_folds"] = candidate["rolling"]["folds"]
        row["rolling_fold_count"] = candidate["rolling"]["fold_count"]
        if BASELINE_YEARLY_IC.is_file():
            yearly_delta = _yearly_rank_ic_delta(
                candidate,
                _load_json(BASELINE_YEARLY_IC),
            )
            row["yearly_rank_ic_delta_vs_b5"] = yearly_delta
            all_up = [
                year
                for year, values in yearly_delta.items()
                if all(value > 0 for value in values.values())
            ]
            all_down = [
                year
                for year, values in yearly_delta.items()
                if all(value < 0 for value in values.values())
            ]
            row["note"] += (
                f" 年度切片三池同时改善：{','.join(all_up) or '无'}；"
                f"三池同时退化：{','.join(all_down) or '无'}。"
            )
    if spec.get("direct_control_file"):
        control = _load_json(IC_DIR / spec["direct_control_file"])
        assessment = _early_stopping_assessment(candidate, control)
        row["model_diagnostics"] = candidate["rolling"]["model_diagnostics"]
        row["early_stopping_assessment"] = assessment
        row["note"] += (
            " 相对无早停年度滚动：CSI1000 RankIC "
            f"{assessment['csi1000_rank_ic_delta']:+.5f}，逐种子 "
            f"{assessment['pairwise_csi1000_rankic_vs_no_es']['wins']}/5，"
            f"早停触发 {assessment['triggered_count']}/"
            f"{assessment['booster_count']}，判定 {assessment['verdict']}。"
        )
    return row


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("pending", "final"), required=True)
    parser.add_argument(
        "--experiment",
        choices=("all", *EXPERIMENTS),
        default="all",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    pending = {row["exp_id"]: row for row in pending_rows()}
    keys = list(EXPERIMENTS) if args.experiment == "all" else [args.experiment]
    for key in keys:
        spec = EXPERIMENTS[key]
        row = pending[spec["exp_id"]]
        if args.stage == "final":
            row = finalize_row(row, spec)
        upsert_registry_row(REGISTRY, row)
        print(
            f"{args.stage}: {row['exp_id']} "
            f"conclusion={row['conclusion']}"
        )


if __name__ == "__main__":
    main()
