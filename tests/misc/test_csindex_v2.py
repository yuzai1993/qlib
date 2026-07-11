from pathlib import Path
import ast
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSINDEX_V2_DIR = PROJECT_ROOT / "scripts/data_collector/csindex_v2"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_csindex_v2_sources_are_valid_python():
    errors = []
    for path in sorted(CSINDEX_V2_DIR.glob("*.py")):
        try:
            ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            errors.append((path.name, exc.lineno, exc.msg))
    assert errors == []


def test_manual_fixes_preserve_both_dates():
    from scripts.data_collector.csindex_v2.aggregator import apply_manual_fixes

    changes = pd.DataFrame(
        [
            {
                "index_name": "csi300",
                "symbol": "SZ000527",
                "type": "remove",
                "effective_date": "2013-08-13",
                "announce_date": "2013-08-13",
                "source_id": "3453",
                "method": "content_approx_date",
                "source": "content",
            }
        ]
    )
    fixes = pd.DataFrame(
        [
            {
                "action": "patch_date",
                "index_name": "csi300",
                "symbol": "SZ000527",
                "type": "remove",
                "effective_date": "2013-09-18",
                "announce_date": "2013-08-13",
                "note": "replacement",
            },
            {
                "action": "add",
                "index_name": "csi300",
                "symbol": "SZ000333",
                "type": "add",
                "effective_date": "2013-09-18",
                "announce_date": "2013-08-13",
                "note": "replacement",
            },
        ]
    )

    result, counts = apply_manual_fixes(changes, fixes)

    rows = result.set_index("symbol")
    assert rows.loc["SZ000527", "effective_date"] == "2013-09-18"
    assert rows.loc["SZ000527", "announce_date"] == "2013-08-13"
    assert rows.loc["SZ000333", "effective_date"] == "2013-09-18"
    assert rows.loc["SZ000333", "announce_date"] == "2013-08-13"
    assert counts == {"add": 1, "drop": 0, "patch": 1}


def test_manual_fixes_csv_contains_announcement_pairs():
    fixes = pd.read_csv(CSINDEX_V2_DIR / "manual_fixes.csv", dtype=str)

    expected = {
        ("csi300", "SZ000333", "add"): ("2013-09-18", "2013-08-13"),
        ("csi300", "SZ000527", "remove"): ("2013-09-18", "2013-08-13"),
        ("csi1000", "SH600090", "remove"): ("2020-07-13", "2020-07-03"),
        ("csi1000", "SH600223", "add"): ("2020-07-13", "2020-07-03"),
    }
    rows = fixes.set_index(["index_name", "symbol", "type"])

    for key, dates in expected.items():
        assert rows.loc[key, "effective_date"] == dates[0]
        assert rows.loc[key, "announce_date"] == dates[1]


def test_manual_fixes_csv_contains_csi500_first_known_snapshot_adds():
    fixes = pd.read_csv(
        CSINDEX_V2_DIR / "manual_fixes.csv", dtype=str, keep_default_na=False
    )
    rows = fixes.set_index(["index_name", "symbol", "type"])

    expected = {
        ("csi500", "SH601598", "add"): "2019-01-31",
        ("csi500", "SH601868", "add"): "2021-09-30",
    }
    for key, effective_date in expected.items():
        assert rows.loc[key, "action"] == "add"
        assert rows.loc[key, "effective_date"] == effective_date
        assert rows.loc[key, "announce_date"] == ""
        assert "Tushare" in rows.loc[key, "note"]
        assert "首次月末快照" in rows.loc[key, "note"]


def test_extract_effective_date_handles_joint_implementation_phrase():
    from scripts.data_collector.csindex_v2.parser_content import extract_effective_date

    text = (
        "本次调整将与2009年12月14日公布的定期调整方案"
        "于2010年1月4日一并实施和生效"
    )

    assert extract_effective_date(text, announce_date="2009-12-29") == "2010-01-04"


