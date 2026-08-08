from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config_loader  # noqa: E402
import phase_s_protocol as protocol  # noqa: E402
import run_strategy_sweep as sweep  # noqa: E402
from generate_phase_s_predictions import prediction_index_sha256  # noqa: E402

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
            "topk": 20,
            "n_drop": 2,
            "hold_thresh": 10,
            "status": "success",
            sweep.IR_KEY: 0.10,
            sweep.ANN_KEY: 0.08,
            sweep.MDD_KEY: -0.15,
            "annualized_one_way_turnover": 8.0,
            "result_dir": "baseline-result",
        },
        {
            "candidate_id": "topk-t10-d1-h10",
            "strategy_class": "TopkDropoutStrategy",
            "topk": 10,
            "n_drop": 1,
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
    assert comparison["winner"]["candidate_id"] == "topk-t10-d1-h10"
    assert comparison["evaluation_mode"] == "full_history_in_sample"
    report = (tmp_path / "COMPARISON.md").read_text(encoding="utf-8")
    assert "full 胜者: `topk-t10-d1-h10`" in report
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
            "topk": 20,
            "n_drop": 2,
            "hold_thresh": 10,
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
                "schema_version": 1,
                "data_version": "2026-07-31",
                "predictions": [
                    {
                        "model_ref": "b6-m",
                        "pool": "csi1000",
                        "segment": "full",
                        "path": str(pred),
                        "prediction_sha256": hashlib.sha256(pred.read_bytes()).hexdigest(),
                        "model_path": str(seed2000),
                        "model_sha256": hashlib.sha256(seed2000.read_bytes()).hexdigest(),
                        "data_version": "2026-07-31",
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
    monkeypatch.setattr(
        sweep, "DEFAULT_FULL_PREDICTION_MANIFEST", manifest, raising=False
    )

    with pytest.raises(ValueError, match="seed-4000"):
        sweep.verify_prediction_contract(
            pred, manifest, model_ref="b6-m", pool="csi1000", segment="full"
        )


def _coverage(frame: pd.DataFrame) -> dict:
    dates = pd.DatetimeIndex(frame.index.get_level_values("datetime"))
    return {
        "start": str(dates.min().date()),
        "end": str(dates.max().date()),
        "n_dates": int(dates.nunique()),
        "n_rows": int(len(frame)),
        "index_sha256": prediction_index_sha256(frame.index),
    }


def _full_prediction_contract_fixture(tmp_path: Path, monkeypatch):
    index = pd.MultiIndex.from_arrays(
        [
            pd.to_datetime(["2020-01-13", "2020-01-14", "2026-07-31"]),
            ["SH600000", "SH600001", "SH600002"],
        ],
        names=["datetime", "instrument"],
    )
    frame = pd.DataFrame({"score": [0.1, 0.2, 0.3]}, index=index)
    pred = tmp_path / "csi1000_full.pkl"
    frame.to_pickle(pred)
    frozen = protocol.load_frozen_model(ROOT, "b6-m")
    entry = {
        "model_ref": "b6-m",
        "pool": "csi1000",
        "segment": "full",
        "path": str(pred),
        "prediction_sha256": hashlib.sha256(pred.read_bytes()).hexdigest(),
        "model_path": str(frozen.model_path),
        "model_sha256": frozen.model_sha256,
        "coverage": _coverage(frame),
        "data_version": "2026-07-31",
    }
    manifest = tmp_path / "prediction_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "data_version": "2026-07-31",
                "predictions": [entry],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sweep, "DEFAULT_FULL_PREDICTION_MANIFEST", manifest, raising=False
    )
    monkeypatch.setattr(
        sweep, "FULL_PREDICTION_COVERAGE", copy.deepcopy(entry["coverage"]), raising=False
    )
    return pred, manifest, frame, entry


def test_full_prediction_contract_validates_the_actual_canonical_dataframe(
    tmp_path, monkeypatch
):
    pred, manifest, _frame, entry = _full_prediction_contract_fixture(
        tmp_path, monkeypatch
    )

    verified = sweep.verify_prediction_contract(
        pred, manifest, model_ref="b6-m", pool="csi1000", segment="full"
    )

    assert verified == entry


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("truncate", "canonical coverage"),
        ("index", "canonical coverage"),
        ("data_version", "data_version"),
    ],
)
def test_full_prediction_contract_rejects_self_consistent_tampering(
    tmp_path, monkeypatch, tamper, message
):
    pred, manifest, frame, entry = _full_prediction_contract_fixture(
        tmp_path, monkeypatch
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if tamper == "truncate":
        changed = frame.iloc[[0, -1]]
    elif tamper == "index":
        changed = frame.copy()
        changed.index = pd.MultiIndex.from_arrays(
            [
                changed.index.get_level_values("datetime"),
                ["SH600000", "SH600999", "SH600002"],
            ],
            names=["datetime", "instrument"],
        )
    else:
        changed = frame
        payload["data_version"] = "2026-07-30"
        payload["predictions"][0]["data_version"] = "2026-07-30"
    changed.to_pickle(pred)
    payload["predictions"][0]["prediction_sha256"] = hashlib.sha256(
        pred.read_bytes()
    ).hexdigest()
    payload["predictions"][0]["coverage"] = _coverage(changed)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
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


def _effective_config_sha(base: dict, candidate: dict) -> str:
    rendered = yaml.safe_dump(
        sweep.build_sweep_config(
            base, candidate, pool="csi1000", segment="full"
        ),
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _resume_summary_fixture(tmp_path: Path, monkeypatch):
    base = sweep.load_config(str(BASE_CONFIG))
    all_candidates = protocol.strategy_grid("b6-m")
    baseline = next(
        row
        for row in all_candidates
        if row["candidate_id"] == protocol.CURRENT_STRATEGY_BASELINE_ID
    )
    retry = next(
        row
        for row in all_candidates
        if row["candidate_id"] != protocol.CURRENT_STRATEGY_BASELINE_ID
    )
    candidates = [copy.deepcopy(baseline), copy.deepcopy(retry)]
    pred = tmp_path / "prediction.pkl"
    pred.write_bytes(b"canonical prediction")
    prediction_sha = hashlib.sha256(pred.read_bytes()).hexdigest()
    manifest = tmp_path / "prediction_manifest.json"
    manifest.write_text('{"predictions": []}\n', encoding="utf-8")
    base_path = Path(base["_config_path"]).resolve()
    payload = {
        "schema_version": 1,
        "model_ref": "b6-m",
        "pool": "csi1000",
        "segment": "full",
        "evaluation_mode": "full_history_in_sample",
        "source_pred": str(pred.resolve()),
        "source_pred_sha256": prediction_sha,
        "base_config": str(base_path),
        "base_config_sha256": hashlib.sha256(base_path.read_bytes()).hexdigest(),
        "requested_candidate_ids": [
            candidate["candidate_id"] for candidate in candidates
        ],
        "all_rows": [
            {
                **copy.deepcopy(baseline),
                "status": "success",
                sweep.IR_KEY: 0.5,
                sweep.ANN_KEY: 0.1,
                sweep.MDD_KEY: -0.2,
                "annualized_one_way_turnover": 5.0,
                "result_dir": "reused-baseline",
                "source_pred_sha256": prediction_sha,
                "effective_config_sha256": _effective_config_sha(base, baseline),
            },
            {
                **copy.deepcopy(retry),
                "status": "failed",
                "error": "retry me",
                "source_pred_sha256": prediction_sha,
                "effective_config_sha256": _effective_config_sha(base, retry),
            },
        ],
    }
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="retry failed")

    monkeypatch.setattr(
        sweep,
        "verify_prediction_contract",
        lambda *args, **kwargs: {"prediction_sha256": prediction_sha},
    )
    monkeypatch.setattr(
        sweep, "strategy_grid", lambda model_ref: copy.deepcopy(candidates)
    )
    monkeypatch.setattr(sweep.subprocess, "run", fake_run)
    return base, pred, manifest, payload, candidates, calls


def _run_resume_main(
    tmp_path: Path,
    pred: Path,
    manifest: Path,
    payload: dict,
) -> tuple[Path, Path, Path]:
    resume = tmp_path / "resume.json"
    resume.write_text(json.dumps(payload), encoding="utf-8")
    output_dir = tmp_path / "output"
    configs_dir = tmp_path / "configs"
    summary = tmp_path / "summary.json"
    sweep.main(
        [
            "--pred",
            str(pred),
            "--prediction-manifest",
            str(manifest),
            "--config",
            str(BASE_CONFIG),
            "--model-ref",
            "b6-m",
            "--pool",
            "csi1000",
            "--segment",
            "full",
            "--resume-summary",
            str(resume),
            "--output-dir",
            str(output_dir),
            "--configs-dir",
            str(configs_dir),
            "--summary-output",
            str(summary),
        ]
    )
    return output_dir, configs_dir, summary


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("model_ref", "model_ref"),
        ("pool", "pool"),
        ("segment", "segment"),
        ("evaluation_mode", "evaluation_mode"),
        ("prediction", "prediction SHA"),
        ("base_config", "base config"),
        ("effective_config", "effective config"),
    ],
)
def test_resume_summary_rejects_mismatched_run_or_candidate_identity(
    tmp_path, monkeypatch, tamper, message
):
    _base_config, pred, manifest, payload, _candidates, calls = (
        _resume_summary_fixture(tmp_path, monkeypatch)
    )
    if tamper == "model_ref":
        payload["model_ref"] = "b1-m"
    elif tamper == "pool":
        payload["pool"] = "csi300"
    elif tamper == "segment":
        payload["segment"] = "valid"
    elif tamper == "evaluation_mode":
        payload["evaluation_mode"] = "historical_audit"
    elif tamper == "prediction":
        payload["source_pred_sha256"] = "0" * 64
    elif tamper == "base_config":
        payload["base_config_sha256"] = "0" * 64
    else:
        payload["all_rows"][0]["effective_config_sha256"] = "0" * 64

    with pytest.raises(ValueError, match=message):
        _run_resume_main(tmp_path, pred, manifest, payload)

    assert calls == []
    assert not (tmp_path / "configs").exists()


