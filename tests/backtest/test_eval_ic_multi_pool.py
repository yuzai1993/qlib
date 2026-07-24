from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import eval_ic_multi_pool as evaluator  # noqa: E402


def test_segment_bounds_uses_requested_valid_window():
    cfg = {
        "segments": {
            "valid": ["2020-01-13", "2021-07-15"],
            "test": ["2021-07-16", "2026-07-16"],
        }
    }

    assert evaluator._segment_bounds(cfg, "valid") == (
        "2020-01-13",
        "2021-07-15",
    )


def test_cli_accepts_valid_segment():
    args = evaluator.parse_args(
        [
            "--config",
            "dummy.yaml",
            "--sessions",
            "session:42",
            "--pools",
            "csi1000",
            "--segment",
            "valid",
            "--output",
            "out.json",
        ]
    )

    assert args.segment == "valid"

