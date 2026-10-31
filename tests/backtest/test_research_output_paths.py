"""Research writers publish only at the resolved archive destination."""

import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))


def archive_alias(root, filename):
    old = root / "backtest/experiments/legacy/arm"
    new = root / "backtest/experiments/models/group/arm"
    mapping = root / "backtest/experiments/_catalog/path_map.json"
    mapping.parent.mkdir(parents=True)
    mapping.write_text(json.dumps({"schema_version": 1, "paths": {}, "directories": {
        old.relative_to(root).as_posix(): new.relative_to(root).as_posix(),
    }}))
    return old / filename, new / filename


@pytest.mark.parametrize("module_name", ["register_strategy_stability", "register_phase_s_experiment"])
def test_protocol_writer_creates_only_current_archive_directory(tmp_path, monkeypatch, module_name):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "_RESEARCH_ROOT", tmp_path)
    old, current = archive_alias(tmp_path, "data/protocol.json")

    module.write_protocol(old)

    assert json.loads(current.read_text())["phase"] == "S"
    assert not (tmp_path / "backtest/experiments/legacy").exists()


@pytest.mark.parametrize("module_name", ["build_experiment_report", "build_phase_m_v1_report"])
def test_model_report_main_creates_only_current_archive_directory(tmp_path, monkeypatch, module_name):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "_RESEARCH_ROOT", tmp_path)
    if hasattr(module, "EXP_ROOT"):
        monkeypatch.setattr(module, "EXP_ROOT", tmp_path)
    monkeypatch.setattr(module, "load_registry", lambda path: [])
    old, current = archive_alias(tmp_path, "reports/hub.html")

    module.main(["--output", str(old)])

    assert "<!" in current.read_text()
    assert not (tmp_path / "backtest/experiments/legacy").exists()


def test_execution_hub_writer_returns_actual_destination(tmp_path, monkeypatch):
    module = importlib.import_module("build_phase_m_v1_bt_report")
    monkeypatch.setattr(module, "_RESEARCH_ROOT", tmp_path)
    monkeypatch.setattr(module, "EXP_ROOT", tmp_path)
    monkeypatch.setattr(module, "load_registry", lambda: [])
    monkeypatch.setattr(module, "catalog_from_registry", lambda rows: ([], []))
    old, current = archive_alias(tmp_path, "reports/hub.html")
    monkeypatch.setattr(module, "HUB_OUT", old)

    written = module.write_all()

    assert written == [current]
    assert "<!" in current.read_text()
    assert not (tmp_path / "backtest/experiments/legacy").exists()


def test_csi_model_report_links_from_its_actual_output_directory(tmp_path, monkeypatch):
    module = importlib.import_module("build_experiment_report")
    monkeypatch.setattr(module, "_RESEARCH_ROOT", tmp_path)
    old, current = archive_alias(tmp_path, "reports/hub.html")
    monkeypatch.setattr(module, "load_registry", lambda path: [{"phase": "S"}])

    module.main(["--output", str(old)])

    from html.parser import HTMLParser

    class Links(HTMLParser):
        targets = set()

        def handle_starttag(self, tag, attrs):
            href = dict(attrs).get("href", "")
            if tag == "a" and href and not href.startswith("#"):
                self.targets.add((current.parent / href).resolve())

    links = Links()
    links.feed(current.read_text())
    reports = tmp_path / "backtest/experiments/reports"
    assert reports / "phase_m_v1_report.html" in links.targets
    assert reports / "strategy_stability_report.html" in links.targets


@pytest.mark.parametrize("module_name", ["build_strategy_stability_report", "build_strategy_report"])
def test_strategy_report_main_writes_at_resolved_output_with_working_links(tmp_path, monkeypatch, module_name):
    module = importlib.import_module(module_name)
    unified = importlib.import_module("build_strategy_stability_report")
    from tests.backtest.test_build_strategy_stability_report import _baseline_row, _row

    rows = [_row("b6-m"), _baseline_row(), {
        "exp_id": "strategy-extended-history/example", "phase": "S", "state": "complete",
        "report_html": "backtest/experiments/models/group/arm/reports/detail.html",
    }]
    for target in {module, unified}:
        monkeypatch.setattr(target, "_RESEARCH_ROOT", tmp_path)
    monkeypatch.setattr(unified, "REPO_ROOT", tmp_path)
    old, current = archive_alias(tmp_path, "reports/hub.html")
    if module_name == "build_strategy_report":
        monkeypatch.setattr(module, "UNIFIED_REPORT", old)
        monkeypatch.setattr(module, "load_registry", lambda path: rows)
        module.main([])
    else:
        monkeypatch.setattr(module, "_load_registry_rows", lambda path: rows)
        module.main(["--output", str(old)])

    assert 'href="detail.html"' in current.read_text()
    assert not (tmp_path / "backtest/experiments/legacy").exists()


def test_correction_writer_resolves_before_creating_parent(tmp_path, monkeypatch):
    module = importlib.import_module("finalize_strategy_neighborhood_full")
    monkeypatch.setattr(module, "_RESEARCH_ROOT", tmp_path)
    old, current = archive_alias(tmp_path, "data/correction.json")
    results = tmp_path / "results.json"
    results.write_text('{"exp_id": "history/example"}')
    correction = {"exp_id": "history/example-correction", "phase": "S"}
    monkeypatch.setattr(module, "load_registry", lambda path: [{"exp_id": "history/example"}])
    monkeypatch.setattr(module, "build_full_baseline_comparison_correction", lambda *args, **kwargs: correction)
    monkeypatch.setattr(module, "append_registry_correction", lambda *args: None)

    module.main(["append-correction", "--results", str(results), "--correction", str(old)])

    assert json.loads(current.read_text()) == correction
    assert not (tmp_path / "backtest/experiments/legacy").exists()
