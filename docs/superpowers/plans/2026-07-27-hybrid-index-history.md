# Hybrid CSI Index History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build reproducible `csi500_hybrid` and `csi1000_hybrid` training universes from 2010 while guaranteeing that membership from 2015-11-30 onward is identical to the existing official universes.

**Architecture:** Keep the official `csindex_v2` event stream untouched. A Tushare backfill helper caches the finite pre-cutover inputs; a pure hybrid builder freezes prefix intervals and appends the current official suffix on every daily run. The updater installs official outputs first, then atomically installs validated hybrid outputs.

**Tech Stack:** Python 3.10+, pandas, pyarrow/parquet, Tushare Pro, pytest, Qlib text instrument intervals.

## Global Constraints

- Official cutover is exactly `2015-11-30`; prefix intervals end no later than `2015-11-27`.
- `csi500`, `csi1000`, `parsed/all_changes.csv`, baseline configs, evaluation pools, and live configs must remain unchanged.
- `csi1000_hybrid` proxy ranks positive `daily_basic.total_mv`, excludes same-date CSI300 and CSI500, then takes at most 1000 symbols.
- The daily scheduler must never redownload or recompute the frozen 2010–2015 prefix.
- The official suffix is copied from the current official instruments output and validated before installation.
- Tushare credentials are read only from `TUSHARE_TOKEN`.
- Qlib/Python commands use `/opt/anaconda3/envs/qlib/bin/python`.
- Do not use stdin/heredoc for Python commands that may trigger Qlib multiprocessing.
- This task creates data capability only; it does not run a model experiment or change `backtest/EXPERIMENT_STANDARD.md`.

---

## File Structure

- Create `scripts/data_collector/csindex_v2/hybrid_history.py`
  - Pure roster transformations, prefix freezing, manifest creation, official suffix validation, and CLI orchestration.
- Create `scripts/data_collector/csindex_v2/hybrid_backfill.py`
  - Tushare client creation, monthly `index_weight` cache migration/fetching, and `daily_basic.total_mv` resumable backfill.
- Create `tests/misc/test_csindex_hybrid_history.py`
  - Unit and integration-style tests using temporary caches and synthetic official intervals.
- Modify `scripts/data_collector/csindex_v2/updater.py`
  - Separate official/hybrid constants, preserve official-first update order, atomically install hybrid outputs, and report hybrid failures without skipping official checks.
- Modify `scripts/data_collector/update_indices_daily.py`
  - Treat a hybrid build/install error as a failed daily index job while retaining official snapshot diagnostics.
- Modify `scripts/data_collector/csindex_v2/README.md`
  - Document hybrid provenance, one-time backfill/freeze commands, daily behavior, and training-only use.

---

### Task 1: Pure Monthly Roster Transformations

**Files:**
- Create: `scripts/data_collector/csindex_v2/hybrid_history.py`
- Create: `tests/misc/test_csindex_hybrid_history.py`

**Interfaces:**
- Produces: `ts_code_to_symbol(ts_code: str) -> str | None`
- Produces: `select_csi1000_proxy(total_mv: pd.DataFrame, excluded: set[str], limit: int = 1000) -> set[str]`
- Produces: `rosters_to_closed_intervals(rosters: list[tuple[str, set[str]]], calendar: list[str], final_end: str, source: str) -> pd.DataFrame`
- Produces: `active_members(intervals: pd.DataFrame, date: str) -> set[str]`

- [ ] **Step 1: Write failing conversion and proxy-selection tests**

```python
def test_ts_code_conversion_rejects_unknown_exchanges():
    assert hybrid.ts_code_to_symbol("600000.SH") == "SH600000"
    assert hybrid.ts_code_to_symbol("000001.SZ") == "SZ000001"
    assert hybrid.ts_code_to_symbol("920001.BJ") == "BJ920001"
    assert hybrid.ts_code_to_symbol("ABC.HK") is None


def test_proxy_selection_excludes_indices_and_breaks_ties_by_symbol():
    frame = pd.DataFrame(
        {
            "ts_code": ["000003.SZ", "000002.SZ", "000001.SZ", "600000.SH"],
            "total_mv": [20.0, 20.0, 100.0, 10.0],
        }
    )
    selected = hybrid.select_csi1000_proxy(
        frame, excluded={"SZ000001"}, limit=2
    )
    assert selected == {"SZ000002", "SZ000003"}
```

