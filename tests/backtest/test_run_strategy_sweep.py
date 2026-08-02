from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config_loader  # noqa: E402
import phase_s_protocol as protocol  # noqa: E402
import run_strategy_sweep as sweep  # noqa: E402

BASE_CONFIG = ROOT / "backtest/configs/train-data/csi1000-full-v2/td_csi1000_full_v2_lgbm_s2000.yaml"


def _base() -> dict:
    return yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))


def test_valid_config_uses_csi1000_dates_500k_and_live_costs():
    candidate = next(
        row
        for row in protocol.strategy_grid("b1-m")
        if row["candidate_id"] == protocol.BASELINE_CANDIDATE_ID
    )

    cfg = sweep.build_sweep_config(
        _base(), candidate, pool="csi1000", segment="valid"
    )

    assert cfg["run"]["mode"] == "pred_backtest"
    assert cfg["segments"]["test"] == ["2020-01-13", "2021-07-15"]
    assert cfg["data"]["instruments"] == "csi1000"
    assert cfg["data"]["benchmark"] == "SH000852"
    assert cfg["backtest"]["account"] == 500000
    assert cfg["backtest"]["exchange_kwargs"] == {
        "freq": "day",
        "deal_price": "close",
        "limit_threshold": 0.095,
        "open_cost": 0.00021,
        "close_cost": 0.00071,
        "min_cost": 5.0,
        "trade_unit": 100,
    }
    assert cfg["strategy"]["kwargs"] == {
        "risk_degree": 0.95,
        "only_tradable": False,
        "forbid_all_trade_at_limit": False,
    }


def test_sweep_config_supports_full_period_selection():
    candidate = protocol.strategy_grid("b6-m")[0]

    config = sweep.build_sweep_config(
        _base(), candidate, pool="csi1000", segment="full"
    )

    assert config["segments"]["test"] == ["2020-01-13", "2026-07-31"]
    assert config["phase_s"]["selection_segment"] == "full"


def test_cli_accepts_full_period_selection_segment():
    args = sweep.parse_args(
        [
            "--pred",
            "prediction.pkl",
            "--prediction-manifest",
            "manifest.json",
            "--config",
            "base.yaml",
            "--model-ref",
            "b6-m",
            "--segment",
            "full",
        ]
    )

    assert args.segment == "full"


def test_cli_defaults_to_full_period_selection_segment():
    args = sweep.parse_args(
        [
            "--pred",
            "prediction.pkl",
            "--prediction-manifest",
            "manifest.json",
            "--config",
            "base.yaml",
            "--model-ref",
            "b6-m",
        ]
    )

    assert args.segment == "full"


def test_full_period_comparison_selects_and_reports_winner(tmp_path):
    rows = [
        {
            "candidate_id": protocol.CURRENT_STRATEGY_BASELINE_ID,
            "strategy_class": "TopkDropoutStrategy",
            "topk": 30,
            "n_drop": 2,
            "hold_thresh": 20,
            "status": "success",
            sweep.IR_KEY: 0.10,
            sweep.ANN_KEY: 0.08,
            sweep.MDD_KEY: -0.15,
            "annualized_one_way_turnover": 8.0,
            "result_dir": "baseline-result",
        },
        {
            "candidate_id": "topk-t20-d2-h10",
            "strategy_class": "TopkDropoutStrategy",
            "topk": 20,
            "n_drop": 2,
            "hold_thresh": 10,
            "status": "success",
            sweep.IR_KEY: 0.20,
            sweep.ANN_KEY: 0.10,
            sweep.MDD_KEY: -0.10,
            "annualized_one_way_turnover": 6.0,
            "result_dir": "winner-result",
        },
    ]

    comparison = sweep.write_comparison(
        tmp_path, rows, model_ref="b6-m", pool="csi1000", segment="full"
    )

    assert comparison["baseline"]["candidate_id"] == protocol.CURRENT_STRATEGY_BASELINE_ID
    assert comparison["winner"]["candidate_id"] == "topk-t20-d2-h10"
    assert comparison["evaluation_mode"] == "full_history_in_sample"
    report = (tmp_path / "COMPARISON.md").read_text(encoding="utf-8")
    assert "full 胜者: `topk-t20-d2-h10`" in report
    assert "evaluation_mode: `full_history_in_sample`" in report
    assert "非样本外检验" in report