def test_resume_summary_reuses_only_rows_with_matching_complete_identity(
    tmp_path, monkeypatch
):
    base, pred, manifest, payload, candidates, calls = _resume_summary_fixture(
        tmp_path, monkeypatch
    )

    _output_dir, configs_dir, summary = _run_resume_main(
        tmp_path, pred, manifest, payload
    )

    assert len(calls) == 1
    assert [path.name for path in configs_dir.iterdir()] == [
        f"{candidates[1]['candidate_id']}_csi1000_full.yaml"
    ]
    result = json.loads(summary.read_text(encoding="utf-8"))
    assert result["model_ref"] == "b6-m"
    assert result["pool"] == "csi1000"
    assert result["segment"] == "full"
    assert result["evaluation_mode"] == "full_history_in_sample"
    assert result["source_pred_sha256"] == payload["source_pred_sha256"]
    assert result["base_config_sha256"] == payload["base_config_sha256"]
    assert result["requested_candidate_ids"] == payload["requested_candidate_ids"]
    reused = next(
        row
        for row in result["all_rows"]
        if row["candidate_id"] == protocol.CURRENT_STRATEGY_BASELINE_ID
    )
    assert reused["result_dir"] == "reused-baseline"
    assert reused["effective_config_sha256"] == _effective_config_sha(
        base, candidates[0]
    )