- [ ] **Step 2: Run the focused tests and confirm missing-module failure**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py \
  -k "conversion or proxy_selection" -q
```

Expected: FAIL because `hybrid_history` and its functions do not exist.

- [ ] **Step 3: Implement deterministic symbol conversion and ranking**

```python
def ts_code_to_symbol(ts_code: str) -> str | None:
    code, dot, exchange = str(ts_code).upper().partition(".")
    if dot != "." or len(code) != 6 or not code.isdigit():
        return None
    prefix = {"SH": "SH", "SZ": "SZ", "BJ": "BJ"}.get(exchange)
    return f"{prefix}{code}" if prefix else None


def select_csi1000_proxy(
    total_mv: pd.DataFrame, excluded: set[str], limit: int = 1000
) -> set[str]:
    frame = total_mv[["ts_code", "total_mv"]].copy()
    frame["symbol"] = frame["ts_code"].map(ts_code_to_symbol)
    frame["total_mv"] = pd.to_numeric(frame["total_mv"], errors="coerce")
    frame = frame[
        frame["symbol"].notna()
        & frame["total_mv"].gt(0)
        & ~frame["symbol"].isin(excluded)
    ]
    frame = frame.sort_values(
        ["total_mv", "symbol"], ascending=[False, True], kind="mergesort"
    )
    return set(frame.head(limit)["symbol"])
```

- [ ] **Step 4: Write failing interval-semantics tests**

```python
def test_rosters_become_non_overlapping_closed_intervals():
    calendar = ["2010-01-29", "2010-02-01", "2010-02-26", "2010-03-01"]
    intervals = hybrid.rosters_to_closed_intervals(
        [
            ("2010-01-29", {"SH600000", "SZ000001"}),
            ("2010-02-26", {"SH600000", "SZ000002"}),
        ],
        calendar,
        final_end="2010-03-01",
        source="fixture",
    )
    assert set(map(tuple, intervals[["symbol", "start", "end"]].to_numpy())) == {
        ("SH600000", "2010-01-29", "2010-03-01"),
        ("SZ000001", "2010-01-29", "2010-02-01"),
        ("SZ000002", "2010-02-26", "2010-03-01"),
    }
    assert hybrid.active_members(intervals, "2010-02-01") == {
        "SH600000",
        "SZ000001",
    }
```

- [ ] **Step 5: Implement roster-to-interval conversion**

Use `bisect_left` against the supplied calendar to find the previous trading day.
Track open segments in `dict[str, str]`; close removed symbols on the previous
trading day and close remaining symbols on `final_end`. Return columns
`symbol,start,end,source` sorted by `symbol,start`.

- [ ] **Step 6: Run the complete new test file**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit the pure transformation layer**

```bash
git add \
  scripts/data_collector/csindex_v2/hybrid_history.py \
  tests/misc/test_csindex_hybrid_history.py
git commit -m "feat(data): add hybrid index transformations"
```

---

### Task 2: Resumable Tushare Backfill

**Files:**
- Create: `scripts/data_collector/csindex_v2/hybrid_backfill.py`
- Modify: `scripts/data_collector/csindex_v2/hybrid_history.py`
- Modify: `tests/misc/test_csindex_hybrid_history.py`
- Modify: `tests/misc/test_tushare_credentials.py`

**Interfaces:**
- Produces: `get_tushare_pro() -> Any`
- Produces: `required_month_end_dates(calendar: list[str], start_month: str, end_month: str) -> list[str]`
- Produces: `merge_cache(existing: pd.DataFrame, incoming: pd.DataFrame, keys: list[str]) -> pd.DataFrame`
- Produces: `backfill_index_weights(pro, snapshot_dir: Path, legacy_dir: Path, sleep_seconds: float = 0.4) -> dict[str, Path]`
- Produces: `backfill_total_mv(pro, dest: Path, calendar: list[str], sleep_seconds: float = 0.4) -> Path`
- Produces: `backfill_all() -> dict[str, str]`

- [ ] **Step 1: Add failing month-end and cache-idempotence tests**

```python
def test_required_month_ends_follow_local_calendar():
    calendar = [
        "2010-01-04", "2010-01-29",
        "2010-02-01", "2010-02-26",
        "2010-03-01",
    ]
    assert backfill.required_month_end_dates(
        calendar, "2010-01", "2010-02"
    ) == ["20100129", "20100226"]


