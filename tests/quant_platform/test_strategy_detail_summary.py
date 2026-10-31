"""Strategy detail descriptions preserve historical anchors and scientific state."""

import json

from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, row, strategy_doc


def test_promoted_strategy_can_record_historical_baseline_without_rewriting_self(tmp_path):
    previous = row("bt/v4", kind="strategy", bt_version="v4")
    current = row("bt/v5", kind="strategy", bt_version="v5", baseline_ref="self")
    c = catalog(tmp_path, [previous, current], [strategy_doc(), strategy_doc()])
    for original in [previous, current]:
        manifest(tmp_path, original["exp_id"], kind="strategy")
    before = {r["id"]: r for r in c.snapshot()["records"]}
    raw = c.registry.read_bytes()
    path = tmp_path / "backtest/experiments/strategies/bt/v5/metadata.json"
    doc = json.loads(path.read_text())
    doc.update(change_summary="从v4模型阶梯持仓改为v2模型Top5轮换。",
               comparison_baseline={"ref": "bt/v4", "version": "v4"})
    path.write_text(json.dumps(doc, ensure_ascii=False))
    after = {r["id"]: r for r in c.snapshot()["records"]}
    assert after["bt/v5"]["change_summary"] == doc["change_summary"]
    assert after["bt/v5"]["comparison_baseline"] == {"ref": "bt/v4", "version": "v4", "display_version": "s4", "initial": False}
    assert after["bt/v5"]["baseline_ref"] == "self"
    for key,value in before["bt/v5"].items():
        if key not in ["change_summary", "comparison_baseline", "analysis_revision", "core_results"]:
            assert after["bt/v5"][key] == value
    assert after["bt/v5"]["core_results"]["records"][0]["id"] == "bt/v4"
    assert after["bt/v5"]["core_results"]["records"][1]["metrics"] == before["bt/v5"]["metrics"]
    assert c.registry.read_bytes() == raw


def test_strategy_without_editorial_override_resolves_only_its_registered_baseline(tmp_path):
    baseline = row("bt/v3", kind="strategy", bt_version="v3")
    trial = row("bt/trial", kind="strategy", bt_version=None, baseline_ref="bt/v3")
    c = catalog(tmp_path, [baseline, trial], [strategy_doc(), strategy_doc()])
    record = next(r for r in c.snapshot()["records"] if r["id"] == "bt/trial")
    assert record["comparison_baseline"] == {"ref": "bt/v3", "version": "v3", "display_version": "s3", "initial": False}
    assert record["change_summary"] == ""


def test_initial_strategy_does_not_invent_a_previous_version(tmp_path):
    original = row("bt/v1", kind="strategy", bt_version="v1", baseline_ref="self")
    c = catalog(tmp_path, [original], [strategy_doc()])
    manifest(tmp_path, original["exp_id"], kind="strategy",
             change_summary="初始Top5轮换规则。", comparison_baseline={"ref": "", "version": "", "initial": True})
    record = c.snapshot()["records"][0]
    assert record["comparison_baseline"] == {"ref": "", "version": "", "display_version": "", "initial": True}
    assert record["version"] == "v1"


def test_raw_model_reference_cannot_be_mislabeled_as_a_strategy_baseline(tmp_path):
    model = row("model/v4", baseline_version="v4")
    strategy = row("bt/trial", kind="strategy", bt_version=None, baseline_ref=model["exp_id"])
    c = catalog(tmp_path, [model, strategy])
    record = next(r for r in c.snapshot()["records"] if r["id"] == strategy["exp_id"])
    assert record["comparison_baseline"]["version"] == ""
