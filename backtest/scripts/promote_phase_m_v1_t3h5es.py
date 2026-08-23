"""登记 H1/H5/H10/H20 × top3×h5 早停评估，并把净年化最高者晋升为 v3。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_phase_m_v1_report import PRIMARY_H, PRIMARY_K
from register_regime_m0_labels import load_registry, write_registry

EXP_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = EXP_ROOT / "backtest" / "result" / "eval_regime_m0_t3h5es"
DETAIL_REPORT = "backtest/experiments/regime_adapt_m0_label_report.html"
V1_ID = "regime-adapt/m0-h20-label-v4"
V2_ID = "regime-adapt/m0-h20-t5h5-es-v1"
HORIZONS = (1, 5, 10, 20)
SEEDS = (42, 1000, 2000, 3000, 4000)


def _official(path: Path) -> dict:
    doc = json.loads(path.read_text())
    pool = doc["pools"]["all"]
    return pool.get("ensemble") or pool.get("seed_mean") or {}


def snap_arm(hh: int) -> dict:
    path = EVAL_DIR / f"eval_m0h{hh}.json"
    if not path.exists():
        raise SystemExit(f"missing {path}")
    sm = _official(path)
    prim = ((sm.get("head") or {}).get(PRIMARY_K) or {}).get(PRIMARY_H) or {}
    if prim.get("net_ann") is None:
        raise SystemExit(f"{path} missing primary {PRIMARY_K}x{PRIMARY_H}")
    years = {
        yr: ((grid.get(PRIMARY_K) or {}).get(PRIMARY_H) or {})
        for yr, grid in (sm.get("head_years") or {}).items()
    }
    regimes = {
        reg: ((grid.get(PRIMARY_K) or {}).get(PRIMARY_H) or {})
        for reg, grid in (sm.get("head_regimes") or {}).items()
    }
    return {
        "primary_k": int(PRIMARY_K),
        "primary_h": int(PRIMARY_H),
        "net_ann": prim.get("net_ann"),
        "net_ann_vol": prim.get("net_ann_vol"),
        "net_sharpe": prim.get("net_sharpe"),
        "ann": prim.get("ann"),
        "turnover": prim.get("turnover"),
        "n_days": prim.get("n_days"),
        "primary_years": years,
        "primary_regimes": regimes,
        "head": sm.get("head"),
        "eval_path": str(path.relative_to(EXP_ROOT)),
    }


def build_row(hh: int, metrics: dict, *, winner: bool) -> dict:
    exp_id = f"regime-adapt/m0-h{hh}-t3h5es-v1"
    sessions = [f"backtest/result/regimeadaptfast_m0h{hh}_t3h5es_s{s}" for s in SEEDS]
    return {
        "exp_id": exp_id,
        "direction": "regime-adapt",
        "phase": "M",
        "phase_m_protocol": "v1",
        "date": date.today().isoformat(),
        "state": "completed",
        "arm": f"m0-h{hh}-t3h5es",
        "display_name": f"M0 H{hh} t3h5es",
        "train_label_horizon": hh,
        "seeds": list(SEEDS),
        "hypothesis": (
            "官方主格与早停改为 top3×h5 后，比较训练标签 H1/H5/H10/H20 的扣费净年化"
        ),
        "eval_protocol": (
            "allA_top3_h5_net_ann/vol/sharpe | 官方=五种子zscore均值信号一次评估 | "
            "网格 1/2/3/4/5×2/3/4/5 | 上市>=60 + 日频ST + 成交额>=1000万 + 剔t+1涨停"
        ),
        "eval_output": metrics["eval_path"],
        "detail_report": DETAIL_REPORT,
        "metrics": {k: v for k, v in metrics.items() if k != "eval_path"},
        "note": (
            f"es_metric=top3_h5_net_ann；主格 top3×h5 净年化最优，晋升为 v3"
            if winner
            else "es_metric=top3_h5_net_ann；同批标签对照，未晋升"
        ),
        "baseline_ref": "self" if winner else f"regime-adapt/m0-h{winner_horizon()}-t3h5es-v1",
        "baseline_version": "v3" if winner else None,
        "result_dirs": sessions,
        "plan": "backtest/experiments/plans/20260809_regime_adaptation_plan.md",
    }


_WINNER_H = None


def winner_horizon() -> int:
    if _WINNER_H is None:
        raise RuntimeError("winner not chosen")
    return _WINNER_H


def upsert(rows: list[dict], row: dict) -> None:
    idx = {r.get("exp_id"): i for i, r in enumerate(rows)}
    eid = row["exp_id"]
    if row.get("baseline_version") is None:
        row.pop("baseline_version", None)
    if eid in idx:
        rows[idx[eid]] = row
        print("replace", eid)
    else:
        rows.append(row)
        print("append", eid)


def _primary_metrics(rel: str) -> dict:
    sm = _official(EXP_ROOT / rel)
    prim = ((sm.get("head") or {}).get(PRIMARY_K) or {}).get(PRIMARY_H) or {}
    return {
        "primary_k": int(PRIMARY_K),
        "primary_h": int(PRIMARY_H),
        "net_ann": prim.get("net_ann"),
        "net_ann_vol": prim.get("net_ann_vol"),
        "net_sharpe": prim.get("net_sharpe"),
        "ann": prim.get("ann"),
        "turnover": prim.get("turnover"),
        "n_days": prim.get("n_days"),
        "primary_years": {
            yr: ((grid.get(PRIMARY_K) or {}).get(PRIMARY_H) or {})
            for yr, grid in (sm.get("head_years") or {}).items()
        },
        "primary_regimes": {
            reg: ((grid.get(PRIMARY_K) or {}).get(PRIMARY_H) or {})
            for reg, grid in (sm.get("head_regimes") or {}).items()
        },
        "head": sm.get("head"),
    }


def retarget_historical(rows: list[dict]) -> None:
    """历史 v1/v2 改读已有 k=1..5 网格 JSON，使总报告主格变成 top3×h5。"""
    by_id = {r.get("exp_id"): i for i, r in enumerate(rows)}
    mapping = {
        V1_ID: "backtest/result/eval_regime_m0_labels/eval_m0h20_k123h2345.json",
        V2_ID: "backtest/result/eval_regime_m0_labels/eval_m0h20es_k123h2345.json",
    }
    for eid, rel in mapping.items():
        if eid not in by_id:
            continue
        row = dict(rows[by_id[eid]])
        row["eval_output"] = rel
        metrics = dict(row.get("metrics") or {})
        metrics.update(_primary_metrics(rel))
        row["metrics"] = metrics
        if eid == V2_ID:
            row["note"] = (
                (row.get("note") or "")
                + "；2026-08-22 官方主格改为 top3×h5，本行保留为历史 v2，数字改读新主格"
            )
        rows[by_id[eid]] = row
        print("retarget", eid, "->", rel)


def main() -> None:
    global _WINNER_H
    snaps = {hh: snap_arm(hh) for hh in HORIZONS}
    ranked = sorted(snaps.items(), key=lambda kv: kv[1]["net_ann"], reverse=True)
    _WINNER_H, win = ranked[0]
    print("ranking top3xh5 net_ann:")
    for hh, m in ranked:
        mark = " WIN" if hh == _WINNER_H else ""
        print(
            f"  H{hh}: net_ann={m['net_ann']:+.4f} vol={m['net_ann_vol']:.4f} "
            f"sharpe={m['net_sharpe']:.3f}{mark}"
        )

    rows = load_registry()
    retarget_historical(rows)
    for hh, metrics in snaps.items():
        upsert(rows, build_row(hh, metrics, winner=(hh == _WINNER_H)))
    # v2 不再是 current：只留 baseline_version，不改 historical baseline_ref
    by_id = {r.get("exp_id"): i for i, r in enumerate(rows)}
    if V2_ID in by_id:
        v2 = dict(rows[by_id[V2_ID]])
        v2["baseline_version"] = "v2"
        rows[by_id[V2_ID]] = v2
    write_registry(rows)

    winner_id = f"regime-adapt/m0-h{_WINNER_H}-t3h5es-v1"
    winner_path = EVAL_DIR / "v3_winner.json"
    winner_path.write_text(
        json.dumps(
            {
                "exp_id": winner_id,
                "horizon": _WINNER_H,
                "display_name": f"M0 H{_WINNER_H} t3h5es",
                "metrics": {
                    k: win[k]
                    for k in (
                        "net_ann",
                        "net_ann_vol",
                        "net_sharpe",
                        "ann",
                        "turnover",
                        "n_days",
                    )
                },
                "eval_path": win["eval_path"],
                "ranking": [
                    {
                        "horizon": hh,
                        "net_ann": m["net_ann"],
                        "net_ann_vol": m["net_ann_vol"],
                        "net_sharpe": m["net_sharpe"],
                    }
                    for hh, m in ranked
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print("wrote", winner_path.relative_to(EXP_ROOT))
    print("winner", winner_id, "net_ann", win["net_ann"])


if __name__ == "__main__":
    main()