def test_merge_cache_is_idempotent_and_sorted():
    existing = pd.DataFrame(
        [{"trade_date": "20100129", "con_code": "000001.SZ"}]
    )
    incoming = pd.DataFrame(
        [
            {"trade_date": "20100129", "con_code": "000001.SZ"},
            {"trade_date": "20100226", "con_code": "600000.SH"},
        ]
    )
    merged = backfill.merge_cache(
        existing, incoming, ["trade_date", "con_code"]
    )
    assert merged.to_dict("records") == [
        {"trade_date": "20100129", "con_code": "000001.SZ"},
        {"trade_date": "20100226", "con_code": "600000.SH"},
    ]
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py \
  -k "month_ends or merge_cache" -q
```

Expected: FAIL because `hybrid_backfill` does not exist.

- [ ] **Step 3: Implement credential handling, date selection, and atomic parquet writes**

`get_tushare_pro()` imports `tushare`, reads `os.environ["TUSHARE_TOKEN"]`,
and raises `RuntimeError("TUSHARE_TOKEN 未配置")` when empty. Atomic writes use a
temporary parquet file in the destination directory followed by `os.replace`.

- [ ] **Step 4: Add failing fake-client backfill tests**

```python
class FakePro:
    def __init__(self):
        self.daily_calls = []

    def daily_basic(self, **kwargs):
        self.daily_calls.append(kwargs["trade_date"])
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600000.SH"],
                "trade_date": [kwargs["trade_date"]] * 2,
                "total_mv": [10.0, 20.0],
            }
        )


def test_total_mv_backfill_skips_cached_dates(tmp_path):
    dest = tmp_path / "total_mv.parquet"
    pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20100129"],
            "total_mv": [10.0],
        }
    ).to_parquet(dest)
    pro = FakePro()
    calendar = ["2010-01-29", "2010-02-26", "2010-03-31", "2010-04-30",
                "2010-05-31", "2010-06-30", "2010-07-30", "2010-08-31",
                "2010-09-30", "2010-10-29", "2010-11-30", "2010-12-31",
                "2011-01-31", "2011-02-28", "2011-03-31", "2011-04-29",
                "2011-05-31", "2011-06-30", "2011-07-29", "2011-08-31",
                "2011-09-30", "2011-10-31", "2011-11-30", "2011-12-30",
                "2012-01-31", "2012-02-29", "2012-03-30", "2012-04-27",
                "2012-05-31", "2012-06-29", "2012-07-31", "2012-08-31",
                "2012-09-28", "2012-10-31", "2012-11-30", "2012-12-31",
                "2013-01-31", "2013-02-28", "2013-03-29", "2013-04-26",
                "2013-05-31", "2013-06-28", "2013-07-31", "2013-08-30",
                "2013-09-30", "2013-10-31", "2013-11-29", "2013-12-31",
                "2014-01-30", "2014-02-28", "2014-03-31", "2014-04-30",
                "2014-05-30", "2014-06-30", "2014-07-31", "2014-08-29",
                "2014-09-30", "2014-10-31", "2014-11-28", "2014-12-31",
                "2015-01-30", "2015-02-27", "2015-03-31", "2015-04-30"]
    backfill.backfill_total_mv(pro, dest, calendar, sleep_seconds=0)
    assert "20100129" not in pro.daily_calls
    assert pro.daily_calls[0] == "20100226"
```

The implementation must use actual dates present in the supplied calendar.

- [ ] **Step 5: Implement index-weight migration and total-market-cap fetching**

Index specifications:

```python
INDEX_WEIGHT_SPECS = {
    "csi500": ("000905.SH", "2010-01", "2015-11"),
    "csi1000": ("000852.SH", "2015-05", "2015-11"),
}
```

For each month, retain only the latest `trade_date` returned in that month.
Validate that CSI500 snapshots contain exactly 500 unique symbols. CSI1000
snapshots may contain 1000–1002 symbols because the source can include temporary
constituents. Migrate the existing legacy parquet before making network calls.

- [ ] **Step 6: Extend the hardcoded-token regression guard**

Add `ROOT / "scripts/data_collector/csindex_v2/hybrid_backfill.py"` to
`PRODUCTION_COLLECTORS` in `tests/misc/test_tushare_credentials.py`.

- [ ] **Step 7: Run backfill and credential tests**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py \
  tests/misc/test_tushare_credentials.py -q
```

