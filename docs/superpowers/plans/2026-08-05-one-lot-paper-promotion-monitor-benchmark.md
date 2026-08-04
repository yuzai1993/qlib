# One-Lot Paper Promotion and Monitor Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely retire the already-claimed 2026-08-05 Shadow batch, publish a one-lot LIVE batch for the simulation account, and label the monitor benchmark as CSI1000.

**Architecture:** Add a narrow ledger operation for an audited `SIMULATE -> LIVE` promotion and a separate fail-closed recovery module that archives an unexecuted QMT active batch without altering its contents. Keep the operational LIVE confirmation process-local, create only the 2026-08-05 daily marker, and retain QMT's 100-share hard cap. The monitor data path remains `SH000852`; only its hard-coded presentation label changes.

**Tech Stack:** Python 3.12, SQLite, pytest, Bash wrappers, QMT Python 3.6 bridge protocol, vanilla JavaScript/ECharts, SMB.

## Global Constraints

- QMT account environment remains exactly `SIMULATION`; `allow_real_money=false` remains unchanged.
- The 2026-08-05 authorization is one-day only; do not create a future `LIVE_OK` marker or an automatic marker job.
- The installed QMT strategy must retain `MAX_ORDER_QUANTITY=100`.
- Never edit the claimed Shadow JSONL, done marker, or active-state payload in place.
- Never remove the original shared files until verified archive copies and a SHA256 manifest are durable.
- macOS Qlib commands use `/opt/anaconda3/envs/qlib/bin/python` and must not run Qlib multiprocessing code from stdin/heredoc.
- The QMT strategy must be stopped in its UI before the destructive phase of Shadow retirement.

---

### Task 1: Audited Shadow-to-LIVE Ledger Promotion

**Files:**
- Modify: `live_trading/modules/fill_importer.py`
- Create: `live_trading/scripts/link_shadow_promotion.py`
- Test: `tests/live_trading/test_fill_importer.py`

**Interfaces:**
- Consumes: existing `LiveRecorder.get_batch(batch_id: str)` and the `batches`, `fills` tables.
- Produces: `LiveRecorder.promote_shadow_batch(source_batch_id: str, replacement_batch_id: str) -> bool`.
- CLI: `link_shadow_promotion.py --db-path PATH --source-batch ID --replacement-batch ID`.

- [ ] **Step 1: Write the failing happy-path test**

```python
def test_promote_shadow_batch_marks_unexecuted_same_session_replacement(env):
    _, recorder, _ = env
    old = "20260805_csi1000_b6m_b2s_postclose_001"
    new = "20260805_csi1000_b6m_b2s_postclose_002"
    recorder.record_batch(old, "2026-08-05", "SIMULATE", 2)
    recorder.record_batch(new, "2026-08-05", "LIVE", 2)

    assert recorder.promote_shadow_batch(old, new)
    assert not recorder.promote_shadow_batch(old, new)
    assert recorder.get_batch(old)["superseded_by"] == new
```

- [ ] **Step 2: Run the test and verify RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_fill_importer.py::test_promote_shadow_batch_marks_unexecuted_same_session_replacement -q`

Expected: FAIL with `AttributeError: 'LiveRecorder' object has no attribute 'promote_shadow_batch'`.

- [ ] **Step 3: Add failing safety tests**

Add parameterized cases that reject: source mode LIVE, replacement mode SIMULATE, different `trade_date`, different strategy key, any source row in `fills`, a superseded replacement, self-reference, and redirecting an already promoted source.

```python
with pytest.raises(SchemaError, match="unexecuted SIMULATE"):
    recorder.promote_shadow_batch(source, replacement)
```

- [ ] **Step 4: Implement the minimal ledger method**

Implement one SQLite transaction that loads both rows, applies the existing idempotency/conflict rules, requires source `SIMULATE`, replacement `LIVE`, identical date and strategy, and verifies:

```sql
SELECT COUNT(*) FROM fills WHERE batch_id=?
```

returns zero before updating `superseded_by` and `superseded_at`. Do not weaken `supersede_batch`, whose same-mode invariant remains intact.

- [ ] **Step 5: Verify GREEN and regression coverage**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_fill_importer.py -q`

Expected: all tests pass.

- [ ] **Step 6: Add the narrow ledger-link CLI**

Open the explicitly named SQLite database with `LiveRecorder`, call `promote_shadow_batch`, print a JSON result containing source, replacement and whether the row changed, and return nonzero on `SchemaError`. The CLI performs no SMB operation and accepts no mode override.

- [ ] **Step 7: Commit Task 1**

```bash
git add live_trading/modules/fill_importer.py live_trading/scripts/link_shadow_promotion.py tests/live_trading/test_fill_importer.py
git commit -m "feat(live): support audited shadow promotion"
```

