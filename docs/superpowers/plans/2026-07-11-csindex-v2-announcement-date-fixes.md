# csindex_v2 Announcement-Date Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `csindex_v2` reproducible and ensure every generated constituent interval uses the announcement date, while retaining the true effective date for audit.

**Architecture:** Keep `announce_date` and `effective_date` as separate fields in `all_changes.csv`. Route normal changes, manual fixes, and full-list anchors through one announcement-first date resolver before roster replay. Add targeted regression tests for the package syntax error, manual date propagation, id=1335 date extraction, anchor dates, and the SH600090/SH600223 paired replacement.

**Tech Stack:** Python 3.12, pandas, pytest, qlib CN trading calendar, existing cached CSIndex/Tushare artifacts.

## Global Constraints

- `changes/*_instruments.txt` and `changes/*_intervals.csv` use announcement dates; only missing announcement dates fall back to effective dates.
- `all_changes.csv` retains both dates and does not overwrite a known effective date with an announcement date.
- SH600223 is paired with SH600090 using announcement date `2020-07-03` and effective date `2020-07-13`.
- CSI300 and CSI2000 anchor intervals use their announcement dates (`2005-06-22` and `2023-08-10`).
- Reuse local caches; do not crawl the website or refresh Tushare snapshots.
- Preserve unrelated dirty-worktree changes and do not stage or commit files during this execution.

---

### Task 1: Restore Package Reproducibility

**Files:**
- Modify: `scripts/data_collector/csindex_v2/__init__.py`
- Create: `tests/misc/test_csindex_v2.py`

**Interfaces:**
- Consumes: Python source files under `scripts/data_collector/csindex_v2`.
- Produces: a package that can be parsed and imported by `python -m`.

- [ ] **Step 1: Write the failing syntax regression test**

```python
from pathlib import Path
import ast


CSINDEX_V2_DIR = Path(__file__).resolve().parents[2] / "scripts/data_collector/csindex_v2"


def test_csindex_v2_sources_are_valid_python():
    errors = []
    for path in sorted(CSINDEX_V2_DIR.glob("*.py")):
        try:
            ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            errors.append((path.name, exc.lineno, exc.msg))
    assert errors == []
```

- [ ] **Step 2: Run the test and confirm the current line 3 syntax error**

Run: `pytest -q tests/misc/test_csindex_v2.py::test_csindex_v2_sources_are_valid_python`

Expected: FAIL listing `__init__.py`, line 3, invalid character `。`.

- [ ] **Step 3: Replace the Markdown body with a valid module docstring**

```python
"""Rebuild CSI scale-index constituent history from official announcements.

See README.md in this directory for data sources, date semantics, and commands.
"""
```

- [ ] **Step 4: Verify syntax and module execution reach the builder**

Run: `pytest -q tests/misc/test_csindex_v2.py::test_csindex_v2_sources_are_valid_python`

Expected: PASS.

Run: `PYTHONDONTWRITEBYTECODE=1 python -m scripts.data_collector.csindex_v2.builder`

Expected: no `SyntaxError`; builder may rebuild the current cache successfully.

### Task 2: Preserve Announcement Dates in Manual Fixes

**Files:**
- Modify: `scripts/data_collector/csindex_v2/manual_fixes.csv`
- Modify: `scripts/data_collector/csindex_v2/aggregator.py`
- Modify: `tests/misc/test_csindex_v2.py`

**Interfaces:**
- Consumes: manual-fix columns `action,index_name,symbol,type,effective_date,announce_date,note`.
- Produces: `apply_manual_fixes(changes: pd.DataFrame, fixes: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]`.

- [ ] **Step 1: Add failing tests for manual add and patch behavior**

```python
import pandas as pd

from scripts.data_collector.csindex_v2.aggregator import apply_manual_fixes


def test_manual_fixes_preserve_both_dates():
    changes = pd.DataFrame([{
        "index_name": "csi300",
        "symbol": "SZ000527",
        "type": "remove",
        "effective_date": "2013-08-13",
        "announce_date": "2013-08-13",
        "source_id": "3453",
        "method": "content_approx_date",
        "source": "content",
    }])
    fixes = pd.DataFrame([
        {
            "action": "patch_date", "index_name": "csi300", "symbol": "SZ000527",
            "type": "remove", "effective_date": "2013-09-18",
            "announce_date": "2013-08-13", "note": "replacement",
        },
        {
            "action": "add", "index_name": "csi300", "symbol": "SZ000333",
            "type": "add", "effective_date": "2013-09-18",
            "announce_date": "2013-08-13", "note": "replacement",
        },
    ])

    result, counts = apply_manual_fixes(changes, fixes)

    rows = result.set_index("symbol")
    assert rows.loc["SZ000527", "effective_date"] == "2013-09-18"
    assert rows.loc["SZ000527", "announce_date"] == "2013-08-13"
    assert rows.loc["SZ000333", "effective_date"] == "2013-09-18"
    assert rows.loc["SZ000333", "announce_date"] == "2013-08-13"
    assert counts == {"add": 1, "drop": 0, "patch": 1}
```