Expected: PASS without network access.

- [ ] **Step 8: Commit the backfill layer**

```bash
git add \
  scripts/data_collector/csindex_v2/hybrid_backfill.py \
  scripts/data_collector/csindex_v2/hybrid_history.py \
  tests/misc/test_csindex_hybrid_history.py \
  tests/misc/test_tushare_credentials.py
git commit -m "feat(data): add resumable hybrid history backfill"
```

---

### Task 3: Freeze Auditable Prefixes

**Files:**
- Modify: `scripts/data_collector/csindex_v2/hybrid_history.py`
- Modify: `tests/misc/test_csindex_hybrid_history.py`

**Interfaces:**
- Produces: `members_on_date(intervals: pd.DataFrame, date: str) -> set[str]`
- Produces: `build_prefix_frames(index_weights: dict[str, pd.DataFrame], total_mv: pd.DataFrame, csi300: pd.DataFrame, calendar: list[str]) -> dict[str, pd.DataFrame]`
- Produces: `sha256_file(path: Path) -> str`
- Produces: `freeze_prefixes(hybrid_root: Path = HYBRID_ROOT) -> dict`

- [ ] **Step 1: Add a failing synthetic prefix test**

```python
def test_csi1000_proxy_hands_off_to_direct_snapshot_at_2015_05():
    calendar = ["2015-04-30", "2015-05-04", "2015-05-28",
                "2015-05-29", "2015-06-01", "2015-11-27"]
    total_mv = pd.DataFrame(
        {
            "trade_date": ["20150430"] * 3,
            "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "total_mv": [30.0, 20.0, 10.0],
        }
    )
    weights = {
        "csi500": pd.DataFrame(
            {"trade_date": ["20150430"], "con_code": ["000001.SZ"]}
        ),
        "csi1000": pd.DataFrame(
            {"trade_date": ["20150529"], "con_code": ["000003.SZ"]}
        ),
    }
    csi300 = pd.DataFrame(
        [{"symbol": "SZ000002", "start": "2010-01-01", "end": "2099-12-31"}]
    )
    frames = hybrid.build_prefix_frames(
        weights, total_mv, csi300, calendar, proxy_limit=1000
    )
    csi1000 = frames["csi1000_hybrid"]
    assert hybrid.active_members(csi1000, "2015-04-30") == {"SZ000003"}
    assert hybrid.active_members(csi1000, "2015-05-04") == {"SZ000003"}
    assert hybrid.active_members(csi1000, "2015-05-28") == {"SZ000003"}
    assert hybrid.active_members(csi1000, "2015-05-29") == {"SZ000003"}
    assert set(csi1000["source"]) == {
        "total_mv_proxy", "tushare_index_weight"
    }
```

The proxy contains only the non-excluded symbol and the direct-source boundary
remains explicit even when the same symbol is present on both sides.

- [ ] **Step 2: Run the handoff test and confirm failure**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py \
  -k "hands_off_to_direct_snapshot" -q
```

Expected: FAIL because `build_prefix_frames` is missing.

- [ ] **Step 3: Implement prefix construction**

Normalize dates to ISO format. Use the most recent same-month CSI500 snapshot
for exclusions. Build the CSI1000 proxy and direct portions separately so their
source boundary remains auditable. Clip all final intervals to `PREFIX_END`.

- [ ] **Step 4: Add failing manifest test**

```python
def test_freeze_prefixes_writes_manifest_with_hashes(tmp_path, monkeypatch):
    manifest = hybrid.freeze_prefixes(
        hybrid_root=tmp_path / "hybrid",
        changes_dir=tmp_path / "changes",
        calendar_path=tmp_path / "day.txt",
    )
    assert manifest["algorithm_version"] == 1
    assert manifest["cutover"] == "2015-11-30"
    assert manifest["prefix_end"] == "2015-11-27"
    assert set(manifest["outputs"]) == {
        "csi500_hybrid", "csi1000_hybrid"
    }
    assert len(manifest["inputs"]["total_mv_monthly"]["sha256"]) == 64
