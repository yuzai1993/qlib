from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))

import finalize_strategy_neighborhood as finalizer  # noqa: E402
import run_strategy_neighborhood as runner  # noqa: E402
import strategy_neighborhood_protocol as protocol  # noqa: E402


def _metrics(candidate_id: str, ir: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "status": "success",
        "neighbor_ir_p25": ir - 0.1,
        "excess_with_cost_information_ratio": ir,
        "excess_with_cost_annualized_return": 0.20,
        "excess_with_cost_max_drawdown": -0.15,
        "annualized_one_way_turnover": 4.0,
        "result_dir": f"backtest/result/{candidate_id}",
    }


def _baseline() -> dict:
    return {
        "exp_id": "baseline/b2-s-on-b6-m",
        "baseline_ref": "B2-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "strategy": {
            "candidate_id": "topk-t30-d2-h20",
            "topk": 30,
            "n_drop": 2,
            "hold_thresh": 20,
        },
        "test_results": {
            pool: _metrics("topk-t30-d2-h20", value)
            for pool, value in {"csi1000": 0.9, "csi300": 1.0, "csi500": 0.5}.items()
        },
    }


def _protocol() -> dict:
    return {
        "exp_id": "strategy-neighborhood/b2-s-local-v1",
        "direction": "strategy-neighborhood-b2-s",
        "phase": "S",
        "baseline_ref": "B2-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "model_ref": "b6-m",
        "account": 500000,
        "selection_pool": "csi1000",
        "selection_segment": ["2020-01-13", "2021-07-15"],
        "test_pools": ["csi1000", "csi300", "csi500"],
        "test_segment": ["2021-07-16", "2026-07-31"],
        "strategy_grid": protocol.strategy_neighborhood_grid(),
        "selection_rule": ["axial_neighbor_ir_p25 desc"],
        "test_policy": "winner only",
    }


def _manifest(tmp_path: Path) -> dict:
    predictions = []
    for pool, segment in (
        ("csi1000", "valid"),
        ("csi1000", "test"),
        ("csi300", "test"),
        ("csi500", "test"),
    ):
        path = tmp_path / f"{pool}_{segment}.pkl"
        path.write_bytes(b"pred")
        predictions.append(
            {
                "model_ref": "b6-m",
                "pool": pool,
                "segment": segment,
                "path": str(path),
                "prediction_sha256": "a" * 64,
                "coverage": {"index_sha256": "b" * 64},
            }
        )
    return {
        "data_version": "2026-07-31",
        "models": {
            "b6-m": {
                "manifest_path": "backtest/models/baselines/b6-m/manifest.json",
                "model_path": "backtest/models/baselines/b6-m/seed4000/trained_model",
                "model_sha256": "c" * 64,
            }
        },
        "predictions": predictions,
    }


def test_preregistered_row_freezes_b2s_protocol_and_540_candidates(tmp_path: Path):
    row = finalizer.build_preregistered_row(
        _protocol(),
        _manifest(tmp_path),
        protocol_path=tmp_path / "protocol.json",
        prediction_manifest_path=tmp_path / "prediction_manifest.json",
        configs_dir=tmp_path / "configs",
    )

    assert row["state"] == "preregistered"
    assert row["baseline_ref"] == "B2-S v1.0"
    assert row["frozen_model_ref"] == "B6 v1.0"
    assert row["candidate_count"] == 540
    assert row["selection_pool"] == "csi1000"
    assert row["test_policy"] == "winner only"
    assert len(row["prediction_artifacts"]) == 4


