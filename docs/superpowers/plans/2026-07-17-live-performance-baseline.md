# Live Performance Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate first-day live account, benchmark, cumulative, and excess returns without adding a visible pre-2026-07-16 snapshot.

**Architecture:** Validate an optional `monitor.performance_baseline` section when loading live configuration. The report pipeline selects a synthetic previous snapshot only for the configured first snapshot date and only when no real earlier snapshot exists; the existing pure snapshot formulas then calculate returns normally.

**Tech Stack:** Python 3.12, YAML, SQLite, pytest, Qlib monitoring pipeline.

## Global Constraints

- Do not create a `daily_snapshot` row dated before 2026-07-16.
- The baseline applies only to `first_snapshot_date: "2026-07-16"` when no real earlier snapshot exists.
- Real prior snapshots always take precedence over the configured baseline.
- Rebuild the local snapshot without sending an external daily notification.

---

### Task 1: Validate and apply the performance baseline

**Files:**
- Modify: `live_trading/modules/live_config.py`
- Modify: `live_trading/scripts/run_monitor.py`
- Modify: `live_trading/configs/csi300_topk10_live.yaml`
- Test: `tests/live_trading/test_live_config.py`
- Test: `tests/live_trading/test_snapshot.py`

**Interfaces:**
- Produces: `_validate_performance_baseline(config: dict) -> None` in `live_config.py`.
- Produces: `_previous_performance_snapshot(date: str, previous: list, config: dict) -> dict | None` in `run_monitor.py`.
- Consumes: existing `build_snapshot(..., prev_snapshot=...)` without changing its signature.

- [ ] **Step 1: Write failing configuration-validation tests**

Add tests proving a complete positive baseline loads, while missing keys, non-ISO dates, zero/negative values, booleans, and non-numeric values raise `ValueError` mentioning `performance_baseline`.

- [ ] **Step 2: Run the validation tests and verify RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q tests/live_trading/test_live_config.py
```

Expected: new invalid-baseline tests fail because `load_live_config` currently accepts them.

- [ ] **Step 3: Implement minimal configuration validation**

Parse `first_snapshot_date` with `datetime.date.fromisoformat`; require exactly the three documented keys to exist and require both numeric values to be finite and greater than zero. Call the validator after merging base and live YAML.

- [ ] **Step 4: Run configuration tests and verify GREEN**

Run the command from Step 2 and expect all tests to pass.

- [ ] **Step 5: Write failing baseline-selection and return tests**

Add tests proving:

```python
baseline = {
    "monitor": {"performance_baseline": {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": 10_000_000.0,
        "benchmark_close": 4786.78271484375,
    }}
}
```

produces a synthetic prior snapshot only on 2026-07-16 with no real prior row; a real prior row wins; missing config or a different date returns `None`; and passing the synthetic row to `build_snapshot` yields account `0.0053288441536663`, benchmark `-0.018456686`, and excess `0.023785530` approximately.

- [ ] **Step 6: Run the selection tests and verify RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q tests/live_trading/test_snapshot.py
```

Expected: import or attribute failure because `_previous_performance_snapshot` does not exist.

- [ ] **Step 7: Implement baseline selection and wire it into reports**

Add `_previous_performance_snapshot`; update `run_report` to pass its result to `build_snapshot`; add the exact approved baseline values to the live YAML.

- [ ] **Step 8: Run focused and full regression tests**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest -q tests/live_trading/test_live_config.py tests/live_trading/test_snapshot.py tests/live_trading/test_pipeline_monitor.py
/opt/anaconda3/envs/qlib/bin/python -m pytest -q tests/live_trading tests/paper_trading/test_signal_generator.py
```

Expected: all tests pass.

- [ ] **Step 9: Commit the implementation**

```bash
git add live_trading/modules/live_config.py live_trading/scripts/run_monitor.py live_trading/configs/csi300_topk10_live.yaml tests/live_trading/test_live_config.py tests/live_trading/test_snapshot.py
git commit -m "fix(live_trading): seed first-day performance baseline"
```

### Task 2: Rebuild and verify the formal first-day snapshot

**Files:**
- Modify operational data: `live_trading/data/csi300_topk10_live.db`

**Interfaces:**
- Consumes: the configured baseline and `run_report` with a local null notifier.
- Produces: corrected 2026-07-16 `daily_snapshot` and position snapshot rows.

- [ ] **Step 1: Rebuild locally without external notification**

Run `run_report` for 2026-07-16 with `daily_report` disabled in the in-memory config and `NullNotifier`; record the latest report pipeline status locally.

- [ ] **Step 2: Verify database values and history boundary**

Assert no `daily_snapshot` exists before 2026-07-16 and verify rounded values: account daily/cumulative `0.005328844`, benchmark daily/cumulative `-0.018456686`, excess `0.023785530`.

- [ ] **Step 3: Verify the local monitoring API**

Read `/api/overview` and `/api/nav`; confirm all three return cards are populated and the latest report stage is `OK`.

- [ ] **Step 4: Final repository verification**

Run `git diff --check`, confirm the worktree is clean, and report the test count and operational values.