def test_resolve_event_date_prefers_announcement_and_falls_back():
    from scripts.data_collector.csindex_v2.builder import resolve_event_date

    calendar = ["2023-08-10", "2023-08-11", "2023-08-14"]

    assert (
        resolve_event_date("2023-08-10", "2023-08-11", calendar, "announce")
        == "2023-08-10"
    )
    assert resolve_event_date(None, "2023-08-11", calendar, "announce") == "2023-08-11"
    assert (
        resolve_event_date("2023-08-10", "2023-08-11", calendar, "effective")
        == "2023-08-11"
    )


def test_anchor_metadata_resolves_to_announcement_dates():
    from scripts.data_collector.csindex_v2.builder import resolve_event_date
    from scripts.data_collector.csindex_v2.index_starts import CSI2000_ANCHOR, CSI300_ANCHOR

    calendar = ["2005-06-22", "2005-07-01", "2023-08-10", "2023-08-11"]

    assert (
        resolve_event_date(
            CSI300_ANCHOR["announce_date"],
            CSI300_ANCHOR["effective_date"],
            calendar,
            "announce",
        )
        == "2005-06-22"
    )
    assert (
        resolve_event_date(
            CSI2000_ANCHOR["announce_date"],
            CSI2000_ANCHOR["effective_date"],
            calendar,
            "announce",
        )
        == "2023-08-10"
    )


def test_index_start_metadata_discloses_csi500_manual_snapshot_fixes():
    from scripts.data_collector.csindex_v2.index_starts import (
        build_index_start_records,
    )

    rows = []
    for index_name, count in (("csi500", 50), ("csi1000", 100)):
        for i in range(count):
            common = {
                "index_name": index_name,
                "effective_date": "2015-12-14",
                "announce_date": "2015-11-30",
                "source_id": "4272",
                "source": "excel",
            }
            rows.append({**common, "symbol": f"ADD{i:06d}", "type": "add"})
            rows.append({**common, "symbol": f"DEL{i:06d}", "type": "remove"})

    records = build_index_start_records(pd.DataFrame(rows))

    assert (
        records["csi500"]["data_source"]
        == "official_with_manual_tushare_snapshot_fixes"
    )
    assert records["csi1000"]["data_source"] == "official_only"


def test_interval_errors_check_structure_overlap_and_snapshot():
    from scripts.data_collector.csindex_v2.validator import interval_errors

    calendar = ["2020-01-02", "2020-01-03", "2020-01-06"]
    valid = pd.DataFrame(
        [
            {"symbol": "SH600000", "start": "2020-01-02", "end": "2020-01-03"},
            {"symbol": "SH600001", "start": "2020-01-02", "end": "2099-12-31"},
        ]
    )
    assert interval_errors(valid, calendar, "2020-01-06", {"SH600001"}) == []

    overlap = pd.concat(
        [
            valid,
            pd.DataFrame(
                [{"symbol": "SH600000", "start": "2020-01-03", "end": "2099-12-31"}]
            ),
        ],
        ignore_index=True,
    )
    errors = interval_errors(overlap, calendar, "2020-01-06", {"SH600001"})

    assert any("区间重叠" in error for error in errors)
    assert any("终局快照" in error for error in errors)


def test_change_errors_detect_event_key_and_calendar_problems():
    from scripts.data_collector.csindex_v2.validator import change_errors

    valid = pd.DataFrame(
        [
            {
                "index_name": "csi300",
                "symbol": "SH600000",
                "type": "add",
                "effective_date": "2020-01-02",
                "source": "excel",
            }
        ]
    )
    assert change_errors(valid, ["2020-01-02", "2020-01-03"]) == []

    invalid = pd.concat([valid, valid, valid], ignore_index=True)
    invalid.loc[1, "symbol"] = "600000"
    invalid.loc[1, "effective_date"] = "2020-01-04"
    errors = change_errors(invalid, ["2020-01-02", "2020-01-03"])

    assert any("非法证券代码" in error for error in errors)
    assert any("生效日非交易日" in error for error in errors)
    assert any("重复事件键" in error for error in errors)
