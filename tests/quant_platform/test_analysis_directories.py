import io
import json
import zipfile
from pathlib import Path

import pytest

from quant_platform.analyses import AnalysisStore


def payload(**changes):
    row = dict(title="目录分析", background="背景", method="方法", conclusion="结论", usage="使用说明",
               tags=["诊断"], related_experiments=[], status="active",
               codes=[{"name": "analysis.py", "content": "print('archive only')\n"}])
    row.update(changes)
    return row


def manual_analysis(root, name="new-analysis", **changes):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "REPORT.md").write_text("# 手写分析\n\n原始结论\n", encoding="utf-8")
    metadata = {"schema_version": 1, "id": name, "title": "手写分析", "historical": True,
                "report_path": "REPORT.md", "code_files": []}
    metadata.update(changes)
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return directory


def test_canonical_diagnostics_scans_new_directories_on_every_read(tmp_path):
    research = tmp_path / "research"
    root = research / "backtest/diagnostics"
    store = AnalysisStore(None, research)
    assert store.root == root
    assert store.list() == []
    manual_analysis(root)
    assert [row["id"] for row in store.list()] == ["new-analysis"]
    manual_analysis(root, "another")
    assert {row["id"] for row in store.list()} == {"new-analysis", "another"}


def test_metadata_is_separate_and_updates_publish_whole_revisions(tmp_path):
    store = AnalysisStore(None, tmp_path / "research")
    first = store.create(payload())
    directory = store.root / first["id"]
    previous_metadata = json.loads((directory / "metadata.json").read_text())
    assert "report" not in previous_metadata and "codes" not in previous_metadata
    original_report = directory / previous_metadata["report_path"]
    original_bytes = original_report.read_bytes()
    second = store.update(first["id"], payload(version=1, conclusion="新结论"))
    metadata = json.loads((directory / "metadata.json").read_text())
    assert second["version"] == 2
    assert metadata["report_path"] != previous_metadata["report_path"]
    assert original_report.read_bytes() == original_bytes
    assert store.get(first["id"])["conclusion"] == "新结论"


def test_failed_metadata_commit_keeps_previous_report_and_codes(tmp_path, monkeypatch):
    store = AnalysisStore(None, tmp_path / "research")
    first = store.create(payload())
    directory = store.root / first["id"]
    before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    import quant_platform.analyses as module
    replace = module.os.replace

    def fail_metadata(source, destination):
        if Path(destination).name == "metadata.json":
            raise OSError("injected commit failure")
        return replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_metadata)
    with pytest.raises(OSError):
        store.update(first["id"], payload(version=1, conclusion="不得发表"))
    assert store.get(first["id"])["version"] == 1
    after = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("bad_path", ["../outside.md", "/etc/passwd", "code/../../outside.md", "code\\outside.py"])
def test_metadata_paths_cannot_escape_analysis_directory(tmp_path, bad_path):
    store = AnalysisStore(None, tmp_path / "research")
    manual_analysis(store.root, report_path=bad_path)
    with pytest.raises(ValueError):
        store.list()


@pytest.mark.parametrize("target", ["directory", "metadata", "report", "code"])
def test_analysis_symlinks_are_refused(tmp_path, target):
    store = AnalysisStore(None, tmp_path / "research")
    directory = manual_analysis(store.root)
    outside = tmp_path / "outside"
    outside.mkdir()
    if target == "directory":
        directory.rename(outside / "saved")
        directory.symlink_to(outside / "saved", target_is_directory=True)
    else:
        file = directory / {"metadata": "metadata.json", "report": "REPORT.md", "code": "analysis.py"}[target]
        if target == "code":
            metadata = json.loads((directory / "metadata.json").read_text())
            metadata["code_files"] = [{"name": "analysis.py", "path": "analysis.py"}]
            (directory / "metadata.json").write_text(json.dumps(metadata))
            file.write_text("# archive")
        file.rename(outside / "saved")
        file.symlink_to(outside / "saved")
    with pytest.raises(ValueError):
        store.list()


def test_invalid_metadata_never_falls_back_to_report_guessing(tmp_path):
    store = AnalysisStore(None, tmp_path / "research")
    directory = manual_analysis(store.root)
    (directory / "metadata.json").write_text("not json")
    with pytest.raises(ValueError):
        store.list()


def test_report_hash_mismatch_and_duplicate_ids_are_rejected(tmp_path):
    store = AnalysisStore(None, tmp_path / "research")
    directory = manual_analysis(store.root, report_sha256="0" * 64)
    with pytest.raises(ValueError, match="校验"):
        store.list()
    metadata = json.loads((directory / "metadata.json").read_text())
    metadata.pop("report_sha256")
    (directory / "metadata.json").write_text(json.dumps(metadata))
    manual_analysis(store.root, "second", id="new-analysis")
    with pytest.raises(ValueError, match="重复"):
        store.list()


def test_only_diagnostics_can_be_writable_inside_research_root(tmp_path):
    research = tmp_path / "research"
    AnalysisStore(research / "backtest/diagnostics", research)
    for invalid in (research, research / "archive", research / "live_trading/analyses"):
        with pytest.raises(ValueError):
            AnalysisStore(invalid, research)


def test_custom_analysis_root_does_not_merge_another_diagnostics_tree(tmp_path):
    research = tmp_path / "research"
    manual_analysis(research / "backtest/diagnostics", "research-only")
    custom = tmp_path / "archive"
    manual_analysis(custom, "custom-only")
    store = AnalysisStore(custom, research)
    assert [row["id"] for row in store.list()] == ["custom-only"]


def test_exported_directory_is_discovered_with_full_document_and_codes(tmp_path):
    store = AnalysisStore(None, tmp_path / "research")
    first = store.create(payload(background="正文包含 ## 方法，也不能误分段"))
    package = zipfile.ZipFile(io.BytesIO(store.export(first["id"])))
    target = tmp_path / "imported" / "manual-name"
    package.extractall(target)
    imported = AnalysisStore(target.parent, tmp_path / "other-research")
    actual = imported.get(first["id"])
    assert actual["background"] == first["background"]
    assert actual["report"] == first["report"]
    assert actual["codes"] == first["codes"]


def test_canonical_storage_under_bridge_is_still_rejected_before_writing(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must reject before writing")

    monkeypatch.setattr(Path, "mkdir", forbidden)
    with pytest.raises(ValueError, match="交易"):
        AnalysisStore(None, Path("/Volumes/QMT_BRIDGE/research"))
