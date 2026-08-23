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

BASELINE_ID = "regime-adapt/m0-h20-t5h5-es-v1"
H20_ID = "regime-adapt/m0-h20-label-v4"
EVAL_FILES = {"m0h20": "eval_m0h20_st_daily.json"}
DETAIL_REPORT = "backtest/experiments/regime_adapt_m0_label_report.html"
HYPOTHESIS = "不同训练标签期限在同一把 top5×h5 尺子下的净年化/波动/夏普"
GRID_ID = "regime-adapt/m0-label-k123-h2345"
GRID_FILES = {
    "m0h20es": "eval_m0h20es_k123h2345.json",
    "m0h20": "eval_m0h20_k123h2345.json",
}
GRID_HYPOTHESIS = (
    "在更小持仓 k∈{1,2,3,4,5} 与更短持有 h∈{2,3,4,5} 上看 M0 H20 ES / M0 H20 "
    "的扣费净年化、波动、夏普；不改官方主格 top5×h5"
)

# 2026-08-19 起总报告只保留 baseline 版本；H1/H2/H3/H5/H10/H40 已归档，禁止再 upsert。
ARMS = [
    ("regime-adapt/m0-h20-label-v4", "m0h20", 20, "M0 训练标签 H20，主格 top5×h5"),
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


def eval_filename(key: str) -> str:
    return EVAL_FILES.get(key, f"eval_{key}.json")


def snap(key: str) -> dict | None:
    path = EVAL_DIR / eval_filename(key)
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    pool = doc["pools"]["all"]
    sm = pool.get("ensemble") or pool.get("seed_mean") or {}
    prim = (sm.get("head") or {}).get("3", {}).get("5", {})
    regimes = {}
    for reg, grid in (sm.get("head_regimes") or {}).items():
        regimes[reg] = (grid.get("3") or {}).get("5") or {}
    years = {
        yr: (grid.get("3") or {}).get("5") or {}
        for yr, grid in (sm.get("head_years") or {}).items()
    }
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
        "primary_regimes": regimes,
        "primary_years": years,
        "head": sm.get("head"),
        "filters": doc.get("filters"),
        "official_signal": doc.get("official_signal"),
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
            "allA_top3_h5_net_ann/vol/sharpe | 官方=五种子zscore均值信号一次评估 | "
            "网格 5/15/50×2/3/5/10 | 上市>=60 + 日频ST + 成交额>=1000万 + 剔t+1涨停"
        ),
        "eval_output": f"backtest/result/eval_regime_m0_labels/{eval_filename(key)}",
        "detail_report": DETAIL_REPORT,
        "metrics": metrics,
        "note": (
            "官方评估：日频 ST + 五种子 z-score 等权合成后再算 top5×h5"
            "（eval_m0h20_st_daily.json）；"
            "eval_m0h20.json 保留 8/16 st_names 对照；"
            "seed_mean 只作稳健性"
            if exp_id == H20_ID
            else note
        ),
        "display_name": "M0 H20",
        "baseline_ref": BASELINE_ID if exp_id == H20_ID else H20_ID,
        "baseline_version": "v1",
        "result_dirs": [
            f"backtest/result/regimeadaptfast_m0h20_s{s}"
            for s in (42, 1000, 2000, 3000, 4000)
        ],
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


def snap_k123_grid() -> dict | None:
    arms: dict[str, dict] = {}
    for key, fname in GRID_FILES.items():
        path = EVAL_DIR / fname
        if not path.exists():
            return None
        doc = json.loads(path.read_text())
        pool = doc["pools"]["all"]
        sm = pool.get("ensemble") or pool.get("seed_mean") or {}
        arms[key] = {
            "head": sm.get("head"),
            "head_years": sm.get("head_years"),
            "head_regimes": sm.get("head_regimes"),
            "filters": doc.get("filters"),
            "official_signal": doc.get("official_signal"),
            "n_days": ((sm.get("head") or {}).get("1") or {}).get("5", {}).get("n_days"),
        }
    return {
        "grid_k": [1, 2, 3, 4, 5],
        "grid_h": [2, 3, 4, 5],
        "arms": arms,
    }


def build_grid_row(metrics: dict) -> dict:
    return {
        "exp_id": GRID_ID,
        "direction": "regime-adapt",
        "phase": "M",
        "phase_m_protocol": "v1",
        "date": date.today().isoformat(),
        "state": "completed",
        "arm": "m0-h20",
        "display_name": "M0 标签网格 k1-5×h2345",
        "train_label_horizon": 20,
        "seeds": [42, 1000, 2000, 3000, 4000],
        "hypothesis": GRID_HYPOTHESIS,
        "eval_protocol": (
            "allA_k1to5_h2345_net_ann/vol/sharpe | 官方=五种子zscore均值信号一次评估 | "
            "网格 1/2/3/4/5×2/3/4/5 | 上市>=60 + 日频ST + 成交额>=1000万 + 剔t+1涨停 | "
            "不覆盖官方 top5×h5 JSON"
        ),
        "eval_output": f"backtest/result/eval_regime_m0_labels/{GRID_FILES['m0h20']}",
        "detail_report": DETAIL_REPORT,
        "metrics": metrics,
        "note": (
            "本轮只重评仍在的 M0 H20 ES / M0 H20；"
            "不改写 m0-h20-label-v4 / m0-h20-t5h5-es-v1 的官方 top5×h5 数字"
        ),
        "baseline_ref": BASELINE_ID,
        "result_dirs": [
            f"backtest/result/regimeadaptfast_m0h20_t5h5es_s{s}"
            for s in (42, 1000, 2000, 3000, 4000)
        ]
        + [
            f"backtest/result/regimeadaptfast_m0h20_s{s}"
            for s in (42, 1000, 2000, 3000, 4000)
        ],
        "plan": "backtest/experiments/plans/20260809_regime_adaptation_plan.md",
    }


def upsert_grid_row() -> None:
    metrics = snap_k123_grid()
    if metrics is None:
        raise SystemExit(f"missing {GRID_FILES} under {EVAL_DIR}")
    rows = load_registry()
    index_by_id = {row.get("exp_id"): i for i, row in enumerate(rows)}
    row = build_grid_row(metrics)
    if GRID_ID in index_by_id:
        rows[index_by_id[GRID_ID]] = row
        print("replace", GRID_ID)
    else:
        rows.append(row)
        print("append", GRID_ID)
    write_registry(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="upsert Phase M v1 M0 改标签 registry 行")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="重写这 7 行（与默认 upsert 相同，显式声明）",
    )
    parser.add_argument(
        "--grid",
        choices=("k123h2345",),
        default=None,
        help="只登记小 k/短 h 网格重评，不改写官方 top5×h5 行",
    )
    args = parser.parse_args()
    if args.grid == "k123h2345":
        upsert_grid_row()
        return
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
            prev = rows[index_by_id[exp_id]]
            if prev.get("metrics_st_names") and "metrics_st_names" not in row:
                row["metrics_st_names"] = prev["metrics_st_names"]
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
