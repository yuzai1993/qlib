# Live Monitor Active Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live monitor show account `88813528` and batch `20260715_csi300_topk10_003` as the only effective 2026-07-15 execution plan while retaining `001/002` as explicitly superseded audit records.

**Architecture:** Persist replacement relationships in the SQLite `batches` ledger instead of inferring them from shared-directory locations. Monitoring consumes active-only queries, while audit APIs continue returning all batches with a derived lifecycle status. The read-only FastAPI/JavaScript dashboard surfaces the effective account and replacement relationship.

**Tech Stack:** Python 3.12, SQLite/WAL, pytest, FastAPI TestClient, native JavaScript SPA, uvicorn.

## Global Constraints

- Do not delete historical batches, orders, fills, or database rows.
- Keep the Web API and SPA read-only; replacement writes remain an operator-side ledger operation.
- Do not modify the QMT signal protocol, batch `003`, or `LIVE_OK_2026-07-15`.
- Keep `list_batches()` as the all-history audit query; active filtering uses explicit methods.
- A superseded batch contributes zero operational `missing`, but its original reconcile result remains available as `raw_missing` and in batch detail.
- Follow red-green-refactor for every behavior change and commit each independently testable task.

---

### Task 1: Persist immutable batch replacement relationships

**Files:**
- Modify: `live_trading/modules/fill_importer.py:91-207, 296-452, 931-944`
- Modify: `tests/live_trading/test_fill_importer.py`

**Interfaces:**
- Produces: `LiveRecorder.supersede_batch(old_batch_id: str, new_batch_id: str) -> bool`
- Produces: `LiveRecorder.get_active_batches_by_date(trade_date: str) -> list[dict]`
- Produces: `LiveRecorder.get_latest_active_batch(mode: str | None = None) -> dict | None`
- Preserves: `LiveRecorder.list_batches(limit: int = 10) -> list[dict]` returns all batches.

- [ ] **Step 1: Write failing schema and lifecycle tests**

Add tests that create `001`, `002`, and `003` on the same date/mode, then assert:

```python
def test_supersede_batch_is_idempotent_and_active_queries_exclude_old(env):
    _, recorder, _ = env
    for seq in (1, 2, 3):
        recorder.record_batch(
            f"20260715_csi300_topk10_{seq:03d}",
            "2026-07-15", "LIVE", 10,
        )

    assert recorder.supersede_batch(
        "20260715_csi300_topk10_001",
        "20260715_csi300_topk10_003",
    )
    assert not recorder.supersede_batch(
        "20260715_csi300_topk10_001",
        "20260715_csi300_topk10_003",
    )
    recorder.supersede_batch(
        "20260715_csi300_topk10_002",
        "20260715_csi300_topk10_003",
    )

    active = recorder.get_active_batches_by_date("2026-07-15")
    assert [row["batch_id"] for row in active] == [
        "20260715_csi300_topk10_003"
    ]
    assert recorder.get_latest_active_batch("LIVE")["batch_id"].endswith("_003")
    all_rows = {row["batch_id"]: row for row in recorder.list_batches(limit=20)}
    assert all_rows["20260715_csi300_topk10_001"]["superseded_by"].endswith("_003")
```

Add validation tests for unknown ids, self-replacement, different trade date/mode/strategy, and conflicting second replacement.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q \
  tests/live_trading/test_fill_importer.py -k 'supersede or active_queries'
```

Expected: FAIL because `supersede_batch` and active query methods do not exist.

- [ ] **Step 3: Add schema migration and minimal lifecycle implementation**

Extend new-table DDL and old-database migration with nullable `superseded_by` and `superseded_at`. Implement a private strategy-key helper for legacy rows:

```python
@staticmethod
def _batch_strategy_key(row):
    if row["strategy_id"]:
        return row["strategy_id"]
    stem, _seq = row["batch_id"].rsplit("_", 1)
    _date, strategy = stem.split("_", 1)
    return strategy
```

Implement `supersede_batch` in one `_conn()` transaction. Return `False` only when the exact relationship already exists; otherwise raise `SchemaError` for the invalid cases listed in Step 1. Update only:

```sql
UPDATE batches
SET superseded_by=?, superseded_at=datetime('now', 'localtime')
WHERE batch_id=?
```

Implement active queries with `WHERE superseded_by IS NULL`, deterministic ordering by `trade_date DESC, batch_id DESC`, and optional exact mode filtering for `get_latest_active_batch`.

- [ ] **Step 4: Run lifecycle and legacy migration tests**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q \
  tests/live_trading/test_fill_importer.py
```

Expected: all tests PASS, including the existing legacy database migration test.

- [ ] **Step 5: Commit Task 1**

```bash
git add live_trading/modules/fill_importer.py tests/live_trading/test_fill_importer.py
git commit -m "feat(live_trading): track superseded signal batches"
```

---

### Task 2: Exclude superseded batches from operational monitoring

