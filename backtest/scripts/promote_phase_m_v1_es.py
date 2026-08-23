"""把 M0 H20 官方评估切到日频 ST，并晋升 M0 H20 ES 为当前 Phase M v1 基线。

只改 registry 行，不重跑评估。历史实验的 baseline_ref 不改写。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from register_regime_m0_labels import H20_ID, load_registry, snap, write_registry

ES_ID = "regime-adapt/m0-h20-t5h5-es-v1"
REEVAL_ID = "regime-adapt/m0-h20-st-daily-reeval"
ES_SESSIONS = [
    f"backtest/result/regimeadaptfast_m0h20_t5h5es_s{s}"
    for s in (42, 1000, 2000, 3000, 4000)
]
H20_SESSIONS = [
    f"backtest/result/regimeadaptfast_m0h20_s{s}"
    for s in (42, 1000, 2000, 3000, 4000)
]


def main() -> None:
    rows = load_registry()
    by_id = {row.get("exp_id"): i for i, row in enumerate(rows)}

    metrics = snap("m0h20")
    if metrics is None:
        raise SystemExit("missing eval_m0h20_st_daily.json")
    if H20_ID not in by_id:
        raise SystemExit(f"missing {H20_ID}")
    v4 = dict(rows[by_id[H20_ID]])
    old_metrics = v4.get("metrics") or {}
    v4["metrics"] = metrics
    v4["eval_output"] = "backtest/result/eval_regime_m0_labels/eval_m0h20_st_daily.json"
    v4["baseline_ref"] = ES_ID
    v4["display_name"] = "M0 H20"
    v4["result_dirs"] = H20_SESSIONS
    v4["note"] = (
        "2026-08-19 官方评估切到日频 ST；"
        "8/16 st_names 对照见 eval_m0h20.json / metrics_st_names"
    )
    v4["metrics_st_names"] = {
        k: old_metrics.get(k)
        for k in (
            "net_ann_excess",
            "net_ann_vol",
            "net_sharpe",
            "ann_excess",
            "turnover",
            "n_days",
        )
    }
    rows[by_id[H20_ID]] = v4
    print("updated", H20_ID, "net_sharpe", metrics.get("net_sharpe"))

    if ES_ID not in by_id:
        raise SystemExit(f"missing {ES_ID}")
    es = dict(rows[by_id[ES_ID]])
    es["display_name"] = "M0 H20 ES"
    es["baseline_ref"] = "self"
    es["result_dirs"] = ES_SESSIONS
    es["note"] = (
        "es_metric=top5_h5_net_ann；2026-08-19 晋升为 Phase M v1 当前基线；"
        "valid=整段评估窗，乐观偏差大于原 499 天 RankIC 早停；"
        "不覆盖 regimeadaptfast_m0h20_s*"
    )
    rows[by_id[ES_ID]] = es
    print("promoted", ES_ID)

    if REEVAL_ID in by_id:
        reeval = dict(rows[by_id[REEVAL_ID]])
        reeval["state"] = "superseded"
        reeval.pop("phase_m_protocol", None)
        reeval["note"] = "已被 v4 日频 ST 切口径吸收；本行 superseded"
        rows[by_id[REEVAL_ID]] = reeval
        print("superseded", REEVAL_ID)

    write_registry(rows)
    print("registry written", len(rows), "rows")


if __name__ == "__main__":
    main()