def test_registry_rows_store_repo_paths_portably(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(finalizer, "REPO_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    manifest["models"]["b6-m"]["manifest_path"] = str(
        tmp_path / "backtest/models/baselines/b6-m/manifest.json"
    )
    manifest["models"]["b6-m"]["model_path"] = str(
        tmp_path / "backtest/models/baselines/b6-m/seed4000/trained_model"
    )
    for item in manifest["predictions"]:
        item["path"] = str(
            tmp_path / "backtest/experiments/run" / Path(item["path"]).name
        )
    row = finalizer.build_preregistered_row(
        _protocol(),
        manifest,
        protocol_path=tmp_path / "backtest/experiments/run/protocol.json",
        prediction_manifest_path=tmp_path / "backtest/experiments/run/manifest.json",
        configs_dir=tmp_path / "backtest/configs/run",
    )

    assert row["model_manifest"] == "backtest/models/baselines/b6-m/manifest.json"
    assert row["model_path"] == "backtest/models/baselines/b6-m/seed4000/trained_model"
    assert all(
        item["path"].startswith("backtest/")
        for item in row["prediction_artifacts"]
    )


def test_final_report_starts_with_baseline_and_compares_frozen_winner():
    winner_id = protocol.BASELINE_CANDIDATE_ID
    valid = {
        "winner": _metrics(winner_id, 1.4),
        "all_rows": [_metrics(winner_id, 1.4), _metrics("other", 1.3)],
    }
    test = {
        "state": "test_complete",
        "winner": valid["winner"],
        "pools": {
            pool: _metrics(winner_id, value)
            for pool, value in {"csi1000": 1.1, "csi300": 1.2, "csi500": 0.7}.items()
        },
    }

    html = finalizer.build_html(_baseline(), _protocol(), valid, test)
    soup = BeautifulSoup(html, "html.parser")

    assert "baseline" in (soup.select_one("table").get("class") or [])
    assert "B2-S v1.0" in soup.select_one("table.baseline").get_text()
    assert "topk-t30-d2-h20" in soup.select_one("table.baseline").get_text()
    assert winner_id in soup.select_one("table.winner").get_text()
    assert "CSI1000" in soup.select_one("table.winner").get_text()
    assert "1.100" in soup.select_one("table.winner").get_text()


def test_registry_transition_preserves_unrelated_lines_and_rejects_rewrite(tmp_path: Path):
    registry = tmp_path / "registry.jsonl"
    original = '{"exp_id":"baseline/x","state":"baseline"}\n'
    registry.write_text(original, encoding="utf-8")
    preregistered = {
        "exp_id": "strategy-neighborhood/b2-s-local-v1",
        "state": "preregistered",
    }

    finalizer.upsert_registry_transition(registry, preregistered)
    complete = {**preregistered, "state": "test_complete", "winner": "x"}
    finalizer.upsert_registry_transition(registry, complete)

    lines = registry.read_text(encoding="utf-8").splitlines(keepends=True)
    assert lines[0] == original
    assert json.loads(lines[1]) == complete
    with pytest.raises(ValueError, match="immutable"):
        finalizer.upsert_registry_transition(
            registry, {**complete, "winner": "changed"}
        )


@pytest.mark.parametrize("state", ["test_complete", "complete", "running", None])
def test_legacy_registry_transition_rejects_direct_completed_or_unknown_insert(
    tmp_path: Path, state: object
):
    with pytest.raises(ValueError, match="absent -> preregistered"):
        finalizer.upsert_registry_transition(
            tmp_path / "registry.jsonl",
            {"exp_id": finalizer.EXP_ID, "state": state},
        )


def test_legacy_cli_has_no_standalone_report_output_target():
    args = finalizer.parse_args(["finalize"])

    assert not hasattr(args, "output")
    assert finalizer.UNIFIED_REPORT.name == "strategy_stability_report.html"


def test_complete_row_recomputes_winner_and_rejects_tampering(tmp_path: Path):
    grid = protocol.strategy_neighborhood_grid()
    rows = [_metrics(item["candidate_id"], 1.0) for item in grid]
    scored, winner = protocol.score_valid_candidates(rows, grid)
    valid_path = tmp_path / "valid.json"
    test_path = tmp_path / "test.json"
    report_path = tmp_path / "report.html"
    valid = {
        "state": "valid_complete",
        "protocol_sha256": "protocol-sha",
        "run_contract": {
            "protocol_sha256": "protocol-sha",
            "prediction_manifest_sha256": "manifest-sha",
            "base_config_sha256": "base-sha",
        },
        "all_rows": scored,
        "winner": {**winner, "candidate_id": grid[-1]["candidate_id"]},
    }
    test = {
        "state": "test_complete",
        "run_contract": valid["run_contract"],
        "winner": valid["winner"],
        "pools": {
            pool: _metrics(valid["winner"]["candidate_id"], 1.0)
            for pool in ("csi1000", "csi300", "csi500")
        },
    }
    valid_path.write_text(json.dumps(valid), encoding="utf-8")
    test_path.write_text(json.dumps(test), encoding="utf-8")
    report_path.write_text("report", encoding="utf-8")
    preregistered = {
        "state": "preregistered",
        "protocol_sha256": "protocol-sha",
        "prediction_manifest_sha256": "manifest-sha",
        "prediction_artifacts": [],
    }

    with pytest.raises(ValueError, match="recomputed valid winner"):
        finalizer.build_complete_row(
            preregistered,
            _protocol(),
            valid,
            test,
            valid_path=valid_path,
            test_path=test_path,
            report_path=report_path,
        )


def test_complete_row_rejects_non_exact_valid_candidate_set(tmp_path: Path):
    grid = protocol.strategy_neighborhood_grid()
    rows = [_metrics(item["candidate_id"], 1.0) for item in grid]
    valid = {
        "state": "valid_complete",
        "all_rows": rows[:-1] + [rows[0]],
        "winner": rows[0],
    }
    test = {"state": "test_complete", "winner": rows[0], "pools": {}}
    path = tmp_path / "x.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly match protocol grid"):
        finalizer.build_complete_row(
            {"state": "preregistered"},
            _protocol(),
            valid,
            test,
            valid_path=path,
            test_path=path,
            report_path=path,
        )