**Files:**
- Modify: `live_trading/scripts/run_monitor.py:85-137`
- Modify: `tests/live_trading/test_next_trade_date.py`
- Modify: `tests/live_trading/test_pipeline_monitor.py`

**Interfaces:**
- Consumes: `LiveRecorder.get_active_batches_by_date(trade_date)` from Task 1.
- Consumes: `LiveRecorder.get_latest_active_batch(mode=None)` and active batch rows.
- Produces: postmarket/evening findings based only on active plans.

- [ ] **Step 1: Write failing postmarket active-only test**

Create a temporary recorder with old/new same-day LIVE batches, supersede old, monkeypatch `FillImporter.reconcile` to record batch ids, and call `run_postmarket`:

```python
seen = []
monkeypatch.setattr(
    run_monitor.FillImporter,
    "reconcile",
    lambda self, batch_id: seen.append(batch_id) or {
        "planned": 1, "terminal": 0, "missing": 1,
        "rejected": 0, "errors": 0,
    },
)
run_monitor.run_postmarket("2026-07-15", recorder, store, config)
assert seen == ["20260715_csi300_topk10_003"]
```

Extend the evening test recorder so it provides active rows and assert a superseded higher/lower sequence is ignored.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q \
  tests/live_trading/test_pipeline_monitor.py \
  tests/live_trading/test_next_trade_date.py
```

Expected: FAIL because monitoring still calls all-history batch queries.

- [ ] **Step 3: Switch monitoring to active-only queries**

Change `run_postmarket` to:

```python
batches = recorder.get_active_batches_by_date(date)
```

Change `run_evening` to call `recorder.get_active_batches_by_date(next_day)` after resolving the Tushare date, then select the highest batch_id from those rows. Do not add another list interface.

- [ ] **Step 4: Run monitoring tests**

Run the command from Step 2. Expected: all PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add live_trading/scripts/run_monitor.py \
  tests/live_trading/test_pipeline_monitor.py \
  tests/live_trading/test_next_trade_date.py
git commit -m "fix(live_trading): monitor only active batches"
```

---

### Task 3: Expose account and lifecycle status through the API

**Files:**
- Modify: `live_trading/web/api.py:45-128`
- Modify: `tests/live_trading/test_monitor_web_api.py`

**Interfaces:**
- Consumes: `get_latest_active_batch("LIVE")` and all-history `list_batches()`.
- Produces: `/api/overview.account_id`, `/api/overview.active_batch_id`.
- Produces: `/api/batches[].lifecycle_status`, `superseded_by`, `raw_missing`, and operational `missing`.

- [ ] **Step 1: Convert the API fixture to a durable account-aware batch**

Build the existing `BATCH` with `BatchHeader(account_id="88813528", mode="LIVE", ...)`, `SignalOrder` objects, and `record_publish_plan`. Add an old same-date LIVE batch, then call `supersede_batch(old, BATCH)`.

Add failing assertions:

```python
def test_overview_exposes_active_account_and_batch(client):
    data = client.get("/api/overview").json()
    assert data["account_id"] == "88813528"
    assert data["active_batch_id"] == BATCH

def test_batches_mark_superseded_rows_without_operational_missing(client):
    rows = {row["batch_id"]: row for row in client.get("/api/batches").json()}
    old = rows[OLD_BATCH]
    assert old["lifecycle_status"] == "SUPERSEDED"
    assert old["superseded_by"] == BATCH
    assert old["raw_missing"] == old["planned"]
    assert old["missing"] == 0
    assert rows[BATCH]["lifecycle_status"] == "ACTIVE"
    assert rows[BATCH]["account_id"] == "88813528"
```

- [ ] **Step 2: Run API tests and verify RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q \
  tests/live_trading/test_monitor_web_api.py
```

Expected: FAIL on missing overview and lifecycle fields.

- [ ] **Step 3: Implement minimal API projection**

In `overview`, select `active = recorder.get_latest_active_batch("LIVE")` once and add:

```python
"account_id": active.get("account_id", "") if active else "",
"active_batch_id": active.get("batch_id", "") if active else "",
```

In `batches`, preserve the reconcile dict but project lifecycle fields:

```python
raw_missing = r["missing"]
superseded = bool(b.get("superseded_by"))
result.append({
    **b,
    **r,
    "raw_missing": raw_missing,
    "missing": 0 if superseded else raw_missing,
    "lifecycle_status": "SUPERSEDED" if superseded else "ACTIVE",
})
```

- [ ] **Step 4: Run API tests**

Run the command from Step 2. Expected: all PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add live_trading/web/api.py tests/live_trading/test_monitor_web_api.py
git commit -m "feat(live_trading): expose active account in monitor api"
```

---

### Task 4: Render active account and superseded status in the SPA

**Files:**
- Modify: `live_trading/web/static/js/app.js:35-55, 174-204`
- Modify: `live_trading/web/static/css/style.css`
- Modify: `tests/live_trading/test_monitor_web_api.py`

