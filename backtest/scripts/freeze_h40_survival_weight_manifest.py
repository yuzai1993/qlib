"""Freeze the adaptive H40 survival-power label family from valid diagnostics."""

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
    survival_power_weighted_label,
)
from backtest.scripts.config_loader import load_config  # noqa: E402
from backtest.scripts.eval_ic_multi_pool import _init_qlib  # noqa: E402

HORIZON = 40
POWERS = (0.5, 1.0, 2.0)
SEEDS = (42, 1000, 2000, 3000, 4000)
TEST_POOLS = ("csi1000", "csi300", "csi500")
POWER_NAMES = {0.5: "p05", 1.0: "p10", 2.0: "p20"}
HYPOTHESES = {
    0.5: (
        "Top30/Drop1 valid 生存率的平方根形成较平缓的 H40 衰减，"
        "保留更多中后段收益信息，预期提高 CSI1000 固定一日 RankIC"
    ),
    1.0: (
        "直接按 Top30/Drop1 valid 持仓生存概率加权 H40 未来逐日收益，"
        "预期在 CSI1000 固定一日 RankIC 与 RankICIR 之间取得平衡"
    ),
    2.0: (
        "Top30/Drop1 valid 生存率平方形成更前置的 H40 衰减，"
        "降低远期收益噪声，预期提高 CSI1000 固定一日 RankICIR"
    ),
}


def build_manifest(
    diagnostic: dict,
    *,
    calendar,
    diagnostic_sha256: str,
    generated_at: Optional[str] = None,
) -> dict:
    survival = {
        int(age): float(probability)
        for age, probability in diagnostic["pooled"]["survival"].items()
    }
    candidates = []
    for power in POWERS:
        expression, weights = survival_power_weighted_label(
            survival,
            max_horizon=HORIZON,
            power=power,
        )
        variant = f"survival-{POWER_NAMES[power]}-h{HORIZON}"
        candidates.append(
            {
                "variant": variant,
                "exp_id": f"label-design/{variant}",
                "kind": "survival_power_weighted",
                "label_horizon": HORIZON,
                "power": power,
                "label": expression,
                "weights": {
                    str(age): weight
                    for age, weight in weights.items()
                },
                "hypothesis": HYPOTHESES[power],
            }
        )

    return {
        "generated_at": generated_at
        or datetime.now().isoformat(timespec="seconds"),
        "frozen_before_test": True,
        "test_metrics_opened": False,
        "adaptive_followup": True,
        "adaptive_context": (
            "候选族由上一阶段 test 中 survival-weighted H60 相对累计 H60 "
            "在 CSI1000 的表现启发；权重仅由 valid 持仓诊断生成"
        ),
        "baseline_ref": "B1 v1.0",
        "direction": "label-design",
        "phase": "M",
        "data_version": diagnostic["data_version"],
        "diagnostic_sha256": diagnostic_sha256,
        "holding_strategy": {"topk": 30, "n_drop": 1},
        "primary_test_pool": "csi1000",
        "test_pools": list(TEST_POOLS),
        "selection_rule": [
            "csi1000.rank_ic_mean",
            "csi1000.rank_icir",
            "mean_three_pool_rank_ic_delta",
        ],
        "label_horizon": HORIZON,
        "purge_trading_days": HORIZON + 1,
        "common_self_eval_end": common_self_eval_end(
            calendar,
            official_end="2026-07-16",
            max_horizon=HORIZON,
        ),
        "seeds": list(SEEDS),
        "train_pool": "csi1000",
        "fixed_eval_label": cumulative_label(1),
        "candidates": candidates,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze adaptive H40 survival-power label candidates"
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