def test_complete_row_verifies_prediction_and_config_contracts(tmp_path: Path):
    proto = _protocol()
    base_path = runner.DEFAULT_BASE_CONFIG
    proto.update(
        base_config=str(base_path.relative_to(ROOT)),
        base_config_sha256=finalizer.sha256_file(base_path),
    )
    base = runner.load_config(str(base_path))
    grid = proto["strategy_grid"]
    valid_rows = []
    for candidate in grid:
        row = {**candidate, **_metrics(candidate["candidate_id"], 1.0)}
        row.update(
            source_pred_sha256="valid-pred",
            effective_config_sha256=runner.effective_config_sha256(
                base, candidate, pool="csi1000", segment="valid"
            ),
        )
        valid_rows.append(row)
    scored, winner = protocol.score_valid_candidates(valid_rows, grid)
    contract = {
        "protocol_sha256": "protocol-sha",
        "prediction_manifest_sha256": "manifest-sha",
        "base_config_sha256": proto["base_config_sha256"],
    }
    valid = {
        "state": "valid_complete",
        "protocol_sha256": "protocol-sha",
        "run_contract": contract,
        "all_rows": scored,
        "winner": winner,
    }
    pools = {}
    for pool in ("csi1000", "csi300", "csi500"):
        row = _metrics(winner["candidate_id"], 1.0)
        row.update(
            source_pred_sha256=f"{pool}-pred",
            effective_config_sha256=runner.effective_config_sha256(
                base, winner, pool=pool, segment="test"
            ),
        )
        pools[pool] = row
    test = {
        "state": "test_complete",
        "run_contract": contract,
        "winner": winner,
        "pools": pools,
    }
    preregistered = {
        "state": "preregistered",
        "protocol_sha256": "protocol-sha",
        "prediction_manifest_sha256": "manifest-sha",
        "prediction_artifacts": [
            {
                "model_ref": "b6-m",
                "pool": "csi1000",
                "segment": "valid",
                "prediction_sha256": "valid-pred",
            },
            *[
                {
                    "model_ref": "b6-m",
                    "pool": pool,
                    "segment": "test",
                    "prediction_sha256": f"{pool}-pred",
                }
                for pool in ("csi1000", "csi300", "csi500")
            ],
        ],
    }
    valid_path = tmp_path / "valid.json"
    test_path = tmp_path / "test.json"
    report_path = tmp_path / "report.html"
    valid_path.write_text(json.dumps(valid), encoding="utf-8")
    test_path.write_text(json.dumps(test), encoding="utf-8")
    report_path.write_text("report", encoding="utf-8")

    complete = finalizer.build_complete_row(
        preregistered,
        proto,
        valid,
        test,
        valid_path=valid_path,
        test_path=test_path,
        report_path=report_path,
    )
    assert complete["selected_candidate_id"] == winner["candidate_id"]

    test["pools"]["csi300"]["source_pred_sha256"] = "tampered"
    with pytest.raises(ValueError, match="csi300 test prediction identity differs"):
        finalizer.build_complete_row(
            preregistered,
            proto,
            valid,
            test,
            valid_path=valid_path,
            test_path=test_path,
            report_path=report_path,
        )