**Interfaces:**
- Consumes: API fields produced in Task 3.
- Produces: escaped account/batch display and status-aware batch table.

- [ ] **Step 1: Write failing static contract test**

Add a test that reads `app.js` from `REPO_ROOT` and asserts stable field tokens and labels are present:

```python
def test_spa_renders_account_and_batch_lifecycle():
    js = (REPO_ROOT / "live_trading/web/static/js/app.js").read_text()
    assert "ov.account_id" in js
    assert "ov.active_batch_id" in js
    assert "lifecycle_status" in js
    assert "已废弃" in js
    assert "账号" in js
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q \
  tests/live_trading/test_monitor_web_api.py::test_spa_renders_account_and_batch_lifecycle
```

Expected: FAIL because the SPA does not use those fields.

- [ ] **Step 3: Implement the status-aware display**

Update the top badge to include `账号 ${ov.account_id || '—'}` through `esc`. Add `有效批次 ${ov.active_batch_id || '—'}` to the overview heading.

Expand the batch table with account and status columns. ACTIVE rows use the existing green badge class; SUPERSEDED rows use a new muted class and render:

```javascript
const isSuperseded = b.lifecycle_status === 'SUPERSEDED';
const status = isSuperseded
    ? `<span class="badge badge-muted">已废弃 → ${esc(b.superseded_by)}</span>`
    : '<span class="badge badge-ok">有效</span>';
const missing = isSuperseded ? '—' : b.missing;
```

Update the detail-row `colspan` from 7 to 9. Add `.badge-muted` and an optional `.row-muted` color rule without changing layout structure.

- [ ] **Step 4: Run API/static tests**

Run the full `test_monitor_web_api.py`. Expected: all PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add live_trading/web/static/js/app.js \
  live_trading/web/static/css/style.css \
  tests/live_trading/test_monitor_web_api.py
git commit -m "feat(live_trading): show active account and batch status"
```

---

### Task 5: Migrate formal data, restart 8081, and verify deployment

**Files:**
- Modify at runtime: `live_trading/data/csi300_topk10_live.db`
- Runtime service: `live_trading/scripts/run_web.py --config csi300_topk10_live --host 127.0.0.1 --port 8081`
- Verify: `/Volumes/qmt_bridge/inbox`, `/Volumes/qmt_bridge/archive`

**Interfaces:**
- Consumes all code from Tasks 1-4.
- Produces the formal `001/002 -> 003` ledger state and refreshed local Web service.

- [ ] **Step 1: Run complete regression before formal mutation**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q \
  tests/live_trading tests/paper_trading/test_signal_generator.py
```

Expected: zero failures.

- [ ] **Step 2: Back up and migrate the formal ledger**

Use SQLite `.backup` to create a new timestamped pre-monitor-sync backup beside the database. Instantiate `LiveRecorder` to add the nullable columns, then call:

```python
recorder.supersede_batch(
    "20260715_csi300_topk10_001",
    "20260715_csi300_topk10_003",
)
recorder.supersede_batch(
    "20260715_csi300_topk10_002",
    "20260715_csi300_topk10_003",
)
```

Query all three rows and assert only `003` has `superseded_by IS NULL`, with `account_id=88813528`.

- [ ] **Step 3: Verify shared execution state is unchanged**

Assert:

- inbox contains only `signal_20260715_csi300_topk10_003.{jsonl,done}`;
- archive contains `signal_20260715_csi300_topk10_002.{jsonl,done}`;
- processing contains no `001/002/003` signal;
- `LIVE_OK_2026-07-15` state is unchanged.

- [ ] **Step 4: Restart the exact 8081 monitor process**

Read the listening PID with `lsof -nP -iTCP:8081 -sTCP:LISTEN`, verify its command contains `live_trading/scripts/run_web.py --config csi300_topk10_live`, terminate only that PID, and start the same command bound to `127.0.0.1:8081`. Write stdout/stderr to `live_trading/logs/web.log`.

- [ ] **Step 5: Verify the live API and static page locally**

Run local requests and assert:

```text
/api/overview -> account_id=88813528, active_batch_id=..._003
/api/batches  -> 001/002 SUPERSEDED, 003 ACTIVE
/              -> HTTP 200 and includes the updated static assets
```

Do not invoke notifier or any endpoint that sends external messages.

- [ ] **Step 6: Run fresh completion verification**

Run the full regression from Step 1, `python -m py_compile` for changed Python files, `git diff --check`, and `git status --short`. Expected: all tests pass, compilation exits 0, no whitespace errors, and only intentional uncommitted runtime files (normally none).

- [ ] **Step 7: Commit any final documentation-only adjustment**

If no tracked documentation changed during deployment, skip this commit. Otherwise stage only the documented files and commit with:

```bash
git commit -m "docs(live_trading): document active batch monitoring"
```