@pytest.mark.parametrize(
    ("model_ref", "pool"),
    [("b1-m", "csi1000"), ("b6-m", "csi300")],
)
def test_cli_rejects_full_selection_outside_b6_csi1000(model_ref, pool):
    with pytest.raises(SystemExit):
        sweep.parse_args(
            [
                "--pred",
                "prediction.pkl",
                "--prediction-manifest",
                "manifest.json",
                "--config",
                "base.yaml",
                "--model-ref",
                model_ref,
                "--pool",
                pool,
                "--segment",
                "full",
            ]
        )


def test_full_comparison_rejects_non_b6_csi1000_scope(tmp_path):
    rows = [
        {
            "candidate_id": protocol.CURRENT_STRATEGY_BASELINE_ID,
            "strategy_class": "TopkDropoutStrategy",
            "topk": 30,
            "n_drop": 2,
            "hold_thresh": 20,
            "status": "success",
            sweep.IR_KEY: 0.10,
            sweep.ANN_KEY: 0.08,
            sweep.MDD_KEY: -0.15,
            "annualized_one_way_turnover": 8.0,
            "result_dir": "baseline-result",
        }
    ]

    with pytest.raises(ValueError, match="B6-M / CSI1000"):
        sweep.write_comparison(
            tmp_path, rows, model_ref="b1-m", pool="csi1000", segment="full"
        )


def test_historical_b1_valid_comparison_keeps_its_b1_audit_baseline(tmp_path):
    rows = [
        {
            "candidate_id": protocol.BASELINE_CANDIDATE_ID,
            "strategy_class": "TopkDropoutStrategy",
            "topk": 10,
            "n_drop": 2,
            "hold_thresh": 1,
            "status": "success",
            sweep.IR_KEY: 0.10,
            sweep.ANN_KEY: 0.08,
            sweep.MDD_KEY: -0.15,
            "annualized_one_way_turnover": 8.0,
            "result_dir": "baseline-result",
        }
    ]

    comparison = sweep.write_comparison(
        tmp_path, rows, model_ref="b1-m", pool="csi1000", segment="valid"
    )

    assert comparison["baseline"]["candidate_id"] == protocol.BASELINE_CANDIDATE_ID


def test_full_prediction_contract_requires_b6_seed4000_model_binding(tmp_path, monkeypatch):
    pred = tmp_path / "csi1000_full.pkl"
    pred.write_bytes(b"frozen-prediction")
    seed4000 = tmp_path / "seed4000" / "trained_model"
    seed4000.parent.mkdir()
    seed4000.write_bytes(b"seed4000")
    seed2000 = tmp_path / "seed2000" / "trained_model"
    seed2000.parent.mkdir()
    seed2000.write_bytes(b"seed2000")
    manifest = tmp_path / "prediction_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "predictions": [
                    {
                        "model_ref": "b6-m",
                        "pool": "csi1000",
                        "segment": "full",
                        "path": str(pred),
                        "prediction_sha256": hashlib.sha256(pred.read_bytes()).hexdigest(),
                        "model_path": str(seed2000),
                        "model_sha256": hashlib.sha256(seed2000.read_bytes()).hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sweep,
        "load_frozen_model",
        lambda _root, _model_ref: SimpleNamespace(
            model_path=seed4000,
            model_sha256=hashlib.sha256(seed4000.read_bytes()).hexdigest(),
        ),
        raising=False,
    )

    with pytest.raises(ValueError, match="seed-4000"):
        sweep.verify_prediction_contract(
            pred, manifest, model_ref="b6-m", pool="csi1000", segment="full"
        )


