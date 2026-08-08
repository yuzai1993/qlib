# Tushare Trade-Date Batch Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the normal one-day Tushare stock update's per-symbol requests with two full-market trade-date requests while preserving data format, historical adjustment behavior, and a safe per-symbol fallback.

**Architecture:** `TushareCollectorCN.collector_data()` selects a batch path only when the requested range is exactly one day and no debug symbol limit is active. The batch path fetches `daily` and `adj_factor` once, validates the complete response before writing, filters to the collector's A-share universe, and reuses `BaseCollector.save_instrument`; any fetch or validation failure falls back to the existing collector before index data is downloaded.

**Tech Stack:** Python 3, pandas, Tushare Pro, pytest, Qlib data collector utilities.

## Global Constraints

- Preserve the existing source CSV schema and qlib normalization/dump behavior.
- Never replace or truncate a suspended stock's historical source file.
- Reject empty, wrong-date, malformed, duplicate, or limit-sized batch responses before writing.
- Preserve the existing per-symbol path for multi-day backfills, `limit_nums`, and batch failures.
- Do not expose or persist `TUSHARE_TOKEN`.

---

### Task 1: Batch response preparation and validation

**Files:**
- Create: `tests/misc/test_tushare_batch_collector.py`
- Modify: `scripts/data_collector/tushare/collector.py`

**Interfaces:**
- Consumes: Tushare `daily(trade_date=YYYYMMDD)` and `adj_factor(trade_date=YYYYMMDD)` data frames.
- Produces: `TushareCollectorCN._prepare_trade_date_batch(daily, adj, trade_date) -> pandas.DataFrame` with existing source columns plus internal `symbol`.

- [ ] **Step 1: Write failing tests**

Cover merge/schema conversion, universe filtering, missing adjustment fallback, and rejection of empty, wrong-date, duplicate, malformed, or 6000-row results.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_tushare_batch_collector.py -q`

Expected: FAIL because the batch preparation interface does not exist.

- [ ] **Step 3: Write minimal implementation**

Add constants for Tushare's 6000-row response ceiling and required fields, normalize `trade_date`/`vol`, merge adjustment factors on `ts_code,trade_date`, filter against `instrument_list`, and return deterministic date/symbol ordering.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_tushare_batch_collector.py -q`

Expected: PASS.

### Task 2: One-day batch routing and safe fallback

**Files:**
- Modify: `tests/misc/test_tushare_batch_collector.py`
- Modify: `scripts/data_collector/tushare/collector.py`

**Interfaces:**
- Produces: `TushareCollectorCN._collect_trade_date_batch() -> None` and `TushareCollectorCN._should_use_trade_date_batch() -> bool`.

- [ ] **Step 1: Write failing tests**

Verify exactly one `daily` and one `adj_factor` call, one save per returned active stock, no write for suspended stocks, per-symbol fallback on validation/API failure, and old routing for multi-day or `limit_nums` runs.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_tushare_batch_collector.py -q`

Expected: FAIL because routing still always calls `BaseCollector.collector_data()`.

- [ ] **Step 3: Write minimal implementation**

Track whether `limit_nums` was supplied, use the batch path only for a one-day production update, validate fully before any save, catch batch exceptions with a clear warning, then call the old collector as a complete fallback. Keep index collection after either stock path.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_tushare_batch_collector.py -q`

Expected: PASS.

### Task 3: Operations documentation and regression verification

**Files:**
- Modify: `scripts/data_collector/tushare/README.md`
- Test: `tests/misc/test_tushare_batch_collector.py`
- Test: `tests/misc/test_tushare_vwap.py`
- Test: `tests/misc/test_tushare_credentials.py`

**Interfaces:**
- Produces: documented fast path, fallback conditions, and observable log messages for operations.

- [ ] **Step 1: Document behavior**

Explain that normal one-day updates use two full-market calls, while multi-day/debug work and invalid batch responses use the legacy per-symbol path.

- [ ] **Step 2: Run focused regression tests**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_tushare_batch_collector.py tests/misc/test_tushare_vwap.py tests/misc/test_tushare_credentials.py -q`

Expected: PASS.

- [ ] **Step 3: Run live-trading regression tests**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading -q`

Expected: PASS.

- [ ] **Step 4: Review diff and operational safety**

Run: `git diff --check` and inspect `git diff -- scripts/data_collector/tushare/collector.py scripts/data_collector/tushare/README.md tests/misc/test_tushare_batch_collector.py`.

Expected: no whitespace errors, no credentials, no changes to live account routing.