def test_resume_summary_with_all_matching_successes_runs_no_candidates(
    tmp_path, monkeypatch
):
    _base, pred, manifest, payload, candidates, calls = _resume_summary_fixture(
        tmp_path, monkeypatch
    )
    payload["all_rows"][1].update(
        status="success",
        result_dir="reused-candidate",
        **{
            sweep.IR_KEY: 0.4,
            sweep.ANN_KEY: 0.09,
            sweep.MDD_KEY: -0.21,
            "annualized_one_way_turnover": 5.5,
        },
    )

    _output_dir, configs_dir, summary = _run_resume_main(
        tmp_path, pred, manifest, payload
    )

    assert calls == []
    assert list(configs_dir.iterdir()) == []
    result = json.loads(summary.read_text(encoding="utf-8"))
    assert [row["result_dir"] for row in result["all_rows"]] == [
        "reused-baseline",
        "reused-candidate",
    ]
    assert {row["candidate_id"] for row in result["all_rows"]} == {
        candidate["candidate_id"] for candidate in candidates
    }


def test_resume_summary_rejects_an_omitted_requested_candidate(
    tmp_path, monkeypatch
):
    _base, pred, manifest, payload, _candidates, calls = _resume_summary_fixture(
        tmp_path, monkeypatch
    )
    payload["all_rows"] = payload["all_rows"][:1]

    with pytest.raises(ValueError, match="requested candidate set"):
        _run_resume_main(tmp_path, pred, manifest, payload)

    assert calls == []
    assert not (tmp_path / "configs").exists()
