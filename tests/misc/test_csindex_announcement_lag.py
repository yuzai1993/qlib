"""Official effective-date evidence must govern announcement/snapshot lag."""

import json
from pathlib import Path
import sys

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data_collector.csindex_v2 import updater


def setup_announcement(tmp_path, monkeypatch, *, announce="2026-09-09", content=None):
    roots = {name: tmp_path / name for name in ("instruments", "details", "parsed", "snapshots")}
    for root in roots.values():
        root.mkdir()
    monkeypatch.setattr(updater, "QLIB_INSTRUMENTS", roots["instruments"])
    for setting, name in (("DETAILS_DIR", "details"), ("PARSED_DIR", "parsed"), ("SNAPSHOTS_DIR", "snapshots")):
        monkeypatch.setattr(updater.cfg, setting, roots[name])
    path = roots["instruments"] / "csi300.txt"
    end = "2026-09-30" if announce == "2026-10-08" else "2026-09-08"
    path.write_text(f"SH601059\t2020-01-01\t{end}\nSZ002294\t{announce}\t2099-12-31\n")
    add_announcement(
        roots,
        "3006227",
        announce,
        content or "<p>决定自东兴证券、信达证券退市日起，对沪深300等指数进行样本调整。</p>",
        inferred=content is None,
    )
    return roots, path


def add_announcement(roots, source_id, announce, content, *, inferred=False):
    (roots["details"] / f"{source_id}.json").write_text(
        json.dumps(
            {
                "data": {
                    "id": int(source_id),
                    "publishDate": announce,
                    "title": "指数样本临时调整",
                    "content": content,
                }
            },
            ensure_ascii=False,
        )
    )
    changes = roots["parsed"] / "all_changes.csv"
    rows = [
        {
            "index_name": "csi300",
            "symbol": symbol,
            "type": direction,
            "announce_date": announce,
            "effective_date": announce if inferred else "2026-10-09",
            "source_id": source_id,
            "source": "excel",
            "method": "excel_approx_date" if inferred else "excel",
        }
        for symbol, direction in (("SH601059", "remove"), ("SZ002294", "add"))
    ]
    df = pd.DataFrame(rows)
    if changes.exists():
        df = pd.concat([pd.read_csv(changes, dtype=str), df], ignore_index=True)
    df.to_csv(changes, index=False)


def test_conditional_notice_remains_pending_after_25_days(tmp_path, monkeypatch):
    _, path = setup_announcement(tmp_path, monkeypatch)
    before = path.read_bytes()
    result = updater.compare_snapshot("csi300", "2026-10-08", {"SH601059"})
    assert result["ok"]
    assert result["pending_add"] == ["SZ002294"]
    assert result["pending_drop"] == ["SH601059"]
    assert result["announcement_evidence"]["SZ002294"]["source_id"] == "3006227"
    assert path.read_bytes() == before


@pytest.mark.parametrize("day,ok", [("2026-10-08", True), ("2026-10-09", True), ("2026-10-12", False)])
def test_later_dated_notice_limits_old_conditional_exemption(tmp_path, monkeypatch, day, ok):
    roots, _ = setup_announcement(tmp_path, monkeypatch)
    add_announcement(roots, "3006261", "2026-10-08", "于2026年10月9日收市后生效，调整名单见附件。")
    result = updater.compare_snapshot("csi300", day, {"SH601059"})
    assert result["ok"] is ok


@pytest.mark.parametrize("content", ["于2026年10月9日收市后生效。", "自2026年10月9日起生效。"])
def test_elapsed_explicit_date_cannot_hide_behind_25_day_window(tmp_path, monkeypatch, content):
    setup_announcement(tmp_path, monkeypatch, announce="2026-10-08", content=content)
    result = updater.compare_snapshot("csi300", "2026-10-12", {"SH601059"})
    assert not result["ok"]
    assert result["unexplained_local"] == ["SZ002294"]


def test_start_of_day_effective_date_is_not_still_pending_on_that_day(tmp_path, monkeypatch):
    setup_announcement(tmp_path, monkeypatch, announce="2026-10-08", content="自2026年10月9日起生效。")
    assert not updater.compare_snapshot("csi300", "2026-10-09", {"SH601059"})["ok"]


