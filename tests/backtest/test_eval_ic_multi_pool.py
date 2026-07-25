from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
    assert args.eval_label == evaluator.EVAL_LABEL_EXPR
    assert args.eval_label_role == "fixed_1d"
    assert args.eval_end is None


def test_cli_requires_self_role_for_custom_evaluation_label():
    with pytest.raises(SystemExit):
        evaluator.parse_args(
            [
                "--config",
                "dummy.yaml",
                "--sessions",
                "session:42",
                "--output",
                "out.json",
                "--eval-label",
                "Ref($close, -21)/Ref($close, -1)-1",
            ]
        )


def test_cli_accepts_self_label_and_common_end():
    args = evaluator.parse_args(
        [
            "--config",
            "dummy.yaml",
            "--sessions",
            "session:42",
            "--output",
            "out.json",
            "--eval-label-role",
            "self",
            "--eval-label",
            "Ref($close, -21)/Ref($close, -1)-1",
            "--eval-end",
            "2026-04-22",
        ]
    )

    assert args.eval_label_role == "self"
    assert args.eval_label == "Ref($close, -21)/Ref($close, -1)-1"
    assert args.eval_end == "2026-04-22"


def test_effective_segment_uses_override_without_mutating_config():
    cfg = {
        "segments": {
            "test": ["2021-07-16", "2026-07-16"],
        }
    }

    assert evaluator._effective_segment(
        cfg, "test", end_override="2026-04-22"
    ) == ("2021-07-16", "2026-04-22")
    assert cfg["segments"]["test"] == ["2021-07-16", "2026-07-16"]


def test_effective_segment_rejects_override_after_official_end():
    cfg = {"segments": {"test": ["2021-07-16", "2026-07-16"]}}

    with pytest.raises(ValueError, match="official segment end"):
        evaluator._effective_segment(
            cfg, "test", end_override="2026-07-17"
        )
