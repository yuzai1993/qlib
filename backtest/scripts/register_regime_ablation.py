"""登记 / 刷新 Phase M v1 的 feat / sample 单因子实验。"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

EXP_ROOT = Path(__file__).resolve().parents[2]
REG = EXP_ROOT / "backtest" / "experiments" / "registry.jsonl"
BASELINE_ID = "regime-adapt/m0-h20-t5h5-es-v1"

SPECS = [
    {
        "exp_id": "regime-adapt/m0-h20-regime-feat-v1",
        "display_name": "M0 H20 + regime 特征",
        "arm": "m0-h20-feat",
        "eval": "backtest/result/eval_regime_ablation/eval_feat.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20_feat_report.html",
        "hypothesis": (
            "只加 11 列 regime 特征（Alpha158RegimeTechnical），标签仍 H20、"
            "日权重仍用 M0 自然分布；预期主格 top5×h5 扣费夏普相对 M0 H20 上升"
        ),
        "note": "单因子：特征。权重=M0，标签=H20",
    },
    {
        "exp_id": "regime-adapt/m0-h20-sample-v1",
        "display_name": "M0 H20 + 样本采样",
        "arm": "m0-h20-sample",
        "eval": "backtest/result/eval_regime_ablation/eval_sample.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20_sample_report.html",
        "hypothesis": (
            "特征仍是 M0，只把训练日权换成 M3 的 55/30/15 + 48m 半衰期；"
            "预期主格 top5×h5 扣费夏普相对 M0 H20 上升，尤其 F 态"
        ),
        "note": "单因子：采样。特征=M0，标签=H20",
    },
    {
        "exp_id": "regime-adapt/m0-h20-densemble-s42-v1",
        "display_name": "M0 H20 DoubleEnsemble s42",
        "arm": "m0-h20-densemble",
        "eval": "backtest/result/eval_regime_ablation/eval_densemble.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20_densemble_report.html",
        "hypothesis": (
            "在 M0 H20 数据上把单 LGBM 换成 B6-M 冻结超参的 DoubleEnsemble"
            "（num_models=3, SR/FS, lr=0.1），单种子 42；"
            "预期主格 top5×h5 扣费夏普相对同种子 M0 H20 上升。"
            "单种子结果不作晋升依据"
        ),
        "note": "单种子 42；超参=B6-M FROZEN_MODEL_KWARGS；特征/权重/标签同 M0 H20",
        "seeds": [42],
    },
    {
        "exp_id": "regime-adapt/m0-h20es-densemble-s42-v1",
        "display_name": "M0 H20 ES + DoubleEnsemble s42",
        "arm": "m0-h20es-densemble",
        "eval": "backtest/result/eval_regime_ablation/eval_densemble_s42_vs_v2.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20es_densemble_report.html",
        "hypothesis": (
            "相对当前基线 v2（M0 H20 ES），只把单 LGBM 换成 B6-M 冻结超参"
            "DoubleEnsemble（num_models=3, SR/FS, lr=0.1）；"
            "数据仍是全A + H20 + M0 日权；早停沿用 B6-M 的 RankIC"
            "（DoubleEnsemble 尚未接 top5_h5_net_ann）。"
            "先跑种子 42；单种子结果不作晋升依据"
        ),
        "note": (
            "单种子 42；对照 v2 官方合成信号 + v2 seed 42；"
            "超参=B6-M FROZEN_MODEL_KWARGS；早停=daily_rank_ic"
        ),
        "seeds": [42],
    },
    {
        "exp_id": "regime-adapt/m0-h20-t5h5-es-v1",
        "display_name": "M0 H20 ES",
        "arm": "m0-h20-t5h5es",
        "eval": "backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20_t5h5es_report.html",
        "hypothesis": (
            "训练配方仍是 M0 H20，早停改为全A 1454 天 top5×h5 扣费净年化"
            "（上市>=60 + 日频 ST + 成交额>=1000万 + 剔t+1涨停）；"
            "valid=整段评估窗，乐观偏差大于原 499 天 RankIC 早停"
        ),
        "note": (
            "es_metric=top5_h5_net_ann；2026-08-19 晋升为 Phase M v1 当前基线；"
            "2026-08-20 官方主格改为五种子 z-score 等权合成后再算 top5×h5；"
            "不覆盖 regimeadaptfast_m0h20_s*"
        ),
        "direction": "m0-h20-es",
    },
    {
        "exp_id": "regime-adapt/m0-h20-st-daily-reeval",
        "display_name": "M0 H20（日频 ST 重评）",
        "arm": "m0-h20-st-daily",
        "eval": "backtest/result/eval_regime_m0_labels/eval_m0h20_st_daily.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20_t5h5es_report.html",
        "hypothesis": (
            "同一组 regimeadaptfast_m0h20_s*，仅把评估 ST 从静态 st_names 换成日频 st_daily；"
            "官方数字已并入 regime-adapt/m0-h20-label-v4"
        ),
        "note": "已被 v4 日频 ST 切口径吸收；本行 superseded",
        "direction": "m0-h20-es",
    },
    {
        "exp_id": "regime-adapt/m0-h20-t3h5es-densemble-s42-v1",
        "display_name": "v3 + DoubleEnsemble s42",
        "arm": "m0-h20-t3h5es-densemble",
        "eval": "backtest/result/eval_regime_ablation/eval_densemble_s42_vs_v3.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20_t3h5es_densemble_s42_report.html",
        "hypothesis": (
            "相对当前基线 v3（M0 H20 t3h5es），只把单 LGBM 换成 B6-M 冻结超参"
            "DoubleEnsemble（num_models=3, SR/FS, lr=0.1）；"
            "数据仍是全A + H20 + M0 日权；早停对齐 v3 的 top3_h5_net_ann。"
            "先跑种子 42；单种子结果不作晋升依据"
        ),
        "note": (
            "单种子 42；对照 v3 官方合成信号 + v3 seed 42；"
            "超参=B6-M FROZEN_MODEL_KWARGS；早停=top3_h5_net_ann"
        ),
        "seeds": [42],
        "baseline_ref": "regime-adapt/m0-h20-t3h5es-v1",
        "eval_protocol": (
            "allA_top3_h5_net_ann/vol/sharpe | 官方=五种子zscore均值信号一次评估 | "
            "网格 1/2/3/4/5×2/3/4/5 | 上市>=60 + 日频ST + 成交额>=1000万 + 剔t+1涨停"
        ),
        "result_dirs": ["backtest/result/regimeadapt_m0h20_t3h5es_densemble_s42"],
    },
    {
        "exp_id": "regime-adapt/m0-h20-t3h5es-densemble-v1",
        "display_name": "v3 + DoubleEnsemble",
        "arm": "m0-h20-t3h5es-densemble",
        "eval": "backtest/result/eval_regime_ablation/eval_densemble_vs_v3.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20_t3h5es_densemble_report.html",
        "hypothesis": (
            "相对当前基线 v3（M0 H20 t3h5es），只把单 LGBM 换成 B6-M 冻结超参"
            "DoubleEnsemble（num_models=3, SR/FS, lr=0.1）；"
            "数据仍是全A + H20 + M0 日权；早停对齐 v3 的 top3_h5_net_ann。"
            "五种子正式评估；官方数字为合成信号一次评估"
        ),
        "note": (
            "五种子 [42, 1000, 2000, 3000, 4000]；对照 v3 官方合成信号；"
            "超参=B6-M FROZEN_MODEL_KWARGS；早停=top3_h5_net_ann；"
            "单种子侦察见 regime-adapt/m0-h20-t3h5es-densemble-s42-v1"
        ),
        "seeds": [42, 1000, 2000, 3000, 4000],
        "baseline_ref": "regime-adapt/m0-h20-t3h5es-v1",
        "eval_protocol": (
            "allA_top3_h5_net_ann/vol/sharpe | 官方=五种子zscore均值信号一次评估 | "
            "网格 1/2/3/4/5×2/3/4/5 | 上市>=60 + 日频ST + 成交额>=1000万 + 剔t+1涨停"
        ),
        "result_dirs": [
            f"backtest/result/regimeadapt_m0h20_t3h5es_densemble_s{s}"
            for s in (42, 1000, 2000, 3000, 4000)
        ],
    },
    {
        "exp_id": "regime-adapt/m0-h20-rankices-v1",
        "display_name": "M0 H20 RankIC ES",
        "arm": "m0-h20-rankices",
        "eval": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20_rankices_report.html",
        "hypothesis": (
            "相对当前基线 v3，训练配方不变（全A + H20 + M0 日权 + 单 LGBM B3-M），"
            "valid 仍用评估窗 valid_frame_t5h5es.pkl（约 1454 天、H5 标签），"
            "只把早停打分从 top3_h5_net_ann 换成 daily_rank_ic。"
            "这不是重跑 v1（v1 是 499 天次日 RankIC）。"
            "预期：若 top3 早停过拟合头部噪声，主格净年化应接近或高于 v3，停止轮数更稳"
        ),
        "note": (
            "五种子；对照 v3 官方合成信号；"
            "es_metric=daily_rank_ic；es_valid=eval_window；"
            "超参=B3-M FROZEN_SINGLE_KWARGS"
        ),
        "seeds": [42, 1000, 2000, 3000, 4000],
        "baseline_ref": "self",
        "baseline_version": "v4",
        "eval_protocol": (
            "allA_top3_h5_net_ann/vol/sharpe | 官方=五种子zscore均值信号一次评估 | "
            "网格 1/2/3/4/5×2/3/4/5 | 上市>=60 + 日频ST + 成交额>=1000万 + 剔t+1涨停"
        ),
        "result_dirs": [
            f"backtest/result/regimeadaptfast_m0h20_rankices_s{s}"
            for s in (42, 1000, 2000, 3000, 4000)
        ],
    },
    {
        "exp_id": "regime-adapt/m0-h20-rankices-densemble-s42-v1",
        "display_name": "v4 + DoubleEnsemble s42",
        "arm": "m0-h20-rankices-densemble",
        "eval": "backtest/result/eval_regime_ablation/eval_densemble_s42_vs_v4.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20_rankices_densemble_s42_report.html",
        "hypothesis": (
            "相对当前基线 v4（M0 H20 RankIC ES），只把单 LGBM 换成 B6-M 冻结超参"
            "DoubleEnsemble（num_models=3, SR/FS, lr=0.1）；"
            "数据仍是全A + H20 + M0 日权；早停对齐 v4 的评估窗 daily_rank_ic。"
            "先跑种子 42；单种子结果不作晋升依据"
        ),
        "note": (
            "单种子 42；对照 v4 官方合成信号 + v4 seed 42；"
            "超参=B6-M FROZEN_MODEL_KWARGS；早停=评估窗 daily_rank_ic"
        ),
        "seeds": [42],
        "baseline_ref": "regime-adapt/m0-h20-rankices-v1",
        "eval_protocol": (
            "allA_top3_h5_net_ann/vol/sharpe | 官方=五种子zscore均值信号一次评估 | "
            "网格 1/2/3/4/5×2/3/4/5 | 上市>=60 + 日频ST + 成交额>=1000万 + 剔t+1涨停"
        ),
        "result_dirs": ["backtest/result/regimeadapt_m0h20_rankices_densemble_s42"],
    },
    {
        "exp_id": "regime-adapt/m0-h20-rankices-densemble-v1",
        "display_name": "v4 + DoubleEnsemble",
        "arm": "m0-h20-rankices-densemble",
        "eval": "backtest/result/eval_regime_ablation/eval_densemble_vs_v4.json",
        "detail_report": "backtest/experiments/regime_adapt_m0h20_rankices_densemble_report.html",
        "hypothesis": (
            "相对当前基线 v4（M0 H20 RankIC ES），只把单 LGBM 换成 B6-M 冻结超参"
            "DoubleEnsemble（num_models=3, SR/FS, lr=0.1）；"
            "数据仍是全A + H20 + M0 日权；早停对齐 v4 的评估窗 daily_rank_ic。"
            "五种子正式评估；官方数字为合成信号一次评估"
        ),
        "note": (
            "五种子 [42, 1000, 2000, 3000, 4000]；对照 v4 官方合成信号；"
            "超参=B6-M FROZEN_MODEL_KWARGS；早停=评估窗 daily_rank_ic；"
            "单种子侦察见 regime-adapt/m0-h20-rankices-densemble-s42-v1"
        ),
        "seeds": [42, 1000, 2000, 3000, 4000],
        "baseline_ref": "regime-adapt/m0-h20-rankices-v1",
        "eval_protocol": (
            "allA_top3_h5_net_ann/vol/sharpe | 官方=五种子zscore均值信号一次评估 | "
            "网格 1/2/3/4/5×2/3/4/5 | 上市>=60 + 日频ST + 成交额>=1000万 + 剔t+1涨停"
        ),
        "result_dirs": [
            f"backtest/result/regimeadapt_m0h20_rankices_densemble_s{s}"
            for s in (42, 1000, 2000, 3000, 4000)
        ],
    },
]


def daily_turnover(prim: dict, h: int = 5):
    daily = prim.get("turnover")
    period = prim.get("turnover_period")
    if period is not None:
        return daily if daily is not None else period / h
    if daily is not None and daily > 0.5:
        return daily / h
    return daily


def snap(rel: str) -> dict | None:
    path = EXP_ROOT / rel
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    pool = doc["pools"]["all"]
    sm = pool.get("ensemble") or pool.get("seed_mean") or {}
    prim = (sm.get("head") or {}).get("3", {}).get("5", {})
    return {
        "primary_k": 3,
        "primary_h": 5,
        "net_ann": prim.get("net_ann"),
        "net_ann_vol": prim.get("net_ann_vol"),
        "net_sharpe": prim.get("net_sharpe"),
        "ann": prim.get("ann"),
        "net_ann_excess": prim.get("net_ann_excess"),
        "ann_excess": prim.get("ann_excess"),
        "turnover": daily_turnover(prim),
        "n_days": prim.get("n_days"),
        "rank_ic_mean": (sm.get("h5") or sm.get("mean_h") or {}).get("rank_ic_mean"),
        "head": sm.get("head"),
        "filters": doc.get("filters"),
        "official_signal": doc.get("official_signal"),
    }


def load_registry() -> list[dict]:
    if not REG.exists():
        return []
    return [json.loads(line) for line in REG.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_registry(rows: list[dict]) -> None:
    REG.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_row(spec: dict, *, state: str, metrics: dict | None) -> dict:
    row = {
        "exp_id": spec["exp_id"],
        "direction": spec.get("direction") or "regime-adapt",
        "phase": "M",
        "phase_m_protocol": "v1",
        "date": date.today().isoformat(),
        "state": state,
        "arm": spec["arm"],
        "display_name": spec["display_name"],
        "train_label_horizon": 20,
        "seeds": spec.get("seeds") or [42, 1000, 2000, 3000, 4000],
        "hypothesis": spec["hypothesis"],
        "eval_protocol": spec.get(
            "eval_protocol",
            "allA_top5_h5_net_ann/vol/sharpe | 官方=五种子zscore均值信号一次评估 | "
            "网格 5/15/50×2/3/5/10 | 上市>=60 + 日频ST + 成交额>=1000万 + 剔t+1涨停",
        ),
        "eval_output": spec["eval"],
        "detail_report": spec["detail_report"],
        "note": spec["note"],
        "baseline_ref": spec.get(
            "baseline_ref",
            "self" if spec["exp_id"] == BASELINE_ID else BASELINE_ID,
        ),
        "plan": "backtest/experiments/plans/20260809_regime_adaptation_plan.md",
    }
    if spec.get("result_dirs"):
        row["result_dirs"] = list(spec["result_dirs"])
    if spec["exp_id"] == BASELINE_ID:
        row["baseline_version"] = "v2"
    elif spec.get("baseline_version"):
        row["baseline_version"] = spec["baseline_version"]
        row["result_dirs"] = [
            f"backtest/result/regimeadaptfast_m0h20_t5h5es_s{s}"
            for s in (42, 1000, 2000, 3000, 4000)
        ]
    if metrics is not None:
        row["metrics"] = metrics
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preregister", action="store_true")
    parser.add_argument(
        "--spec",
        choices=[
            "feat",
            "sample",
            "densemble",
            "densemble-v2",
            "densemble-v3",
            "densemble-v3-all",
            "densemble-v4",
            "densemble-v4-all",
            "rankices",
            "t5h5es",
            "m0h20-st-daily",
            "all",
        ],
        default="all",
    )
    args = parser.parse_args()
    by_key = {
        "feat": SPECS[0],
        "sample": SPECS[1],
        "densemble": SPECS[2],
        "densemble-v2": SPECS[3],
        "t5h5es": SPECS[4],
        "m0h20-st-daily": SPECS[5],
        "densemble-v3": SPECS[6],
        "densemble-v3-all": SPECS[7],
        "rankices": SPECS[8],
        "densemble-v4": SPECS[9],
        "densemble-v4-all": SPECS[10],
    }
    wanted = SPECS if args.spec == "all" else [by_key[args.spec]]
    rows = load_registry()
    index = {r.get("exp_id"): i for i, r in enumerate(rows)}
    for spec in wanted:
        metrics = None if args.preregister else snap(spec["eval"])
        if not args.preregister and metrics is None:
            print("skip missing", spec["exp_id"])
            continue
        state = "preregistered" if args.preregister else "completed"
        row = build_row(spec, state=state, metrics=metrics)
        if spec["exp_id"] in index:
            prev = rows[index[spec["exp_id"]]]
            if args.preregister and prev.get("state") == "completed":
                print("keep completed", spec["exp_id"])
                continue
            rows[index[spec["exp_id"]]] = row
            print("replace", spec["exp_id"], state)
        else:
            index[spec["exp_id"]] = len(rows)
            rows.append(row)
            print("append", spec["exp_id"], state)
    write_registry(rows)


if __name__ == "__main__":
    main()
