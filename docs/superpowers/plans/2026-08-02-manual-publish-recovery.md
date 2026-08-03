# Manual Publish Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the scheduled 22:05 blind retry while preserving an explicit, actionable manual recovery path from the 22:15 evening alert.

**Architecture:** Keep the existing publisher and catch-up wrappers unchanged as execution mechanisms, but remove catch-up from the cron template. Extend the pure evening monitor rule with the deployment config ID so each `PUBLISH_MISSING` finding can name the correct log and select the recovery command based on whether a durable batch already exists.

**Tech Stack:** Bash cron wrappers, Python 3, pytest, SQLite-backed `LiveRecorder`, ServerChan notifier through the existing monitor dispatcher.

## Global Constraints

- Do not install or modify the user's actual crontab.
- Keep `run_publish_catchup_cron.sh` as a manual tool; do not delete or rename it.
- Do not change QMT order submission, account access, `LIVE_OK`, Shadow, one-lot, or full-paper gates.
- Preserve all existing model SHA, Live/Backtest parity, account-environment, publish-lock, and immutable-batch checks.
- A durable batch with missing shared files must use `run_publish_cron.sh <config_id> <trade_date>`; catch-up intentionally skips durable batches.

---

### Task 1: Actionable Evening Publish Alerts

**Files:**
- Modify: `tests/live_trading/test_pipeline_monitor.py:22-48`
- Modify: `tests/live_trading/test_next_trade_date.py:37-95`
- Modify: `live_trading/modules/pipeline_monitor.py:25-48`
- Modify: `live_trading/scripts/run_monitor.py:103-118`

**Interfaces:**
- Consumes: `check_evening(next_trade_date: str, batch: dict | None, inbox_files: list[str] | None, config_id: str) -> list[Finding]`.
- Produces: every `PUBLISH_MISSING` message includes `live_trading/logs/<config_id>_publish_cron.log` plus a state-appropriate manual command.
- Produces: `run_evening(date, recorder, config)` passes `config["live"]["strategy_id"]` to `check_evening`.

- [ ] **Step 1: Write failing alert-content tests**

Update the direct evening checks to pass `CONFIG_ID = "csi1000_b6m_b2s_postclose"` and assert the exact recovery path:

```python
CONFIG_ID = "csi1000_b6m_b2s_postclose"
PUBLISH_LOG = f"live_trading/logs/{CONFIG_ID}_publish_cron.log"


def test_evening_no_batch():
    f = check_evening("2026-07-14", None, [], CONFIG_ID)
    assert _rules(f) == ["PUBLISH_MISSING"] and f[0].level == "CRIT"
    assert PUBLISH_LOG in f[0].message
    assert (
        f"bash live_trading/run_publish_catchup_cron.sh {CONFIG_ID}"
        in f[0].message
    )


def test_evening_missing_done_file():
    f = check_evening("2026-07-14", BATCH, [FILES_OK[0]], CONFIG_ID)
    assert _rules(f) == ["PUBLISH_MISSING"]
    assert PUBLISH_LOG in f[0].message
    assert (
        f"bash live_trading/run_publish_cron.sh {CONFIG_ID} 2026-07-14"
        in f[0].message
    )


def test_evening_inbox_unavailable():
    f = check_evening("2026-07-14", BATCH, None, CONFIG_ID)
    assert _rules(f) == ["PUBLISH_MISSING"]
    assert "不可访问" in f[0].message
    assert "先恢复 SMB 挂载" in f[0].message
    assert PUBLISH_LOG in f[0].message
    assert (
        f"bash live_trading/run_publish_cron.sh {CONFIG_ID} 2026-07-14"
        in f[0].message
    )
```

Update `test_evening_ok` to pass `CONFIG_ID`. In the two `run_evening` tests, add
`"strategy_id": CONFIG_ID` beside `bridge_root` so the integration path verifies
that the config ID is propagated.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py \
  tests/live_trading/test_next_trade_date.py -q
```

Expected: failures report that `check_evening` accepts only three arguments or that alert messages lack the recovery command.

- [ ] **Step 3: Add one recovery-hint formatter and thread the config ID through**

Add this focused helper in `pipeline_monitor.py` and append it to each critical publish message:

```python
def _publish_recovery_hint(config_id, trade_date, has_batch):
    log_path = f"live_trading/logs/{config_id}_publish_cron.log"
    if has_batch:
        command = (
            "bash live_trading/run_publish_cron.sh "
            f"{config_id} {trade_date}"
        )
    else:
        command = (
            "bash live_trading/run_publish_catchup_cron.sh "
            f"{config_id}"
        )
    return f"；发布日志：{log_path}；人工恢复：{command}"
