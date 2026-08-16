"""把头部口径 v3 复评 + 改标签消融写入 registry.jsonl。

口径 v3（2026-08-15）：北极星改为 appraisal_ir（拟合 beta 的残差 IR），
因旧的硬减基准口径把 beta 钉成 1，实测排序退化成 beta 排序（Spearman=+1.00）。
继承 v2：全部测试日 + 剔除 t+1 涨停/停牌 + 无 Hit@k + 含换手与扣费净额。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

EXP_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = EXP_ROOT / "backtest" / "result" / "eval_regime_head_v3"
IR_METRIC = "appraisal_ir"
REG = EXP_ROOT / "backtest" / "experiments" / "registry.jsonl"

KS = ("10", "22", "50")
HS = ("1", "5", "10")

# (exp_id, eval key, arm, 训练标签期限, note)
ARMS = [
    ("regime-adapt/m0-fast-head-eval-v3", "m0fast", "m0-fast", 40, "H40 对照，头部口径 v3 appraisal"),
    ("regime-adapt/m3-fast-head-eval-v3", "m3fast", "m3-fast", 40, "regime 特征+平衡，头部口径 v3"),
    ("regime-adapt/b6m-reference-head-eval-v3", "b6m_ref", "b6m-ref", 40, "现役 B6-M 参考行，头部口径 v3"),
    ("regime-adapt/m0-h1-label-v3", "m0h1", "m0-h1", 1, "m0-fast 配方，训练标签 H1"),
    ("regime-adapt/m0-h5-label-v3", "m0h5", "m0-h5", 5, "m0-fast 配方，训练标签 H5"),
    ("regime-adapt/m0-h10-label-v3", "m0h10", "m0-h10", 10, "m0-fast 配方，训练标签 H10"),
    ("regime-adapt/m3-h5-label-v3", "m3h5", "m3-h5", 5, "m0-h5 + regime 特征 + 风格平衡权重"),
]

BASELINE_REF = {"m3h5": "regime-adapt/m0-h5-label-v3"}


def grid_mean(grid: dict, metric: str, hs=HS) -> float | None:
    vals = [
        (grid.get(k, {}) or {}).get(h, {}).get(metric)
        for k in KS
        for h in hs
    ]
    vals = [v for v in vals if v is not None]
    return float(sum(vals) / len(vals)) if vals else None


def rel_mean(grid: dict, base: dict, hs) -> float | None:
    """同 (k,h) 格跨臂归一后的平均 IR：1.0 = 与同行持平。

    绝对 IR 跨期限不可比（h 越短、重叠越少、IR 越高），比较主客场必须先归一。
    """
    vals = []
    for k in KS:
        for h in hs:
            v, b = (grid.get(k, {}) or {}).get(h, {}).get(IR_METRIC), base.get((k, h))
            if v is not None and b:
                vals.append(v / b)
    return float(sum(vals) / len(vals)) if vals else None


def cell_baseline(grids: list[dict]) -> dict:
    base = {}
    for k in KS:
        for h in HS:
            vals = [(g.get(k, {}) or {}).get(h, {}).get(IR_METRIC) for g in grids]
            vals = [v for v in vals if v is not None]
            if vals:
                base[(k, h)] = sum(vals) / len(vals)
    return base


def snap(key: str, home_h: int, base: dict | None = None) -> dict | None:
    path = EVAL_DIR / f"eval_{key}.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    sm = doc["pools"]["all"]["seed_mean"]
    grid = sm.get("head") or {}
    home_hs = tuple(h for h in HS if int(h) == home_h)
    away_hs = tuple(h for h in HS if int(h) != home_h)
    rel = {}
    if base:
        rel = {
            "ir_home_cells_relative": rel_mean(grid, base, home_hs) if home_hs else None,
            "ir_away_cells_relative": rel_mean(grid, base, away_hs),
            "_home_away_caveat": (
                "ir_home/away_cells 为绝对 IR，跨期限不可比（h 越短 IR 越高）；"
                "比较主客场须用 *_relative（同 (k,h) 格跨臂归一，1.0=持平）"
            ),
        }
    return {
        **rel,
        "north_star_ir": sm.get("north_star_ir"),
        "north_star_ir_std": sm.get("north_star_ir_std"),
        "north_star_ir_regimes": sm.get("north_star_ir_regimes"),
        "ir_by_h": sm.get("ir_by_h"),
        "ir_home_cells": grid_mean(grid, IR_METRIC, home_hs) if home_hs else None,
        "ir_away_cells": grid_mean(grid, IR_METRIC, away_hs),
        "head_beta_grid_mean": grid_mean(grid, "beta"),
        "ann_alpha_grid_mean": grid_mean(grid, "ann_alpha"),
        "net_ann_alpha_grid_mean": grid_mean(grid, "net_ann_alpha"),
        "hard_subtract_ir_grid_mean": grid_mean(grid, "ir"),
        "net_ann_excess_grid_mean": grid_mean(grid, "net_ann_excess"),
        "turnover_grid_mean": grid_mean(grid, "turnover"),
        "h1_rankic": sm.get("h1.rank_ic_mean"),
        "eval_days": (grid.get("22", {}) or {}).get("1", {}).get("n_days"),
        "head_universe": doc.get("head_universe"),
        "head": grid,
        "head_regimes": sm.get("head_regimes"),
    }


def existing_exp_ids() -> set[str]:
    if not REG.exists():
        return set()
    out = set()
    for line in REG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.add(json.loads(line).get("exp_id"))
    return out


def main() -> None:
    already = existing_exp_ids()
    grids = []
    for _, key, _, home_h, _ in ARMS:
        s = snap(key, home_h)
        if s is not None:
            grids.append(s["head"])
    base = cell_baseline(grids)

    rows = []
    for exp_id, key, arm, home_h, note in ARMS:
        if exp_id in already:
            print("skip already registered", exp_id)
            continue
        metrics = snap(key, home_h, base)
        if metrics is None:
            print("skip missing", key)
            continue
        rows.append(
            {
                "exp_id": exp_id,
                "direction": "regime-adapt",
                "phase": "M",
                "date": date.today().isoformat(),
                "state": "completed",
                "arm": arm,
                "train_label_horizon": home_h,
                "seeds": [42, 1000, 2000, 3000, 4000],
                "eval_protocol": (
                    "allA_head_k10_22_50_x_h1_5_10_appraisalIR_mean | 全部测试日 | "
                    "剔除t+1涨停封板与零成交量 | appraisal=对等权全A拟合beta后的残差IR | "
                    "含换手与扣费净alpha"
                ),
                "eval_output": f"backtest/result/eval_regime_head_v3/eval_{key}.json",
                "metrics": metrics,
                "note": note,
                # m3-h5 只改特征+权重，其 baseline 是同标签的 m0-h5（用户 2026-08-15 指定）
                "baseline_ref": BASELINE_REF.get(
                    key, "regime-adapt/m0-fast-head-eval-v3" if key != "m0fast" else "self"
                ),
                "plan": "backtest/experiments/plans/20260809_regime_adaptation_plan.md",
            }
        )
    with REG.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("appended", len(rows), "registry rows")


if __name__ == "__main__":
    main()