### Task 2: Fail-Closed Claimed-Shadow Retirement Tool

**Files:**
- Create: `live_trading/modules/shadow_promotion.py`
- Create: `live_trading/scripts/retire_claimed_shadow.py`
- Create: `tests/live_trading/test_shadow_promotion.py`

**Interfaces:**
- Produces: `validate_unexecuted_state(payload: dict, batch_id: str) -> None`.
- Produces: `retire_claimed_shadow(bridge_root: Path, batch_id: str, *, execute: bool, now: datetime | None = None) -> dict` returning a manifest dictionary.
- CLI: `retire_claimed_shadow.py --bridge-root PATH --batch-id ID [--execute]`; omission of `--execute` is dry-run.

- [ ] **Step 1: Write failing state validation tests**

Use a valid fixture with `phase='SELL'`, `trading_started=False`, `execution_authorized=False`, `execution_live=False`, `submitted=[]`, and `fills={}`. Independently flip each safety field and assert `SchemaError` contains the unsafe field name. Assert a mismatched `batch_id` is rejected.

- [ ] **Step 2: Run validation tests and verify RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_shadow_promotion.py -q`

Expected: collection fails because `live_trading.modules.shadow_promotion` does not exist.

- [ ] **Step 3: Implement validation and dry-run inspection**

Resolve exactly these source paths:

```python
processing / f"signal_{batch_id}.jsonl"
processing / f"signal_{batch_id}.done"
state / f"active_{batch_id}.json"
```

Require all three regular files, parse the active JSON, validate the six fail-closed fields, compute SHA256 for every source, and return the proposed archive location without writing when `execute=False`.

- [ ] **Step 4: Write failing archive and rollback tests**

Assert `execute=True` creates `archive/operator_retired_<batch_id>_<timestamp>/`, copies three byte-identical files, writes `retirement_manifest.json`, verifies all hashes, then removes the three originals. Monkeypatch the copy verifier to fail and assert every original remains and no partial archive is accepted.

- [ ] **Step 5: Implement verified copy-then-remove retirement**

Use `shutil.copy2`, SHA256 re-read verification, an atomically replaced manifest, and only then `Path.unlink()` the known three explicit source paths. On failure, leave sources untouched and raise a descriptive exception; never recursively delete a shared directory.

- [ ] **Step 6: Add and test the CLI**

The CLI prints JSON for dry-run and actual execution. Require the explicit `--execute` switch for mutation and return nonzero on any validation/copy error.

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_shadow_promotion.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add live_trading/modules/shadow_promotion.py live_trading/scripts/retire_claimed_shadow.py tests/live_trading/test_shadow_promotion.py
git commit -m "feat(live): retire claimed shadow batches safely"
```

### Task 3: CSI1000 Monitor Presentation

**Files:**
- Modify: `live_trading/configs/csi1000_b6m_b2s_postclose.yaml`
- Modify: `live_trading/web/api.py`
- Modify: `live_trading/web/static/js/app.js`
- Modify: `tests/live_trading/test_live_config.py`
- Modify: `tests/live_trading/test_monitor_web_api.py`

**Interfaces:**
- Consumes: existing monitor snapshots whose benchmark values already come from `monitor.benchmark=SH000852`.
- Produces: API field `benchmark_name` and ECharts legend/series named `中证1000`.

- [ ] **Step 1: Write failing config and API behavior tests**

Assert the production CSI1000 config loads `monitor.benchmark_name == "中证1000"`, and `/api/overview` returns that value from a real temporary monitor app.

- [ ] **Step 2: Run the test and verify RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_live_config.py::test_load_new_csi1000_paper_config tests/live_trading/test_monitor_web_api.py::test_overview_exposes_active_account_and_batch -q`

Expected: FAIL because the config and overview response lack `benchmark_name`.

- [ ] **Step 3: Make the minimal presentation change**

Add `monitor.benchmark_name: "中证1000"`, expose it from `/api/overview`, and pass it into `drawNavChart` for both the legend and series name. Do not change snapshot calculations or historical data.

- [ ] **Step 4: Verify GREEN**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_live_config.py tests/live_trading/test_monitor_web_api.py tests/live_trading/test_snapshot.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add live_trading/configs/csi1000_b6m_b2s_postclose.yaml live_trading/web/api.py live_trading/web/static/js/app.js tests/live_trading/test_live_config.py tests/live_trading/test_monitor_web_api.py
git commit -m "fix(live): label monitor benchmark as csi1000"
```

### Task 4: Controlled 2026-08-05 Promotion and Verification