```

Change the rule signature to:

```python
def check_evening(next_trade_date, batch, inbox_files, config_id) -> list:
```

For inaccessible inbox messages, insert `先恢复 SMB 挂载` before the helper text.
In `run_monitor.py`, read and pass the deployment identity once:

```python
config_id = config["live"]["strategy_id"]
```

Use `config_id` in both the no-batch and batch-present calls to `check_evening`.

- [ ] **Step 4: Run focused tests and verify they pass**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py \
  tests/live_trading/test_next_trade_date.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the actionable alert change**

```bash
git add live_trading/modules/pipeline_monitor.py \
  live_trading/scripts/run_monitor.py \
  tests/live_trading/test_pipeline_monitor.py \
  tests/live_trading/test_next_trade_date.py
git commit -m "fix(live): make publish alerts actionable"
```

### Task 2: Remove Scheduled Catch-up and Document Manual Recovery

**Files:**
- Modify: `tests/live_trading/test_operational_wrappers.py`
- Modify: `live_trading/crontab.csi1000_postclose.example`
- Modify: `live_trading/run_publish_catchup_cron.sh:1-4`
- Modify: `live_trading/README.md:104-131`

**Interfaces:**
- Consumes: the existing manual wrappers `run_publish_catchup_cron.sh [config_id]` and `run_publish_cron.sh [config_id] [trade_date]`.
- Produces: a cron template containing 21:00 publish and 22:15 evening monitor but no automatic catch-up entry.
- Produces: operator documentation that distinguishes no-durable-batch catch-up from durable-batch shared-file recovery.

- [ ] **Step 1: Write the failing cron-policy test**

Add to `tests/live_trading/test_operational_wrappers.py`:

```python
def test_crontab_uses_alert_then_manual_catchup_policy():
    text = (
        REPO_ROOT / "live_trading" / "crontab.csi1000_postclose.example"
    ).read_text(encoding="utf-8")
    assert "run_publish_catchup_cron.sh" not in text
    assert "run_publish_cron.sh csi1000_b6m_b2s_postclose" in text
    assert "run_monitor_cron.sh evening csi1000_b6m_b2s_postclose" in text
```

- [ ] **Step 2: Run the policy test and verify it fails**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_operational_wrappers.py::test_crontab_uses_alert_then_manual_catchup_policy -q
```

Expected: FAIL because the cron template still contains `run_publish_catchup_cron.sh`.

- [ ] **Step 3: Remove only the scheduled retry and update operator guidance**

Delete this line from the cron example:

```cron
5 22 * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/run_publish_catchup_cron.sh csi1000_b6m_b2s_postclose
```

Change the catch-up wrapper header to state that it is manual-only and must not be
installed in cron. Update the README schedule to list 21:00 and 22:15 only. In the
daily commands section, document:

```bash
# 数据库没有下一交易日批次时人工补发
bash live_trading/run_publish_catchup_cron.sh csi1000_b6m_b2s_postclose

# 数据库已有批次但共享文件缺失时，按明确交易日幂等重发
bash live_trading/run_publish_cron.sh \
  csi1000_b6m_b2s_postclose YYYY-MM-DD
```

State that operators should inspect
`live_trading/logs/csi1000_b6m_b2s_postclose_publish_cron.log` before retrying.

- [ ] **Step 4: Run policy, monitor, and shell verification**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py \
  tests/live_trading/test_next_trade_date.py \
  tests/live_trading/test_operational_wrappers.py -q
bash -n live_trading/run_publish_cron.sh
bash -n live_trading/run_publish_catchup_cron.sh
bash -n live_trading/run_monitor_cron.sh
git diff --check
```

Expected: pytest passes, all Bash syntax checks exit 0, and `git diff --check` emits no output.

- [ ] **Step 5: Commit the scheduling policy change**

```bash
git add live_trading/crontab.csi1000_postclose.example \
  live_trading/run_publish_catchup_cron.sh \
  live_trading/README.md \
  tests/live_trading/test_operational_wrappers.py
git commit -m "chore(live): require manual publish recovery"
```

### Task 3: Final Regression Verification

**Files:**
- Verify: `live_trading/`
- Verify: `tests/live_trading/`

**Interfaces:**
- Consumes: completed Task 1 and Task 2 behavior.
- Produces: evidence that manual recovery guidance did not regress the broader live-trading suite.

- [ ] **Step 1: Run the complete live-trading test suite**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading -q
```

Expected: all live-trading tests pass.

- [ ] **Step 2: Verify the final schedule and repository diff**

Run:

```bash
rg -n "run_publish_(cron|catchup_cron)|run_monitor_cron.sh evening" \
  live_trading/crontab.csi1000_postclose.example live_trading/README.md
git diff --check
git status --short
```

Expected: the cron template shows 21:00 publish and 22:15 evening monitor only;
the README retains catch-up solely as a manual command; no whitespace errors or
uncommitted implementation changes remain.
