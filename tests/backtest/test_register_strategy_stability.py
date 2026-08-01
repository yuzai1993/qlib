from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import phase_s_protocol as protocol  # noqa: E402
import register_strategy_stability as register  # noqa: E402


def _manifest(tmp_path: Path, model_ref: str):
    pred = tmp_path / f"{model_ref}.pkl"
    pred.write_bytes(b"full-prediction")
    return {
        "data_version": "2026-07-31",
        "predictions": [
            {
                "model_ref": model_ref,
                "pool": "csi1000",
                "segment": "full",
                "path": str(pred),
                "prediction_sha256": hashlib.sha256(b"full-prediction").hexdigest(),
                "coverage": {
                    "start": "2020-01-13",
                    "end": "2026-07-31",
                    "n_dates": 1587,
                    "n_rows": 1000,
                    "index_sha256": "a" * 64,
                },
            }
        ],
    }


def _result(model_ref: str, pred_sha: str):
    rows = []
    for candidate in protocol.strategy_grid(model_ref):
        rows.append(
            {
                **candidate,
                "status": "success",
                "source_pred_sha256": pred_sha,
                "result_dir": f"backtest/result/{model_ref}-{candidate['candidate_id']}",
                "full_period": {
                    "annualized_return": 0.1,
                    "sharpe_ratio": 1.0,
                    "calmar_ratio": 0.5,
                    "annualized_volatility": 0.2,
                    "max_drawdown": -0.2,
                    "annualized_one_way_turnover": 4.0,
                },
                "years": {},
            }
        )
    return {
        "model_ref": model_ref,
        "pool": "csi1000",
        "segment": "full",
        "period": ["2020-01-13", "2026-07-31"],
        "all_rows": rows,
    }


def test_preregister_and_finalize_diagnostic_without_selection(tmp_path):
    frozen = protocol.load_frozen_model(ROOT, "b1-m")
    manifest = _manifest(tmp_path, "b1-m")
    row = register.build_preregistered_row(frozen, manifest, protocol_path="protocol.json")

    assert row["state"] == "preregistered"
    assert row["conclusion"] == "preregistered"
    assert len(row["strategy_grid"]) == 18
    assert row["cleanup_retention_eligible"] is False
    assert "selected_candidate_id" not in row

    result = tmp_path / "result.json"
    result.write_text(
        json.dumps(_result("b1-m", manifest["predictions"][0]["prediction_sha256"])),
        encoding="utf-8",
    )
    final = register.bind_results(row, result)

    assert final["state"] == "complete"
    assert final["conclusion"] == "diagnostic_no_selection"
    assert len(final["diagnostic_results"]) == 18
    assert "selected_candidate_id" not in final


def test_atomic_upsert_preserves_formal_phase_s_rows(tmp_path):
    registry = tmp_path / "registry.jsonl"
    formal = {"exp_id": "strategy-sweep/b1-m", "state": "test_complete"}
    registry.write_text(json.dumps(formal) + "\n", encoding="utf-8")
    diagnostic = {
        "exp_id": "strategy-stability-full-period/b1-m",
        "state": "preregistered",
    }

    register.upsert_diagnostic_row(registry, diagnostic, expected_previous_state=None)

    rows = [json.loads(line) for line in registry.read_text().splitlines()]
    assert rows[0] == formal
    assert rows[1] == diagnostic
