"""A revised attachment under an existing notice ID must not be silently skipped."""

import copy
from datetime import date, timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data_collector.csindex_v2 import crawler
from scripts.data_collector.csindex_v2.aggregator import apply_manual_fixes
from scripts.data_collector.csindex_v2.builder import replay_forward, resolve_event_date


def setup_cached_notice(tmp_path, monkeypatch, age=47):
    from scripts.data_collector.csindex_v2 import detail_revisions
    monkeypatch.setattr(detail_revisions, "REVIEWED_REVISIONS", tmp_path / "reviewed.json")
    for name in ("DETAILS_DIR", "FILES_DIR", "SNAPSHOTS_DIR", "PARSED_DIR", "CHANGES_DIR"):
        monkeypatch.setattr(crawler.cfg, name, tmp_path / name.lower())
    monkeypatch.setattr(crawler.cfg, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(crawler.time, "sleep", lambda _: None)
    crawler.cfg.ensure_dirs()
    published = (date.today() - timedelta(days=age)).isoformat()
    original = {"data": {
        "id": 3006227, "publishDate": published,
        "title": "关于沪深300等指数样本临时调整的公告",
        "content": "<p>自退市日起调整，名单见附件。</p>",
        "enclosureList": [{"fileUrl": "https://oss-ch.csindex.com.cn/notice/old.xlsx"}],
    }}
    revised = copy.deepcopy(original)
    revised["data"]["enclosureList"][0]["fileUrl"] = "https://oss-ch.csindex.com.cn/notice/new.xlsx"
    path = crawler.cfg.DETAILS_DIR / "3006227.json"
    path.write_text(json.dumps(original, ensure_ascii=False))
    calls = []

    def get(url):
        calls.append(url)
        return SimpleNamespace(json=lambda: revised)

    monkeypatch.setattr(crawler, "_get", get)
    return [{"id": 3006227, "date": published}], original, revised, path, calls


def test_cached_recent_notice_revision_is_not_silently_skipped(tmp_path, monkeypatch):
    items, original, revised, path, calls = setup_cached_notice(tmp_path, monkeypatch)
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="未经审核.*3006227"):
        crawler.fetch_details(items)
    assert calls
    assert path.read_bytes() == before
    archived = list((tmp_path / "detail_revisions" / "3006227").glob("*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text()) == revised


def test_reviewed_revision_requires_its_dated_corrections(tmp_path, monkeypatch):
    items, original, revised, path, _ = setup_cached_notice(tmp_path, monkeypatch)
    from scripts.data_collector.csindex_v2.detail_revisions import detail_signature

    correction = {
        "action": "add", "index_name": "csi1000", "symbol": "SH603680", "type": "add",
        "announce_date": "2026-10-13", "effective_date": "2026-10-19",
    }
    review_path = tmp_path / "reviewed.json"
    review_path.write_text(json.dumps({"3006227": [{
        "baseline_sha256": detail_signature(original),
        "reviewed_sha256": detail_signature(revised),
        "corrections": [correction],
    }]}))
    fixes_path = tmp_path / "fixes.csv"
    pd.DataFrame(columns=correction).to_csv(fixes_path, index=False)
    from scripts.data_collector.csindex_v2 import detail_revisions
    monkeypatch.setattr(detail_revisions, "REVIEWED_REVISIONS", review_path)
    monkeypatch.setattr(detail_revisions, "MANUAL_FIXES", fixes_path)
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="缺少.*修正"):
        crawler.fetch_details(items)
    pd.DataFrame([correction]).to_csv(fixes_path, index=False)
    crawler.fetch_details(items)
    assert path.read_bytes() == before  # Never backdate the revised roster to the original publication.

    revised["data"]["enclosureList"][0]["fileUrl"] += "?revision=next"
    with pytest.raises(RuntimeError, match="未经审核"):
        crawler.fetch_details(items)


def test_unchanged_recent_notice_and_old_cache_remain_reusable(tmp_path, monkeypatch):
    items, original, revised, path, calls = setup_cached_notice(tmp_path, monkeypatch)
    revised.clear()
    revised.update(original)
    crawler.fetch_details(items)
    assert len(calls) == 1
    assert json.loads(path.read_text()) == original
    items[0]["date"] = (date.today() - timedelta(days=200)).isoformat()
    original["data"]["publishDate"] = items[0]["date"]
    path.write_text(json.dumps(original))
    crawler.fetch_details(items)
    assert len(calls) == 1


def test_failed_refresh_does_not_claim_cached_notice_is_current(tmp_path, monkeypatch):
    items, _, _, path, _ = setup_cached_notice(tmp_path, monkeypatch)
    before = path.read_bytes()
    monkeypatch.setattr(crawler, "_get", lambda _: None)
    with pytest.raises(RuntimeError, match="复查.*失败"):
        crawler.fetch_details(items)
    assert path.read_bytes() == before


def test_new_cache_cannot_backdate_a_known_revision(tmp_path, monkeypatch):
    items, original, revised, path, _ = setup_cached_notice(tmp_path, monkeypatch)
    from scripts.data_collector.csindex_v2 import detail_revisions

    baseline = tmp_path / "original.json"
    baseline.write_text(json.dumps(original))
    reviews = tmp_path / "reviewed.json"
    correction = {"action": "add", "index_name": "csi1000", "symbol": "SH603680", "type": "add",
                  "announce_date": "2026-10-13", "effective_date": "2026-10-19"}
    reviews.write_text(json.dumps({"3006227": [{
        "baseline_sha256": detail_revisions.detail_signature(original),
        "reviewed_sha256": detail_revisions.detail_signature(revised),
        "baseline_file": baseline.name, "corrections": [correction],
    }]}))
    fixes = tmp_path / "fixes.csv"
    pd.DataFrame([correction]).to_csv(fixes, index=False)
    monkeypatch.setattr(detail_revisions, "REVIEWED_REVISIONS", reviews)
    monkeypatch.setattr(detail_revisions, "MANUAL_FIXES", fixes)
    path.unlink()
    crawler.fetch_details(items)
    assert json.loads(path.read_text()) == original


@pytest.mark.parametrize("contaminated_cache", [True, False])
def test_known_revision_rejects_wrong_baseline_or_remote_rollback(tmp_path, monkeypatch, contaminated_cache):
    items, original, revised, path, _ = setup_cached_notice(tmp_path, monkeypatch)
    from scripts.data_collector.csindex_v2 import detail_revisions

    detail_revisions.REVIEWED_REVISIONS.write_text(json.dumps({"3006227": [{
        "baseline_sha256": detail_revisions.detail_signature(original),
        "reviewed_sha256": detail_revisions.detail_signature(revised),
    }]}))
    if contaminated_cache:
        path.write_text(json.dumps(revised))
    else:
        revised.clear()
        revised.update(original)
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="历史基线|回退"):
        crawler.fetch_details(items)
    assert path.read_bytes() == before


