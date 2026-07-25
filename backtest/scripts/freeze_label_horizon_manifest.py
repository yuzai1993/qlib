"""Freeze label-horizon candidates from the valid holding diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))

from backtest.label_design.horizons import (  # noqa: E402
    common_self_eval_end,
    cumulative_label,
    select_horizon_anchors,
    survival_weighted_label,
)
from backtest.scripts.config_loader import load_config  # noqa: E402
from backtest.scripts.eval_ic_multi_pool import _init_qlib  # noqa: E402

ANCHORS = (5, 10, 20, 30, 40, 60)
SEEDS = (42, 1000, 2000, 3000, 4000)
TEST_POOLS = ("csi300", "csi500", "csi1000")


def build_manifest(
    diagnostic: dict,
    *,
    calendar,
    diagnostic_sha256: str,
    generated_at: Optional[str] = None,
) -> dict:
    pooled = diagnostic["pooled"]
    quantiles = {key: float(pooled[key]) for key in ("p50", "p75", "p90")}
    horizons = select_horizon_anchors(quantiles, ANCHORS)
    max_horizon = max(horizons)
    survival = {
        int(age): float(probability)
        for age, probability in pooled["survival"].items()
    }
    survival_expr, survival_weights = survival_weighted_label(
        survival,
        max_horizon=max_horizon,
    )

    candidates = []
    for horizon in horizons:
        candidates.append(
            {
                "variant": f"cum-h{horizon}",
                "exp_id": f"label-design/cum-h{horizon}",
                "kind": "cumulative",
                "label_horizon": horizon,
                "label": cumulative_label(horizon),
                "hypothesis": (
                    f"Top30/Drop1 在 valid 段的实际持有期分位数映射到 {horizon} 日；"
                    f"以 t+1 close 为入场价的 {horizon} 日累计收益标签可能降低单日噪声，"
                    "预期固定一日评测 RankIC 不低于 B1，并在自身期限评测中体现可学习性"
                ),
            }
        )
    candidates.append(
        {
            "variant": f"survival-weighted-h{max_horizon}",
            "exp_id": f"label-design/survival-weighted-h{max_horizon}",
            "kind": "survival_weighted",
            "label_horizon": max_horizon,
            "label": survival_expr,
            "weights": {
                str(age): weight
                for age, weight in survival_weights.items()
            },
            "hypothesis": (
                "按 Top30/Drop1 valid 持仓生存概率加权未来逐日收益，使标签权重与持仓"
                "仍然存续的概率一致；预期比单一累计终点更稳健，并保持固定一日 RankIC"
            ),
        }
    )

    return {
        "generated_at": generated_at
        or datetime.now().isoformat(timespec="seconds"),
        "frozen_before_test": True,
        "test_metrics_opened": False,
        "baseline_ref": "B1 v1.0",
        "direction": "label-design",
        "phase": "M",
        "data_version": diagnostic["data_version"],
        "diagnostic_sha256": diagnostic_sha256,
        "holding_strategy": {"topk": 30, "n_drop": 1},
        "holding_quantiles": quantiles,
        "anchor_candidates": list(ANCHORS),
        "selected_horizons": horizons,
        "max_horizon": max_horizon,
        "purge_trading_days": max_horizon + 1,
        "common_self_eval_end": common_self_eval_end(
            calendar,
            official_end="2026-07-16",
            max_horizon=max_horizon,
        ),
        "seeds": list(SEEDS),
        "train_pool": "csi1000",
        "test_pools": list(TEST_POOLS),
        "fixed_eval_label": cumulative_label(1),
        "candidates": candidates,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze label candidates from valid holding diagnostics"
    )
    parser.add_argument("--diagnostic", required=True, type=Path)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    raw = args.diagnostic.read_bytes()
    diagnostic = json.loads(raw.decode("utf-8"))
    cfg = load_config(args.config)
    _init_qlib(cfg)
    from qlib.data import D

    calendar = pd.DatetimeIndex(
        D.calendar(start_time="2020-01-01", end_time="2026-07-16")
    )
    manifest = build_manifest(
        diagnostic,
        calendar=calendar,
        diagnostic_sha256=hashlib.sha256(raw).hexdigest(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
