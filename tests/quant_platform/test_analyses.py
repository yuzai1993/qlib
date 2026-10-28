import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from quant_platform.analyses import AnalysisStore, ConflictError


def payload(**changes):
    result = dict(
        title="排序稳定性分析",
        background="比较信号变化",
        method="读取冻结摘要",
        conclusion="需要区分研究口径",
        usage="python analysis.py --help",
        tags=["信号"],
        related_experiments=["model/v4"],
        codes=[{"name": "analysis.py", "content": "print('analysis')\n"}],
        status="active",
    )
    result.update(changes)
    return result


def test_save_reload_conflict_archive_and_export(tmp_path):
    store = AnalysisStore(tmp_path / "archive", tmp_path / "research")
    created = store.create(payload())
    assert created["version"] == 1
    reloaded = AnalysisStore(tmp_path / "archive", tmp_path / "research")
    assert reloaded.get(created["id"])["background"] == "比较信号变化"
    updated = reloaded.update(created["id"], payload(version=1, status="archived"))
    assert updated["version"] == 2 and updated["status"] == "archived"
    with pytest.raises(ConflictError):
        store.update(created["id"], payload(version=1))
    package = zipfile.ZipFile(io.BytesIO(store.export(created["id"])))
    assert package.read("code/analysis.py").decode() == "print('analysis')\n"
    assert "## 使用说明" in package.read("REPORT.md").decode()
    assert len(store.list()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"background": ""},
        {"codes": []},
        {"status": "deleted"},
        {"codes": [{"name": "../escape.py", "content": "x"}]},
        {"codes": [{"name": "/tmp/escape.py", "content": "x"}]},
        {"codes": [{"name": "a.py", "content": "x"}, {"name": "a.py", "content": "y"}]},
        {"codes": [{"name": "x.py", "content": "x" * 300_000}]},
    ],
)
def test_invalid_analysis_cannot_write(tmp_path, changes):
    store = AnalysisStore(tmp_path / "archive", tmp_path / "research")
    with pytest.raises(ValueError):
        store.create(payload(**changes))
    assert store.list() == []


def test_historical_reports_are_read_only_and_keep_missing_evidence(tmp_path):
    report = tmp_path / "research/backtest/diagnostics/20260809_test/REPORT.md"
    report.parent.mkdir(parents=True)
    report.write_text("# 历史报告\n\n背景和方法\n![图](missing.png)\n脚本 `missing.py` 已丢失。", encoding="utf-8")
    store = AnalysisStore(tmp_path / "archive", tmp_path / "research")
    history = store.list()[0]
    assert history["historical"] is True
    assert history["attachments_missing"]
    assert "missing.py" in history["report"]
    with pytest.raises(ValueError):
        store.update(history["id"], payload(version=1))
    assert report.read_text().startswith("# 历史报告")


def test_record_symlink_and_path_escape_are_rejected(tmp_path):
    store = AnalysisStore(tmp_path / "archive", tmp_path / "research")
    row = store.create(payload())
    path = tmp_path / "archive" / (row["id"] + ".json")
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(row))
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises((ValueError, FileNotFoundError)):
        store.get(row["id"])
    with pytest.raises((ValueError, FileNotFoundError)):
        store.get("../../outside")


def test_analysis_storage_must_not_overlap_research(tmp_path):
    with pytest.raises(ValueError):
        AnalysisStore(tmp_path / "research/archive", tmp_path / "research")


def test_complete_saved_document_size_is_checked_before_writing(tmp_path):
    store = AnalysisStore(tmp_path / "archive", tmp_path / "research")
    large = payload(**{field: "中" * 99_999 for field in ("background", "method", "conclusion", "usage")})
    large["codes"] = [{"name": "large.py", "content": "x" * 250_000}]
    with pytest.raises(ValueError, match="2 MiB"):
        store.create(large)
    assert store.list() == []


@pytest.mark.parametrize(
    "location",
    [
        "/Volumes/qmt_bridge/archive",
        "/Volumes/QMT_BRIDGE/archive",
        str(Path.home() / "Project/qlib/archive"),
        str(Path.home() / "Project/QLIB/archive"),
    ],
)
def test_production_and_bridge_storage_rejected_before_any_write(tmp_path, monkeypatch, location):
    def forbidden_mkdir(*args, **kwargs):
        raise AssertionError("must reject before touching a protected directory")

    monkeypatch.setattr(Path, "mkdir", forbidden_mkdir)
    with pytest.raises(ValueError, match="生产|交易"):
        AnalysisStore(Path(location), tmp_path / "research")


def test_concurrent_stale_edit_has_only_one_winner(tmp_path):
    store = AnalysisStore(tmp_path / "archive", tmp_path / "research")
    row = store.create(payload())

    def update(title):
        try:
            return store.update(row["id"], payload(title=title, version=1))["version"]
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, ["编辑一", "编辑二"]))
    assert sorted(map(str, results)) == ["2", "conflict"]
    assert store.get(row["id"])["version"] == 2