def test_october_revision_repairs_roster_only_from_revision_date():
    fixes = pd.read_csv(PROJECT_ROOT / "scripts/data_collector/csindex_v2/manual_fixes.csv", dtype=str)
    fixes = fixes.loc[fixes["announce_date"] == "2026-10-13"]
    initial = {"csi1000": {"SH603083", "SZ000001"}, "csi500": {"SZ301297", "SZ000002"}}
    expected = {"csi1000": {"SH603680", "SZ000001"}, "csi500": {"SZ301297", "SZ000002", "SH603083"}}
    calendar = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-19"]
    empty = pd.DataFrame(columns=["index_name", "symbol", "type", "effective_date", "announce_date"])
    corrected, _ = apply_manual_fixes(empty, fixes)
    corrected["event_date"] = pd.Series(
        [resolve_event_date(a, e, calendar) for a, e in
         zip(corrected["announce_date"], corrected["effective_date"])], dtype=object,
    )
    for index, roster in initial.items():
        events = corrected.loc[corrected["index_name"] == index]
        timeline = replay_forward("2026-10-12", roster, events, 0, [])
        assert timeline[0] == ("2026-10-12", roster)
        assert timeline[-1] == ("2026-10-13", expected[index])
        assert set(events["effective_date"]) == {"2026-10-19"}