```

Prepare fixture parquet and instrument files in the test before calling the
function.

- [ ] **Step 5: Implement prefix persistence and manifest hashing**

Write prefix CSVs atomically. The manifest includes:

```python
{
    "algorithm_version": 1,
    "generated_at": "...Z",
    "cutover": "2015-11-30",
    "prefix_end": "2015-11-27",
    "proxy_rule": {
        "metric": "total_mv",
        "limit": 1000,
        "exclude": ["csi300", "csi500"],
    },
    "inputs": {
        "csi500_index_weight": {"path": "...", "sha256": "..."},
        "csi1000_index_weight": {"path": "...", "sha256": "..."},
        "total_mv_monthly": {"path": "...", "sha256": "..."},
        "csi300_official": {"path": "...", "sha256": "..."},
    },
    "outputs": {
        "csi500_hybrid": {"rows": 0, "first": "...", "last": "..."},
        "csi1000_hybrid": {"rows": 0, "first": "...", "last": "..."},
    },
    "monthly_member_counts": {
        "csi500_hybrid": {},
        "csi1000_hybrid": {},
    },
}
```

- [ ] **Step 6: Run all hybrid tests**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit prefix freezing**

```bash
git add \
  scripts/data_collector/csindex_v2/hybrid_history.py \
  tests/misc/test_csindex_hybrid_history.py
git commit -m "feat(data): freeze auditable hybrid prefixes"
```

---

### Task 4: Exact Official Suffix Splicing

**Files:**
- Modify: `scripts/data_collector/csindex_v2/hybrid_history.py`
- Modify: `tests/misc/test_csindex_hybrid_history.py`

**Interfaces:**
- Produces: `read_instruments(path: Path) -> pd.DataFrame`
- Produces: `validate_interval_structure(intervals: pd.DataFrame) -> None`
- Produces: `validate_official_suffix(hybrid: pd.DataFrame, official: pd.DataFrame, calendar: list[str]) -> None`
- Produces: `build_hybrid_outputs(hybrid_root: Path = HYBRID_ROOT, changes_dir: Path = cfg.CHANGES_DIR, calendar_path: Path = CALENDAR_PATH) -> dict[str, Path]`

- [ ] **Step 1: Add failing exact-suffix tests**

```python
def test_hybrid_suffix_is_exact_official_copy(tmp_path):
    prefix = pd.DataFrame(
        [("SH600000", "2010-01-29", "2015-11-27", "tushare_index_weight")],
        columns=["symbol", "start", "end", "source"],
    )
    official = pd.DataFrame(
        [
            ("SH600001", "2015-11-30", "2015-12-01"),
            ("SH600002", "2015-11-30", "2099-12-31"),
        ],
        columns=["symbol", "start", "end"],
    )
    combined = hybrid.splice_official_suffix(prefix, official)
    hybrid.validate_official_suffix(
        combined, official, ["2015-11-30", "2015-12-01", "2015-12-02"]
    )
    pd.testing.assert_frame_equal(
        combined.iloc[len(prefix):].reset_index(drop=True), official
    )


def test_suffix_validation_rejects_one_changed_end_date():
    official = pd.DataFrame(
        [("SH600001", "2015-11-30", "2099-12-31")],
        columns=["symbol", "start", "end"],
    )
    changed = official.copy()
    changed.loc[0, "end"] = "2016-01-04"
    with pytest.raises(ValueError, match="官方后缀"):
        hybrid.validate_official_suffix(
            changed, official, ["2015-11-30", "2016-01-04"]
        )
```

- [ ] **Step 2: Run suffix tests and confirm failure**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py -k "suffix" -q
```

Expected: FAIL because splice/validation functions are missing.

- [ ] **Step 3: Implement splice and validation**

Reject any prefix row with `end >= CUTOVER`. Reject any official row with
`start < CUTOVER`. Append official rows in their existing order. Validate exact
DataFrame equality and compare `active_members` for every local calendar day
from `CUTOVER` through the last available calendar day.

- [ ] **Step 4: Add failing output-atomicity test**

Patch `validate_official_suffix` to raise after candidate construction. Assert
that a pre-existing destination file remains byte-for-byte unchanged.

- [ ] **Step 5: Implement atomic output writes and CLI**