**Files:**
- Runtime only: `/Volumes/qmt_bridge/processing`, `/Volumes/qmt_bridge/archive`, `/Volumes/qmt_bridge/state`
- Runtime only: `live_trading/data/csi1000_b6m_b2s_postclose.db`
- Runtime only: `live_trading/logs/csi1000_b6m_b2s_postclose_publish_cron.log`

**Interfaces:**
- Consumes: Task 1 `promote_shadow_batch` and Task 2 retirement CLI.
- Produces: archived seq=1 Shadow batch, published seq=2 LIVE/SIMULATION batch, and one-day `LIVE_OK_2026-08-05`.

- [ ] **Step 1: Run repository verification before external mutation**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading -q`

Expected: all tests pass.

- [ ] **Step 2: Run the retirement dry-run**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/retire_claimed_shadow.py \
  --bridge-root /Volumes/qmt_bridge \
  --batch-id 20260805_csi1000_b6m_b2s_postclose_001
```

Expected: JSON reports all three sources, `trading_started=false`, no submitted/fills, and no filesystem mutation.

- [ ] **Step 3: Stop QMT bridge strategy in the Windows QMT UI**

This is the sole required manual action. Do not continue until the user confirms the strategy is stopped; Mac SMB access cannot prove the QMT process has released its in-memory batch.

- [ ] **Step 4: Execute verified Shadow retirement**

Run the Step 2 command with `--execute`. Verify the manifest hashes, the three original paths are absent, and all archive copies are present. This is recoverable archival, not deletion.

- [ ] **Step 5: Publish seq=2 with process-local LIVE confirmation**

Run with `LIVE_RUN_MODE=LIVE` and `LIVE_TRADING_CONFIRM=YES` supplied only to this process; leave the persistent cron environment in SIMULATE so 2026-08-06 is not automatically authorized:

```bash
LIVE_RUN_MODE=LIVE LIVE_TRADING_CONFIRM=YES \
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_publish_signals.py \
  --config csi1000_b6m_b2s_postclose \
  --trade-date 2026-08-05 --mode LIVE --seq 2
```

- [ ] **Step 6: Link the old and replacement batches in the ledger**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/link_shadow_promotion.py \
  --db-path live_trading/data/csi1000_b6m_b2s_postclose.db \
  --source-batch 20260805_csi1000_b6m_b2s_postclose_001 \
  --replacement-batch 20260805_csi1000_b6m_b2s_postclose_002
```

Verify seq=1 points to seq=2 and active queries return only seq=2.

- [ ] **Step 7: Create only today's daily execution marker**

Create `/Volumes/qmt_bridge/state/LIVE_OK_2026-08-05`. Verify no `LIVE_OK_2026-08-06` or later marker exists.

- [ ] **Step 8: Restart QMT bridge strategy and inspect claim state**

Ask the user to restart the strategy. Verify seq=2 moves to processing, the processing JSONL header has `mode=LIVE`, the active state has `execution_live=false` before 15:05, and no quantity can exceed the installed `MAX_ORDER_QUANTITY=100` gate.

- [ ] **Step 9: Restart and verify the monitor**

Restart the existing LaunchAgent, request `/api/overview`, `/api/predictions?date=2026-08-04`, and `/`. Verify the API reports `mode=LIVE`, predictions still contain 1,000 CSI1000 instruments with names, and the served JavaScript contains `中证1000` but not `沪深300`.

- [ ] **Step 10: Final safety audit**

Verify: simulation account ID matches the batch header; `account_environment=SIMULATION`; `allow_real_money=false`; seq=2 contains two order rows; SMB is writable; no future LIVE_OK exists; the QMT source still contains `MAX_ORDER_QUANTITY = 100`; and `git status --short` contains no unintended files.

### Task 5: Final Integrated Commit

**Files:**
- Modify: `docs/superpowers/plans/2026-08-05-one-lot-paper-promotion-monitor-benchmark.md` only if execution notes reveal a correction.

**Interfaces:**
- Consumes: all prior task results.
- Produces: a reviewed branch with complete tests and operational evidence.

- [ ] **Step 1: Run final targeted verification**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_fill_importer.py \
  tests/live_trading/test_shadow_promotion.py \
  tests/live_trading/test_repository_boundaries.py \
  tests/live_trading/test_monitor_web_api.py \
  tests/live_trading/test_run_publish_signals.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Inspect final diff and history**

Run: `git diff HEAD~3 --check` and `git status --short`.

Expected: no whitespace errors and no unrelated changes.

- [ ] **Step 3: Record any final documentation-only correction**

If and only if runtime evidence required a factual correction, update this plan, rerun `git diff --check`, and commit with `docs(live): record one-lot promotion runbook correction`. Otherwise create no empty commit.