def test_topk_config_uses_candidate_risk_degree_when_preregistered():
    candidate = {
        "candidate_id": "topk-t30-d2-h20-r090",
        "strategy_class": "TopkDropoutStrategy",
        "topk": 30,
        "n_drop": 2,
        "hold_thresh": 20,
        "risk_degree": 0.90,
    }

    cfg = sweep.build_sweep_config(
        _base(), candidate, pool="csi1000", segment="valid"
    )

    assert cfg["strategy"]["kwargs"]["risk_degree"] == 0.90


def test_soft_topk_config_uses_precomputed_absolute_impact_limit():
    candidate = next(
        row
        for row in protocol.strategy_grid("b6-m")
        if row["candidate_id"] == "soft-t20-i050"
    )

    cfg = sweep.build_sweep_config(
        _base(), candidate, pool="csi1000", segment="valid"
    )

    assert cfg["strategy"] == {
        "class": "SoftTopkStrategy",
        "module_path": "qlib.contrib.strategy.cost_control",
        "topk": 20,
        "kwargs": {
            "trade_impact_limit": pytest.approx(0.02375),
            "risk_degree": 0.95,
        },
    }


def test_pred_backtest_mode_is_accepted_and_aligned(tmp_path):
    candidate = protocol.strategy_grid("b1-m")[0]
    cfg = sweep.build_sweep_config(
        _base(), candidate, pool="csi1000", segment="valid"
    )
    path = tmp_path / "candidate.yaml"
    path.write_text(yaml.safe_dump(copy.deepcopy(cfg)), encoding="utf-8")

    loaded = config_loader.load_config(str(path))

    assert loaded["run"]["mode"] == "pred_backtest"
    assert loaded["backtest"]["start_time"] == "2020-01-13"
    assert loaded["backtest"]["end_time"] == "2021-07-15"


def test_backtest_command_references_frozen_prediction_without_copy(tmp_path):
    command = sweep.build_backtest_command(
        Path(sys.executable),
        ROOT / "backtest/scripts/run_pred_backtest.py",
        tmp_path / "pred.pkl",
        tmp_path / "candidate.yaml",
        "valid-b1",
    )

    assert command == [
        sys.executable,
        str(ROOT / "backtest/scripts/run_pred_backtest.py"),
        "--pred",
        str(tmp_path / "pred.pkl"),
        "--config",
        str(tmp_path / "candidate.yaml"),
        "--note",
        "valid-b1",
        "--skip-pred-copy",
    ]


def test_merge_retry_rows_preserves_failed_attempt_and_original_order():
    existing = [
        {"candidate_id": "baseline", "status": "success", "score": 1},
        {"candidate_id": "soft", "status": "failed", "error": "bad price"},
    ]
    retry = [{"candidate_id": "soft", "status": "success", "score": 2}]

    merged = sweep.merge_retry_rows(existing, retry)

    assert [row["candidate_id"] for row in merged] == ["baseline", "soft"]
    assert merged[1]["status"] == "success"
    assert merged[1]["previous_attempts"] == [
        {"status": "failed", "error": "bad price"}
    ]


def test_non_finite_success_is_reclassified_as_invalid():
    row = {
        "status": "success",
        sweep.IR_KEY: math.nan,
        sweep.ANN_KEY: 0.0,
        sweep.MDD_KEY: -0.1,
        "annualized_one_way_turnover": 2.0,
    }

    sweep.classify_strategy_outcome(row)

    assert row["status"] == "invalid"
    assert "non-finite" in row["error"]


def test_verify_prediction_contract_enforces_path_and_sha(tmp_path):
    pred = tmp_path / "csi1000_valid.pkl"
    pred.write_bytes(b"frozen")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "predictions": [
                    {
                        "model_ref": "b1-m",
                        "pool": "csi1000",
                        "segment": "valid",
                        "path": str(pred),
                        "prediction_sha256": hashlib.sha256(b"frozen").hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    entry = sweep.verify_prediction_contract(
        pred, manifest, model_ref="b1-m", pool="csi1000", segment="valid"
    )
    assert entry["prediction_sha256"] == hashlib.sha256(b"frozen").hexdigest()

    pred.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA"):
        sweep.verify_prediction_contract(
            pred, manifest, model_ref="b1-m", pool="csi1000", segment="valid"
        )
