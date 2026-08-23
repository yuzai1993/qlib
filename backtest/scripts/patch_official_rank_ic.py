"""给已有 Phase M v1 eval JSON 补上官方合成信号的全宇宙 RankIC。

不重跑头部网格。标签过滤与 eval_ic_multi_pool 同一套。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(SCRIPTS))

from config_loader import load_config  # noqa: E402
from ensemble_preds import _as_score_series  # noqa: E402
from eval_ic_multi_pool import (  # noqa: E402
    DEFAULT_ST_DAILY,
    _init_qlib,
    label_window_cutoff,
    official_ic_from_pred,
    prepare_pool_labels,
    require_st_daily,
)

SOURCES = (
    {
        "eval": "backtest/result/eval_regime_m0_labels/eval_m0h20_k123h2345.json",
        "pred": "backtest/result/phase_s_regime/all_top5d1/m0h20_ensemble_pred.pkl",
    },
    {
        "eval": "backtest/result/eval_regime_m0_labels/eval_m0h20es_k123h2345.json",
        "pred": "backtest/result/phase_s_regime/all_top5d1/m0h20es_ensemble_pred.pkl",
    },
    {
        "eval": "backtest/result/eval_regime_m0_t3h5es/eval_m0h20.json",
        "pred": "backtest/result/phase_s_regime/preds/m0h20t3h5es_ensemble_pred.pkl",
    },
    {
        "eval": "backtest/result/eval_regime_ablation/eval_m0h20_rankices.json",
        "pred": "backtest/result/phase_s_regime/preds/m0h20rankices_ensemble_pred.pkl",
    },
)


def _load_pred(path: Path) -> pd.Series:
    raw = pd.read_pickle(path)
    if isinstance(raw, pd.DataFrame):
        raw = raw.iloc[:, 0]
    return _as_score_series(raw, path)


def main(argv=None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="backtest/configs/regime-adapt/eval_m0fast.yaml")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    _init_qlib(cfg)
    from qlib.data import D

    eval_start, eval_end = "2020-08-03", "2026-07-31"
    calendar = pd.DatetimeIndex(D.calendar(start_time=eval_start, end_time=eval_end))
    st_daily = require_st_daily(DEFAULT_ST_DAILY)
    labels_by_key: dict[tuple[int, ...], dict[int, pd.Series]] = {}

    for spec in SOURCES:
        eval_path = QLIB_ROOT / spec["eval"]
        pred_path = QLIB_ROOT / spec["pred"]
        if not eval_path.is_file():
            print("skip missing eval", spec["eval"], flush=True)
            continue
        if not pred_path.is_file():
            print("skip missing pred", spec["pred"], flush=True)
            continue
        doc = json.loads(eval_path.read_text())
        horizons = tuple(int(h) for h in doc.get("horizons") or [5])
        if horizons not in labels_by_key:
            cutoffs = {h: label_window_cutoff(calendar, h) for h in horizons}
            labels_by_key[horizons] = prepare_pool_labels(
                "all",
                eval_start,
                eval_end,
                horizons,
                cutoffs,
                min_listing_days=60,
                st_daily=st_daily,
                min_amount=10_000_000,
            )
        ic = official_ic_from_pred(
            _load_pred(pred_path), labels_by_key[horizons], horizons
        )
        pool = doc.setdefault("pools", {}).setdefault("all", {})
        ens = pool.setdefault("ensemble", {})
        ens.update(ic)
        eval_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        h5 = (ic.get("h5") or {}).get("rank_ic_mean")
        print(f"patched {spec['eval']} h5.rank_ic_mean={h5}", flush=True)


if __name__ == "__main__":
    main()