- [ ] **Step 2: Run the test and confirm the helper/announcement propagation is absent**

Run: `pytest -q tests/misc/test_csindex_v2.py::test_manual_fixes_preserve_both_dates`

Expected: FAIL because `apply_manual_fixes` does not exist.

- [ ] **Step 3: Extract and implement `apply_manual_fixes`**

The helper must:

```python
def apply_manual_fixes(changes: pd.DataFrame, fixes: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    counts = {"add": 0, "drop": 0, "patch": 0}
    # Reuse the current key matching behavior.
    # patch_date always patches effective_date and patches announce_date when supplied.
    # add writes announce_date from the CSV instead of forcing None.
    # Return data sorted by index_name/effective_date/type/symbol.
```

Move the existing loop from `aggregate()` into this helper and keep its current drop semantics.

- [ ] **Step 4: Extend the CSV schema and record paired dates**

Required rows:

```csv
add,csi300,SZ000333,add,2013-09-18,2013-08-13,...
patch_date,csi300,SZ000527,remove,2013-09-18,2013-08-13,...
add,csi1000,SH600090,remove,2020-07-13,2020-07-03,...
add,csi1000,SH600223,add,2020-07-13,2020-07-03,...
```

Rows without a known announcement date keep an empty `announce_date` and therefore use the documented effective-date fallback.

- [ ] **Step 5: Run the focused tests**

Run: `pytest -q tests/misc/test_csindex_v2.py`

Expected: all tests added through Task 2 pass.

### Task 3: Correct id=1335 Effective-Date Extraction

**Files:**
- Modify: `scripts/data_collector/csindex_v2/parser_content.py`
- Modify: `tests/misc/test_csindex_v2.py`

**Interfaces:**
- Consumes: announcement text containing `于2010年1月4日一并实施和生效`.
- Produces: `extract_effective_date(...) == "2010-01-04"` while instruments continue to use the stored announcement date.

- [ ] **Step 1: Add the failing parser regression test**

```python
from scripts.data_collector.csindex_v2.parser_content import extract_effective_date


def test_extract_effective_date_handles_joint_implementation_phrase():
    text = (
        "本次调整将与2009年12月14日公布的定期调整方案"
        "于2010年1月4日一并实施和生效"
    )
    assert extract_effective_date(text, announce_date="2009-12-29") == "2010-01-04"
```

- [ ] **Step 2: Run the test and confirm it returns 2009-12-14**

Run: `pytest -q tests/misc/test_csindex_v2.py::test_extract_effective_date_handles_joint_implementation_phrase`

Expected: FAIL with actual value `2009-12-14`.

- [ ] **Step 3: Accept the optional `一并` phrase before the action verb**

Update the first effective-date pattern so the compact text matches `于<date>一并实施` before falling back to the first date in the body.

- [ ] **Step 4: Verify the parser regression test passes**

Run: `pytest -q tests/misc/test_csindex_v2.py::test_extract_effective_date_handles_joint_implementation_phrase`

Expected: PASS with `2010-01-04`.

### Task 4: Centralize Announcement-First Event and Anchor Dates

**Files:**
- Modify: `scripts/data_collector/csindex_v2/builder.py`
- Modify: `tests/misc/test_csindex_v2.py`

**Interfaces:**
- Consumes: `announce_date`, `effective_date`, `DATE_MODE`, and the trading calendar.
- Produces: `resolve_event_date(...) -> str` used by normal changes and anchor loaders.

- [ ] **Step 1: Add failing resolver and anchor tests**

```python
from scripts.data_collector.csindex_v2.builder import resolve_event_date


def test_resolve_event_date_prefers_announcement_and_falls_back():
    calendar = ["2023-08-10", "2023-08-11", "2023-08-14"]
    assert resolve_event_date("2023-08-10", "2023-08-11", calendar, "announce") == "2023-08-10"
    assert resolve_event_date(None, "2023-08-11", calendar, "announce") == "2023-08-11"
    assert resolve_event_date("2023-08-10", "2023-08-11", calendar, "effective") == "2023-08-11"
```

Also test that CSI300 and CSI2000 anchor metadata resolve to `2005-06-22` and `2023-08-10` in announcement mode.

- [ ] **Step 2: Run the tests and confirm the resolver is missing/current anchors use effective dates**

