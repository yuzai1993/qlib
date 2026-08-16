"""登记 / 刷新 M0 改标签评估（Phase M v1 主格 top5×h5）。

默认按 exp_id upsert（替换已有行，不存在则追加）。
``--refresh`` 与默认行为相同，显式重写这 7 行。
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

EXP_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = EXP_ROOT / "backtest" / "result" / "eval_regime_m0_labels"
REG = EXP_ROOT / "backtest" / "experiments" / "registry.jsonl"

BASELINE_ID = "regime-adapt/m0-h20-label-v4"
DETAIL_REPORT = "backtest/experiments/regime_adapt_m0_label_report.html"
HYPOTHESIS = "不同训练标签期限在同一把 top5×h5 尺子下的净年化/波动/夏普"

ARMS = [
    ("regime-adapt/m0-h1-label-v4", "m0h1", 1, "M0 训练标签 H1，主格 top5×h5"),
    ("regime-adapt/m0-h2-label-v4", "m0h2", 2, "M0 训练标签 H2，主格 top5×h5"),
    ("regime-adapt/m0-h3-label-v4", "m0h3", 3, "M0 训练标签 H3，主格 top5×h5"),
    ("regime-adapt/m0-h5-label-v4", "m0h5", 5, "M0 训练标签 H5，主格 top5×h5"),
    ("regime-adapt/m0-h10-label-v4", "m0h10", 10, "M0 训练标签 H10，主格 top5×h5"),
    ("regime-adapt/m0-h20-label-v4", "m0h20", 20, "M0 训练标签 H20，主格 top5×h5"),
    ("regime-adapt/m0-h40-label-v4", "m0h40", 40, "M0 训练标签 H40，主格 top5×h5"),
]

PRIMARY_H = 5


def daily_turnover(prim: dict, h: int = PRIMARY_H):
    """新口径 turnover 已是日换手；旧 JSON 只有 period 且常 >0.5，按 /h 折成日换手。"""
    daily = prim.get("turnover")
    period = prim.get("turnover_period")
    if period is not None:
        return daily if daily is not None else period / h
    if daily is not None and daily > 0.5:
        return daily / h
    return daily


def snap(key: str) -> dict | None:
    path = EVAL_DIR / f"eval_{key}.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    sm = doc["pools"]["all"]["seed_mean"]
    prim = (sm.get("head") or {}).get("5", {}).get("5", {})
    regimes = {}
    for reg, grid in (sm.get("head_regimes") or {}).items():
        regimes[reg] = (grid.get("5") or {}).get("5") or {}
    years = {
        yr: (grid.get("5") or {}).get("5") or {}
        for yr, grid in (sm.get("head_years") or {}).items()
    }
    return {
        "primary_k": 5,
        "primary_h": 5,
        "net_ann_excess": prim.get("net_ann_excess"),
        "net_ann_vol": prim.get("net_ann_vol"),
        "net_sharpe": prim.get("net_sharpe"),
        "ann_excess": prim.get("ann_excess"),
        "turnover": daily_turnover(prim),
        "n_days": prim.get("n_days"),
        "primary_regimes": regimes,
        "primary_years": years,
        "head": sm.get("head"),
        "filters": doc.get("filters"),
    }


def build_row(exp_id: str, key: str, hh: int, note: str, metrics: dict) -> dict:
    return {
        "exp_id": exp_id,
        "direction": "regime-adapt",
        "phase": "M",
        "phase_m_protocol": "v1",
        "date": date.today().isoformat(),
        "state": "completed",
        "arm": f"m0-h{hh}",
        "train_label_horizon": hh,
        "seeds": [42, 1000, 2000, 3000, 4000],
        "hypothesis": HYPOTHESIS,
        "eval_protocol": (
            "allA_top5_h5_net_ann/vol/sharpe | 网格 5/15/50×2/3/5/10 | "
            "上市>=60 + ST + 成交额>=1000万 + 剔t+1涨停"
        ),
        "eval_output": f"backtest/result/eval_regime_m0_labels/eval_{key}.json",
        "detail_report": DETAIL_REPORT,
        "metrics": metrics,
        "note": note,
        "baseline_ref": "self" if exp_id == BASELINE_ID else BASELINE_ID,
        "plan": "backtest/experiments/plans/20260809_regime_adaptation_plan.md",
    }


def load_registry() -> list[dict]:
    if not REG.exists():
        return []
    rows = []
    for line in REG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def write_registry(rows: list[dict]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    REG.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="upsert Phase M v1 M0 改标签 registry 行")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="重写这 7 行（与默认 upsert 相同，显式声明）",
    )
    args = parser.parse_args()
    _ = args.refresh  # 默认即 upsert；--refresh 只作显式别名

    rows = load_registry()
    index_by_id = {row.get("exp_id"): i for i, row in enumerate(rows)}
    n_replace = 0
    n_append = 0
    n_skip = 0
    for exp_id, key, hh, note in ARMS:
        metrics = snap(key)
        if metrics is None:
            print("skip missing", key)
            n_skip += 1
            continue
        row = build_row(exp_id, key, hh, note, metrics)
        if exp_id in index_by_id:
            rows[index_by_id[exp_id]] = row
            n_replace += 1
            print("replace", exp_id)
        else:
            index_by_id[exp_id] = len(rows)
            rows.append(row)
            n_append += 1
            print("append", exp_id)
    write_registry(rows)
    print(f"upserted replace={n_replace} append={n_append} skip={n_skip}")


if __name__ == "__main__":
    main()