`build_hybrid_outputs` builds and validates both frames before replacing either
destination. The `main()` argparse commands are:

```text
backfill       -> hybrid_backfill.backfill_all()
freeze-prefix  -> freeze_prefixes()
build          -> build_hybrid_outputs()
prepare        -> backfill_all(), freeze_prefixes(), build_hybrid_outputs()
```

- [ ] **Step 6: Run hybrid tests and source compilation**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py \
  tests/misc/test_csindex_v2.py::test_csindex_v2_sources_are_valid_python -q
```

Expected: PASS.

- [ ] **Step 7: Commit suffix splicing**

```bash
git add \
  scripts/data_collector/csindex_v2/hybrid_history.py \
  tests/misc/test_csindex_hybrid_history.py
git commit -m "feat(data): splice exact official index suffixes"
```

---

### Task 5: Daily Scheduler Integration

**Files:**
- Modify: `scripts/data_collector/csindex_v2/updater.py`
- Modify: `scripts/data_collector/update_indices_daily.py`
- Modify: `tests/misc/test_csindex_hybrid_history.py`
- Modify: `tests/misc/test_csindex_v2.py`

**Interfaces:**
- Produces: `OFFICIAL_INDICES = ("csi300", "csi500", "csi1000", "csi2000")`
- Produces: `HYBRID_INDICES = ("csi500_hybrid", "csi1000_hybrid")`
- Preserves: `INSTALL_INDICES = OFFICIAL_INDICES` for callers that import the old name.
- Changes: `update_daily(force_rebuild: bool = True) -> dict` adds `hybrid_error`, hybrid installed paths, and six-pool member counts.

- [ ] **Step 1: Add failing updater ordering and failure-isolation tests**

```python
def test_update_daily_installs_official_before_building_hybrid(monkeypatch):
    events = []
    monkeypatch.setattr(updater, "crawl_incremental", lambda: 0)
    monkeypatch.setattr(updater, "rebuild", lambda: events.append("rebuild"))
    monkeypatch.setattr(
        updater, "install_instruments",
        lambda names: events.append(("install", names)) or {}
    )
    monkeypatch.setattr(
        updater, "build_hybrid_outputs",
        lambda: events.append("build_hybrid") or {}
    )
    monkeypatch.setattr(updater, "archive_snapshots", lambda: None)
    monkeypatch.setattr(
        updater, "check_against_official_snapshots", lambda names: {}
    )
    monkeypatch.setattr(updater, "current_members", lambda name: set())
    updater.update_daily(force_rebuild=True)
    assert events[:4] == [
        "rebuild",
        ("install", updater.OFFICIAL_INDICES),
        "build_hybrid",
        ("install", updater.HYBRID_INDICES),
    ]


def test_hybrid_failure_does_not_skip_official_snapshot_checks(monkeypatch):
    checked = []
    # Patch crawl/rebuild/install as above.
    monkeypatch.setattr(
        updater, "build_hybrid_outputs",
        lambda: (_ for _ in ()).throw(ValueError("prefix missing"))
    )
    monkeypatch.setattr(
        updater,
        "check_against_official_snapshots",
        lambda names: checked.append(names) or {},
    )
    result = updater.update_daily(force_rebuild=True)
    assert checked == [updater.OFFICIAL_INDICES]
    assert "prefix missing" in result["hybrid_error"]
```

- [ ] **Step 2: Run focused updater tests and confirm failure**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py -k "update_daily" -q
```

Expected: FAIL because hybrid constants and orchestration are absent.

- [ ] **Step 3: Implement official-first hybrid orchestration**

After the official `install_instruments(OFFICIAL_INDICES)` call, run
`build_hybrid_outputs()` inside `try/except`. Only call
`install_instruments(HYBRID_INDICES)` on success. Always archive/check the four
official snapshots. Return `hybrid_error` as `None` or a concise exception
string.

- [ ] **Step 4: Make installation atomic**

For each source, copy to a temporary file inside `QLIB_INSTRUMENTS`, flush and
`os.replace` the final destination. A missing source raises before replacement.

- [ ] **Step 5: Add daily-entrypoint regression test**

Patch `update_indices_daily.update_daily` through its imported module boundary,
return an otherwise valid snapshot result plus `hybrid_error="prefix missing"`,
and assert `run()` returns `1`.

