from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backtest/scripts"))

import config_loader
from research_layout import relocate_path, report_href, research_path_identity, resolve_research_path


def _map(root: Path, paths: dict):
    path = root / "backtest/experiments/_catalog/path_map.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "paths": paths}))


def test_moved_path_is_authoritative_even_while_old_copy_exists(tmp_path):
    old = "backtest/configs/model/variant_s42.yaml"
    new = "backtest/experiments/model/variant/configs/variant_s42.yaml"
    _map(tmp_path, {old: new})
    for name in (old, new):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    assert relocate_path(old, tmp_path) == new
    assert relocate_path(str(tmp_path / old), tmp_path) == str(tmp_path / new)
    assert resolve_research_path(old, tmp_path) == (tmp_path / new).resolve()
    assert relocate_path(new, tmp_path) == new


def test_unknown_historical_artifact_paths_are_preserved(tmp_path):
    _map(tmp_path, {})
    missing = "backtest/result/old-cleaned-session"
    external = "/archive/qlib_exp/backtest/result/session"
    assert relocate_path(missing, tmp_path) == missing
    assert relocate_path(external, tmp_path) == external
    assert resolve_research_path(missing, tmp_path) == tmp_path / missing


@pytest.mark.parametrize("destination", [".", "../outside.json", "/private/tmp/outside.json", "backtest/experiments/../../outside.json", "bad\\path"])
def test_invalid_path_map_fails_closed(tmp_path, destination):
    _map(tmp_path, {"backtest/configs/old.yaml": destination})
    with pytest.raises(ValueError):
        relocate_path("backtest/configs/old.yaml", tmp_path)


def test_empty_component_source_is_rejected(tmp_path):
    _map(tmp_path, {".": "backtest/experiments/example.json"})
    with pytest.raises(ValueError):
        relocate_path("backtest/configs/old.yaml", tmp_path)


def test_mapped_symlink_escape_is_rejected(tmp_path):
    destination = "backtest/experiments/escape/secret.yaml"
    _map(tmp_path, {"backtest/configs/old.yaml": destination})
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "backtest/experiments/escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        relocate_path("backtest/configs/old.yaml", tmp_path)


def test_config_loader_finds_legacy_alias_and_new_short_name(tmp_path, monkeypatch):
    old = "backtest/configs/model/variant_s42.yaml"
    new = "backtest/experiments/model/variant/configs/variant_s42.yaml"
    _map(tmp_path, {old: new})
    path = tmp_path / new
    path.parent.mkdir(parents=True)
    path.write_text("run: {mode: train_only}\n")
    old_path = tmp_path / old
    old_path.parent.mkdir(parents=True)
    old_path.write_text("run: {mode: old_copy}\n")
    monkeypatch.setattr(config_loader, "BACKTEST_ROOT", tmp_path / "backtest")
    monkeypatch.setattr(config_loader, "CONFIGS_DIR", tmp_path / "backtest/configs")
    assert config_loader.resolve_config_path(old) == path
    assert config_loader.resolve_config_path("model/variant_s42.yaml") == path
    assert config_loader.resolve_config_path("variant_s42") == path
    assert config_loader.resolve_config_path(str(path)) == path


def test_duplicate_archived_config_basenames_are_ambiguous(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, "BACKTEST_ROOT", tmp_path / "backtest")
    monkeypatch.setattr(config_loader, "CONFIGS_DIR", tmp_path / "backtest/configs")
    for experiment in ("one", "two"):
        path = tmp_path / "backtest/experiments" / experiment / "configs/shared.yaml"
        path.parent.mkdir(parents=True)
        path.write_text("run: {}\n")
    with pytest.raises(config_loader.ConfigError, match="多份匹配"):
        config_loader.resolve_config_path("shared")


def test_report_link_resolves_both_relocated_paths(tmp_path):
    _map(tmp_path, {"backtest/experiments/report.html": "backtest/experiments/reports/report.html", "backtest/experiments/detail.html": "backtest/experiments/model/x/reports/detail.html"})
    assert report_href("backtest/experiments/detail.html", "backtest/experiments/report.html", tmp_path) == "../model/x/reports/detail.html"


def test_yaml_dependency_paths_relocate_in_memory_without_changing_frozen_file(tmp_path, monkeypatch):
    old = "backtest/experiments/old-manifest.json"
    new = "backtest/experiments/model/x/data/manifest.json"
    _map(tmp_path, {old: new})
    path = tmp_path / "frozen.yaml"
    original = f"input: {old}\nmetrics: [1, 2.0, -0.0]\n".encode()
    path.write_bytes(original)
    monkeypatch.setattr(config_loader, "BACKTEST_ROOT", tmp_path / "backtest")
    monkeypatch.setattr(config_loader, "validate_run_section", lambda cfg: cfg)
    monkeypatch.setattr(config_loader, "align_dates_from_segments", lambda cfg: cfg)
    loaded = config_loader.load_config(str(path))
    assert loaded["input"] == new
    assert loaded["metrics"] == [1, 2.0, -0.0]
    assert path.read_bytes() == original


def test_path_identity_preserves_earliest_registered_source_across_multiple_moves(tmp_path):
    old = "backtest/experiments/old-analysis/bench.csv"
    middle = "backtest/experiments/intermediate/bench.csv"
    current = "backtest/experiments/model/x/data/bench.csv"
    _map(tmp_path, {old: middle, middle: current})
    for alias in (old, middle, current):
        assert research_path_identity(alias, tmp_path) == old
        assert research_path_identity(str(tmp_path / alias), tmp_path) == str(tmp_path / old)
    unknown = "backtest/experiments/another-analysis/bench.csv"
    assert research_path_identity(unknown, tmp_path) == unknown
    assert research_path_identity("unchanged descriptive text", tmp_path) == "unchanged descriptive text"
    assert research_path_identity("/archive/old/bench.csv", tmp_path) == "/archive/old/bench.csv"
    assert research_path_identity("./" + old, tmp_path) == "./" + old


def test_path_identity_does_not_guess_between_multiple_registered_origins(tmp_path):
    current = "backtest/experiments/model/x/data/bench.csv"
    _map(tmp_path, {"backtest/experiments/first.csv": current, "backtest/experiments/second.csv": current})
    with pytest.raises(ValueError, match="ambiguous"):
        research_path_identity(current, tmp_path)


def test_path_identity_rejects_registered_cycles(tmp_path):
    first, second = "backtest/experiments/first.csv", "backtest/experiments/second.csv"
    _map(tmp_path, {first: second, second: first})
    with pytest.raises(ValueError, match="cycle"):
        research_path_identity(first, tmp_path)


def test_path_identity_rejects_canonical_destination_symlink_escape(tmp_path):
    old, current = "backtest/experiments/old.csv", "backtest/experiments/escape/new.csv"
    _map(tmp_path, {old: current})
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "backtest/experiments/escape").symlink_to(outside, target_is_directory=True)
    for alias in (old, current):
        with pytest.raises(ValueError, match="escapes"):
            research_path_identity(alias, tmp_path)