@pytest.mark.parametrize("day,ok", [("2026-10-16", True), ("2026-10-19", False), ("2026-10-26", False)])
def test_reviewed_revision_has_a_confirmed_snapshot_deadline(tmp_path, monkeypatch, day, ok):
    from scripts.data_collector.csindex_v2 import updater

    monkeypatch.setattr(updater, "QLIB_INSTRUMENTS", tmp_path)
    monkeypatch.setattr(updater.cfg, "PARSED_DIR", tmp_path)
    monkeypatch.setattr(updater.cfg, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    (tmp_path / "csi1000.txt").write_text(
        "SH603083\t2023-11-24\t2026-10-12\nSH603680\t2026-10-13\t2099-12-31\n"
    )
    fixes = pd.read_csv(PROJECT_ROOT / "scripts/data_collector/csindex_v2/manual_fixes.csv", dtype=str)
    fixes = fixes.loc[fixes["announce_date"] == "2026-10-13"]
    empty = pd.DataFrame(columns=["index_name", "symbol", "type", "effective_date", "announce_date"])
    changes, _ = apply_manual_fixes(empty, fixes)
    changes.to_csv(tmp_path / "all_changes.csv", index=False)

    result = updater.compare_snapshot("csi1000", day, {"SH603083"})
    assert result["ok"] is ok
    for symbol in ("SH603083", "SH603680"):
        evidence = result["announcement_evidence"][symbol]
        assert evidence["source_id"] == "3006227"
        assert evidence["effective_date"] == "2026-10-19"
        assert evidence["reason"] == "reviewed_revision"


@pytest.mark.parametrize("changed_input", ["MANUAL_FIXES", "REVIEWED_REVISIONS"])
def test_approved_correction_invalidates_existing_build(tmp_path, monkeypatch, changed_input):
    from scripts.data_collector.csindex_v2 import detail_revisions, updater

    monkeypatch.setattr(updater.cfg, "CHANGES_DIR", tmp_path)
    for name in ("MANUAL_FIXES", "REVIEWED_REVISIONS"):
        path = tmp_path / name
        path.write_text("original")
        monkeypatch.setattr(detail_revisions, name, path)
    for name in updater.OFFICIAL_INDICES:
        (tmp_path / f"{name}_instruments.txt").write_text("existing output")
    monkeypatch.setattr(updater.cfg, "ensure_dirs", lambda: None)
    monkeypatch.setattr(updater, "crawl_incremental", lambda: 0)
    monkeypatch.setattr(updater, "install_instruments", lambda names: {})
    monkeypatch.setattr(updater, "build_hybrid_outputs", lambda: {})
    monkeypatch.setattr(updater, "archive_snapshots", lambda: None)
    monkeypatch.setattr(updater, "check_against_official_snapshots", lambda names: {})
    monkeypatch.setattr(updater, "current_members", lambda name: set())
    # Keep the real rebuild orchestration while replacing expensive parsers/builders.
    for parser in (updater.parser_excel, updater.parser_pdf, updater.parser_content):
        monkeypatch.setattr(parser, "parse_all", lambda: None)
    monkeypatch.setattr(updater, "aggregate", lambda: None)
    monkeypatch.setattr(updater, "build_all", lambda: "OK")

    assert updater.update_daily(force_rebuild=False)["rebuilt"]  # No proven input fingerprint yet.
    assert not updater.update_daily(force_rebuild=False)["rebuilt"]
    getattr(detail_revisions, changed_input).write_text("approved correction added")
    assert updater.update_daily(force_rebuild=False)["rebuilt"]