Run: `pytest -q tests/misc/test_csindex_v2.py -k 'resolve_event_date or anchor'`

Expected: FAIL.

- [ ] **Step 3: Implement the shared resolver and route `load_changes` through it**

```python
def resolve_event_date(
    announce_date: str | None,
    effective_date: str,
    calendar: list[str],
    date_mode: str = DATE_MODE,
) -> str:
    raw = announce_date if date_mode == "announce" and pd.notna(announce_date) and str(announce_date) else effective_date
    return snap_to_trading_day(str(raw)[:10], calendar)
```

- [ ] **Step 4: Resolve full-list anchor dates with the same helper**

Pass `calendar` into `load_csi300_anchor` and `load_csi2000_anchor`. Use `CSI300_ANCHOR` and `CSI2000_ANCHOR` metadata instead of returning hard-coded effective dates.

- [ ] **Step 5: Verify all unit tests pass**

Run: `pytest -q tests/misc/test_csindex_v2.py`

Expected: all tests pass.

### Task 5: Rebuild Cached Outputs and Validate Historical Invariants

**Files:**
- Regenerate: `~/.cache/qlib/csindex_v2/parsed/content_changes.csv`
- Regenerate: `~/.cache/qlib/csindex_v2/parsed/all_changes.csv`
- Regenerate: `~/.cache/qlib/csindex_v2/changes/*`
- Modify: `scripts/data_collector/csindex_v2/validator.py`

**Interfaces:**
- Consumes: cached announcements/attachments, updated parsers, updated manual fixes.
- Produces: rebuilt announcement-date constituent intervals and an evidence-backed validation report.

- [ ] **Step 1: Reparse only the affected content source**

Run: `python -m scripts.data_collector.csindex_v2.parser_content`

Expected: `content_changes.csv` is regenerated and id=1335 rows have effective date `2010-01-04`.

- [ ] **Step 2: Reaggregate manual fixes**

Run: `python -m scripts.data_collector.csindex_v2.aggregator`

Expected: Midea rows share announcement date `2013-08-13`; SH600090 and SH600223 share announcement date `2020-07-03`.

- [ ] **Step 3: Rebuild all four indices**

Run: `python -m scripts.data_collector.csindex_v2.builder`

Expected: all four indices build; terminal rosters exactly match the four official snapshots.

- [ ] **Step 4: Extend validator checks**

Add checks that report or fail on:

```text
critical nulls
invalid symbols
duplicate event keys
same-day add/remove conflicts
non-trading interval boundaries
overlapping intervals
terminal snapshot differences
SH600223 absent immediately before its 2026 removal
CSI300 roster count not equal to 300
CSI2000 first interval date not equal to 2023-08-10
```

- [ ] **Step 5: Run full validation**

Run: `python -m scripts.data_collector.csindex_v2.validator`

Expected: CSI300 legacy match remains `1200/1200`; terminal snapshot differences are zero; no SH600223 ghost-removal warning; CSI300 stays at 300; CSI2000 begins on `2023-08-10`.

### Task 6: Update Documentation and Final Verification

**Files:**
- Modify: `scripts/data_collector/csindex_v2/README.md`

**Interfaces:**
- Consumes: final implemented date semantics and regenerated validation results.
- Produces: user-facing documentation matching actual behavior.

- [ ] **Step 1: Update README date semantics**

Document that instruments are announcement-date datasets, effective dates are retained for audit, and missing announcement dates fall back to effective dates.

- [ ] **Step 2: Update manual-fix schema and examples**

Document the optional `announce_date` column, the Midea replacement, and the paired SH600090/SH600223 adjustment.

- [ ] **Step 3: Update anchor and residual-issue sections**

State that CSI300 switches to its first full roster on `2005-06-22`, CSI2000 begins on `2023-08-10`, and the former SH600223 residual warning is resolved.

- [ ] **Step 4: Run final verification commands**

Run: `pytest -q tests/misc/test_csindex_v2.py`

Run: `PYTHONDONTWRITEBYTECODE=1 python -m scripts.data_collector.csindex_v2.builder`

Run: `PYTHONDONTWRITEBYTECODE=1 python -m scripts.data_collector.csindex_v2.validator`

Expected: unit tests pass, builder exits zero, terminal rosters match, and validation reports no targeted regressions.

- [ ] **Step 5: Review only scoped changes**

Run: `git status --short -- scripts/data_collector/csindex_v2 tests/misc/test_csindex_v2.py docs/superpowers/plans/2026-07-11-csindex-v2-announcement-date-fixes.md`

Expected: only the plan, csindex_v2 files, README, and targeted tests appear in scope.
