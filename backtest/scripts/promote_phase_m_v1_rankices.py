"""把 v3 + RankIC 早停的官方合成信号晋升为 Phase M v1 模型基线 v4。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_phase_m_v1_report import PRIMARY_H, PRIMARY_K
from register_regime_m0_labels import load_registry, write_registry

EXP_ROOT = Path(__file__).resolve().parents[2]
V3_ID = "regime-adapt/m0-h20-t3h5es-v1"
V4_ID = "regime-adapt/m0-h20-rankices-v1"
EVAL = "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json"


PROMO_NOTE = (
    "2026-08-22 用户批准晋升为 Phase M v1 当前模型基线 v4。"
    "早停=评估窗 daily_rank_ic（valid_frame_t5h5es.pkl，H5 标签只作 RankIC 的 y）；"
    "不是 v1 的 499 天次日 RankIC。主格仍是 top3×h5。"
)


def _promotion_note(prev: str) -> str:
    if PROMO_NOTE in prev:
        return prev
    return f"{prev}；{PROMO_NOTE}" if prev else PROMO_NOTE


def official_metrics(rel: str) -> dict:
    doc = json.loads((EXP_ROOT / rel).read_text())
    sm = (doc.get("pools") or {}).get("all", {})
    ens = sm.get("ensemble") or sm.get("seed_mean") or {}
    prim = ((ens.get("head") or {}).get(PRIMARY_K) or {}).get(PRIMARY_H) or {}
    if prim.get("net_ann") is None:
        raise SystemExit(f"{rel} missing primary {PRIMARY_K}x{PRIMARY_H}")
    ric = (ens.get("h5") or ens.get("mean_h") or {}).get("rank_ic_mean")
    return {
        "primary_k": int(PRIMARY_K),
        "primary_h": int(PRIMARY_H),
        "net_ann": prim.get("net_ann"),
        "net_ann_vol": prim.get("net_ann_vol"),
        "net_sharpe": prim.get("net_sharpe"),
        "ann": prim.get("ann"),
        "turnover": prim.get("turnover"),
        "n_days": prim.get("n_days"),
        "rank_ic_mean": ric,
        "primary_years": {
            yr: ((grid.get(PRIMARY_K) or {}).get(PRIMARY_H) or {})
            for yr, grid in (ens.get("head_years") or {}).items()
        },
        "primary_regimes": {
            reg: ((grid.get(PRIMARY_K) or {}).get(PRIMARY_H) or {})
            for reg, grid in (ens.get("head_regimes") or {}).items()
        },
        "head": ens.get("head"),
    }


def main() -> None:
    rows = load_registry()
    by_id = {r.get("exp_id"): i for i, r in enumerate(rows)}
    if V4_ID not in by_id:
        raise SystemExit(f"missing {V4_ID}")
    row = dict(rows[by_id[V4_ID]])
    row.update(
        {
            "date": date.today().isoformat(),
            "state": "completed",
            "display_name": "M0 H20 RankIC ES",
            "eval_output": EVAL,
            "baseline_ref": "self",
            "baseline_version": "v4",
            "metrics": official_metrics(EVAL),
            "note": _promotion_note(row.get("note") or ""),
        }
    )
    rows[by_id[V4_ID]] = row
    if V3_ID in by_id:
        v3 = dict(rows[by_id[V3_ID]])
        v3["baseline_version"] = "v3"
        rows[by_id[V3_ID]] = v3
    write_registry(rows)
    m = row["metrics"]
    print(
        f"promoted {V4_ID} → v4 "
        f"net_ann={m['net_ann']:+.4f} ric={m.get('rank_ic_mean')}"
    )


if __name__ == "__main__":
    main()