def test_first_trading_day_approximation_is_not_a_confirmed_deadline(tmp_path, monkeypatch):
    setup_announcement(tmp_path, monkeypatch, announce="2026-09-29", content="于2026年10月第一个交易日调整样本。")
    result = updater.compare_snapshot("csi300", "2026-10-01", {"SH601059"})
    assert result["ok"]
    assert result["pending_add"] == ["SZ002294"]
    assert result["pending_drop"] == ["SH601059"]


def test_unknown_effective_date_without_conditional_language_still_expires(tmp_path, monkeypatch):
    roots, _ = setup_announcement(tmp_path, monkeypatch)
    detail = roots["details"] / "3006227.json"
    payload = json.loads(detail.read_text())
    payload["data"]["content"] = "调整样本，调整名单见附件。"
    detail.write_text(json.dumps(payload))
    assert not updater.compare_snapshot("csi300", "2026-10-08", {"SH601059"})["ok"]


def write_snapshot(roots, folder, sheet_date, members):
    archive = roots["snapshots"] / f"daily/{folder}/000300cons.xls"
    archive.parent.mkdir(parents=True)
    # Real official-snapshot layout: date in column 0, security code in column 4.
    with archive.open("wb") as stream:
        pd.DataFrame([[sheet_date, "000300", "沪深300", "CSI300", symbol, "股票"] for symbol in members]).to_excel(
            stream, index=False, engine="openpyxl"
        )


@pytest.mark.parametrize("later_duplicate", [False, True])
def test_previously_observed_transition_cannot_be_excused_as_still_pending(tmp_path, monkeypatch, later_duplicate):
    roots, _ = setup_announcement(tmp_path, monkeypatch)
    write_snapshot(roots, "20260921", "20260921", ["002294"])
    if later_duplicate:
        add_announcement(roots, "3006261", "2026-10-08", "于2026年10月9日收市后生效。")
    result = updater.compare_snapshot("csi300", "2026-10-08", {"SH601059"})
    assert not result["ok"]
    assert result["unexplained_local"] == ["SZ002294"]
    assert result["unexplained_snap"] == ["SH601059"]


def test_archive_folder_cannot_turn_preannouncement_sheet_into_transition_evidence(tmp_path, monkeypatch):
    roots, _ = setup_announcement(tmp_path, monkeypatch)
    write_snapshot(roots, "20260910", "20260908", ["600000"])
    result = updater.compare_snapshot("csi300", "2026-10-08", {"SH601059"})
    assert result["ok"]


def test_future_notice_cannot_explain_past_snapshot(tmp_path, monkeypatch):
    roots, _ = setup_announcement(tmp_path, monkeypatch, content="调整样本，调整名单见附件。")
    add_announcement(roots, "3006261", "2026-10-09", "于2026年10月12日收市后生效。")
    assert not updater.compare_snapshot("csi300", "2026-10-08", {"SH601059"})["ok"]


def test_after_close_without_year_keeps_that_days_old_snapshot_pending(tmp_path, monkeypatch):
    setup_announcement(tmp_path, monkeypatch, announce="2026-10-08", content="决定于10月9日收市后调整。")
    assert updater.compare_snapshot("csi300", "2026-10-09", {"SH601059"})["ok"]


def test_unrelated_meeting_date_is_not_a_confirmed_effective_deadline(tmp_path, monkeypatch):
    roots, _ = setup_announcement(tmp_path, monkeypatch)
    detail = roots["details"] / "3006227.json"
    payload = json.loads(detail.read_text())
    payload["data"]["content"] = "2026年9月1日召开会议，根据编制规则，决定自信达证券退市日起调整样本。"
    detail.write_text(json.dumps(payload, ensure_ascii=False))
    assert updater.compare_snapshot("csi300", "2026-10-08", {"SH601059"})["ok"]


def test_newer_opposite_event_prevents_old_add_exemption(tmp_path, monkeypatch):
    roots, _ = setup_announcement(tmp_path, monkeypatch, announce="2026-10-08")
    add_announcement(roots, "3006262", "2026-10-09", "于2026年10月12日收市后生效。")
    path = roots["parsed"] / "all_changes.csv"
    changes = pd.read_csv(path, dtype=str)
    changes.loc[(changes["source_id"] == "3006262") & (changes["symbol"] == "SZ002294"), "type"] = "remove"
    changes.to_csv(path, index=False)
    result = updater.compare_snapshot("csi300", "2026-10-09", {"SH601059"})
    assert result["unexplained_local"] == ["SZ002294"]
    assert result["announcement_evidence"]["SZ002294"]["reason"] == "newer_opposite_event"
