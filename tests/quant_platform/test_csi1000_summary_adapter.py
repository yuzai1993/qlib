"""Verified CSI1000 strategy summaries retain their original metric basis."""

import json

import pytest

from backtest.scripts.experiment_registry import write_registry
from quant_platform.research import ResearchCatalog


def catalog(tmp_path, history, *, arithmetic=False):
    registry = tmp_path / "backtest/experiments/registry.jsonl"
    write_registry(registry, [history], indexed=True)
    index = json.loads(registry.read_text())
    if arithmetic:
        metadata = registry.parent / "strategies" / history["exp_id"] / "metadata.json"
        value = json.loads(metadata.read_text())
        value["annualized_return_basis"] = {"kind": "arithmetic", "source_sha256": index["detail_sha256"],
                                             "path": f"records/{index['detail_sha256']}.json"}
        metadata.write_text(json.dumps(value))
    return ResearchCatalog(tmp_path), index


def annualized_label(definitions):
    return next(value["label"] for value in definitions if value["key"] == "annualized_return")


def history(**changes):
    return {"exp_id": "baseline/b3-s-on-b6-m", "phase": "S", "state": "baseline",
            "metrics_summary": {"csi1000_full": {
                "ann": .24347797559678627, "sharpe": 1.1250084945519425,
                "alpha": .20522337814815075, "beta": .6267966815605561,
                "benchmark_cum": .2090987201751482, "mdd": -.3053618360209408,
                "calmar": .797, "vol": .216, "turnover": 12.3, "neighbor_ir_p25": .653}},
            **changes}


def test_csi1000_full_absolute_summary_is_unpacked_without_recomputing(tmp_path):
    original = history()
    c, index = catalog(tmp_path, original, arithmetic=True)

    record = c.snapshot()["records"][0]

    assert record["source_status"] == "historical_summary"
    assert record["source"] == "backtest/experiments/" + index["detail_path"]
    assert record["metrics"] == {
        "annualized_return": .24347797559678627, "sharpe_ratio": 1.1250084945519425,
        "alpha": .20522337814815075, "beta": .6267966815605561,
        "benchmark_cumulative_return": .2090987201751482, "max_drawdown": -.3053618360209408,
        "calmar_ratio": .797, "annualized_volatility": .216,
        "annualized_one_way_turnover": 12.3, "annualized_return_arith": None,
    }
    assert not record["official"] and not record["comparable"]
    assert annualized_label(record["metric_definitions"]) == "扣费年化（历史算术口径）"
    resource = next(r for r in record["resources"] if r["kind"] == "registry_record")
    assert json.loads(c.resource(resource["id"]).read_text()) == original


@pytest.mark.parametrize("version", [1, 2])
def test_test_pool_excess_summary_is_not_relabelled_as_absolute_return(tmp_path, version):
    original = history(exp_id=f"baseline/b{version}-s-on-b6-m", metrics_summary={
        "csi1000": {"ir": .935, "ann": .163, "mdd": -.333},
        "csi300": {"ir": 1.03, "ann": .152, "mdd": -.285},
    })
    c, _ = catalog(tmp_path, original)

    record = c.snapshot()["records"][0]

    assert all(value is None for value in record["metrics"].values())
    resource = next(r for r in record["resources"] if r["kind"] == "registry_record")
    assert json.loads(c.resource(resource["id"]).read_text()) == original


def test_broken_portable_source_never_falls_back_to_absolute_summary(tmp_path):
    c, _ = catalog(tmp_path, history(portable_eval_output="backtest/experiments/missing.json"))

    record = c.snapshot()["records"][0]

    assert record["source_status"] == "missing"
    assert all(value is None for value in record["metrics"].values())
    assert not record["official"] and not record["comparable"]


def test_full_period_long_form_metrics_take_precedence_over_legacy_summary(tmp_path):
    c, _ = catalog(tmp_path, history(full_period={"annualized_return": .238, "sharpe_ratio": 1.165}),
                   arithmetic=True)

    record = c.snapshot()["records"][0]

    assert record["source_status"] == "historical_summary"
    assert record["metrics"]["annualized_return"] == .238
    assert record["metrics"]["sharpe_ratio"] == 1.165
    assert annualized_label(record["metric_definitions"]) == "扣费年化（历史算术口径）"


def test_track_definition_does_not_impose_cagr_on_mixed_historical_protocols(tmp_path):
    c, _ = catalog(tmp_path, history())

    snapshot = c.snapshot()

    assert annualized_label(snapshot["metric_definitions_by_track"]["csi1000"]["strategy"]) == "扣费年化（历史原口径）"
    assert annualized_label(snapshot["records"][0]["metric_definitions"]) == "扣费年化（历史原口径）"
    assert annualized_label(snapshot["metric_definitions_by_track"]["all_a"]["strategy"]) == "扣费累乘年化（CAGR）"


def test_annualization_declaration_is_bound_to_exact_source_bytes(tmp_path):
    c, _ = catalog(tmp_path, history(), arithmetic=True)
    metadata = tmp_path / "backtest/experiments/strategies/baseline/b3-s-on-b6-m/metadata.json"
    value = json.loads(metadata.read_text())
    value["annualized_return_basis"]["source_sha256"] = "0" * 64
    metadata.write_text(json.dumps(value))

    record = c.snapshot()["records"][0]

    assert annualized_label(record["metric_definitions"]) == "扣费年化（历史原口径）"
    assert record["metrics"]["annualized_return"] == .24347797559678627


def test_new_dual_annualization_protocol_keeps_cagr_and_audit_values(tmp_path):
    c, _ = catalog(tmp_path, history(full_period={
        "annualized_return": .238, "annualized_return_arith": .25, "sharpe_ratio": 1.165}))

    record = c.snapshot()["records"][0]

    assert annualized_label(record["metric_definitions"]) == "扣费累乘年化（CAGR）"
    assert record["metrics"]["annualized_return"] == .238
    assert record["metrics"]["annualized_return_arith"] == .25


@pytest.mark.parametrize("invalid", [None, [], 1, "arithmetic", {"kind": []}])
def test_invalid_annualization_declaration_is_diagnosed_without_breaking_snapshot(tmp_path, invalid):
    c, _ = catalog(tmp_path, history())
    metadata = tmp_path / "backtest/experiments/strategies/baseline/b3-s-on-b6-m/metadata.json"
    value = json.loads(metadata.read_text())
    value["annualized_return_basis"] = invalid
    metadata.write_text(json.dumps(value))

    snapshot = c.snapshot()

    assert any("annualized_return_basis" in error["message"] for error in snapshot["sources"]["discovery"]["errors"])


def test_annualization_declaration_cannot_bind_to_different_file_with_same_bytes(tmp_path):
    c, index = catalog(tmp_path, history(), arithmetic=True)
    directory = tmp_path / "backtest/experiments/strategies/baseline/b3-s-on-b6-m"
    metadata = directory / "metadata.json"
    value = json.loads(metadata.read_text())
    value["annualized_return_basis"]["path"] = "records/copied.json"
    metadata.write_text(json.dumps(value))
    (directory / "records/copied.json").write_bytes((c.registry.parent / index["detail_path"]).read_bytes())

    record = c.snapshot()["records"][0]

    assert annualized_label(record["metric_definitions"]) == "扣费年化（历史原口径）"