- [ ] **Step 6: Update the daily entrypoint**

Import `OFFICIAL_INDICES`, loop only over official snapshot checks, and mark
`ok = False` when `result["hybrid_error"]` is non-empty.

- [ ] **Step 7: Run updater-related tests**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py \
  tests/misc/test_csindex_v2.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit scheduler integration**

```bash
git add \
  scripts/data_collector/csindex_v2/updater.py \
  scripts/data_collector/update_indices_daily.py \
  tests/misc/test_csindex_hybrid_history.py \
  tests/misc/test_csindex_v2.py
git commit -m "feat(data): update hybrid indices in daily schedule"
```

---

### Task 6: Documentation, Local Preparation, and Verification

**Files:**
- Modify: `scripts/data_collector/csindex_v2/README.md`

**Interfaces:**
- Consumes: `hybrid_history` CLI and daily updater behavior from Tasks 1–5.
- Produces: Reproducible operator instructions and installed hybrid instrument files when credentials/cache permit.

- [ ] **Step 1: Document provenance and commands**

Add sections covering:

```bash
# One-time finite history preparation
/opt/anaconda3/envs/qlib/bin/python \
  -m scripts.data_collector.csindex_v2.hybrid_history prepare

# Rebuild only from frozen prefix plus latest official suffix
/opt/anaconda3/envs/qlib/bin/python \
  -m scripts.data_collector.csindex_v2.hybrid_history build

# Existing daily entrypoint; now refreshes hybrid suffixes too
/opt/anaconda3/envs/qlib/bin/python \
  -m scripts.data_collector.update_indices_daily
```

State explicitly that hybrid pools are approximate before 2015-11-30 and are
training-only until a separate Phase M experiment is approved.

- [ ] **Step 2: Run formatting and static checks**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m black --check \
  scripts/data_collector/csindex_v2/hybrid_history.py \
  scripts/data_collector/csindex_v2/hybrid_backfill.py \
  scripts/data_collector/csindex_v2/updater.py \
  scripts/data_collector/update_indices_daily.py \
  tests/misc/test_csindex_hybrid_history.py
/opt/anaconda3/envs/qlib/bin/python -m flake8 \
  scripts/data_collector/csindex_v2/hybrid_history.py \
  scripts/data_collector/csindex_v2/hybrid_backfill.py \
  scripts/data_collector/csindex_v2/updater.py \
  scripts/data_collector/update_indices_daily.py \
  tests/misc/test_csindex_hybrid_history.py
```

Expected: PASS. If Black 23.7.0 is available through pre-commit, use that pinned
version to avoid unrelated formatting drift.

- [ ] **Step 3: Run the focused regression suite**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_csindex_hybrid_history.py \
  tests/misc/test_csindex_v2.py \
  tests/misc/test_tushare_credentials.py \
  tests/misc/test_tushare_vwap.py -q
```

Expected: PASS.

- [ ] **Step 4: Run the one-time local preparation**

First check only whether `TUSHARE_TOKEN` is available without printing it. If
available, run:

```bash
/opt/anaconda3/envs/qlib/bin/python \
  -m scripts.data_collector.csindex_v2.hybrid_history prepare
```

If unavailable, run `build` only when frozen prefixes already exist; otherwise
report that code and tests are complete but the finite `total_mv` fetch remains
an operator step requiring the token.

- [ ] **Step 5: Verify installed/local outputs without Qlib multiprocessing**

Read the six text files directly and assert:

```python
assert hybrid_500["start"].min() <= "2010-01-29"
assert hybrid_1000["start"].min() <= "2010-01-29"
assert hybrid_500[hybrid_500.start >= "2015-11-30"].equals(official_500)
assert hybrid_1000[hybrid_1000.start >= "2015-11-30"].equals(official_1000)
```

Also compare daily active member sets from 2015-11-30 through the last local
calendar date.

- [ ] **Step 6: Commit documentation**

```bash
git add scripts/data_collector/csindex_v2/README.md
git commit -m "docs(data): document hybrid index history"
```

- [ ] **Step 7: Review final scope**

Run:

```bash
git status --short
git diff --stat HEAD~6..HEAD
git log -n 7 --oneline
```

Confirm unrelated `loss-design` files remain unstaged and unchanged.
