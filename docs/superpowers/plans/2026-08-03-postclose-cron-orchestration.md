# CSI1000 Post-close Cron Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install a fail-closed paper-trading schedule with a serialized 20:00 receipt/data/report pipeline, 21:30 publish, and 22:30 integrity check.

**Architecture:** A new Bash wrapper owns the 20:00 pipeline and a configuration-scoped postclose lock. Import and receipt-check failures are recorded but do not block data collection; report runs only after a successful update. Publish and manual catch-up refuse to overlap that lock, and failed publication is recovered manually after an actionable 22:30 alert.

**Tech Stack:** Bash, cron, Python 3, pytest, Qlib file provider, SQLite, ServerChan.

## Global Constraints

- Keep `LIVE_RUN_MODE=SIMULATE`, `LIVE_TRADING_CONFIRM` unset, and `LIVE_OK` absent.
- Do not initialize the durable ledger or install actual cron entries until empty-account cash reconciles.
- Do not schedule automatic catch-up.
- Do not run report after failed data update.
- Preserve all existing model, parity, account-environment, immutable-batch, and per-job lock gates.
- This is deployment engineering, not an experiment; do not touch the experiment registry.

---

### Task 1: Serialized 20:00 Wrapper

**Files:**
- Create: `live_trading/run_postclose_cron.sh`
- Modify: `tests/live_trading/test_operational_wrappers.py`

**Interfaces:**
- Consumes: optional config ID with the existing `LIVE_CONFIG_ID` fallback pattern.
- Consumes: `run_import_cron.sh`, `run_monitor_cron.sh`, and `scripts/data_collector/tushare/run_update_to_bin.sh`.
- Produces: `logs/<config_id>_postclose_cron.log`, aggregate status, and `.locks/<config_id>_postclose.lock`.

- [ ] **Step 1: Write failing behavior tests**

Create a temporary project layout with fake executable child scripts that append stage names to `POSTCLOSE_TEST_TRACE`. Add these assertions:

```python
def test_postclose_continues_to_update_after_import_failure(tmp_path):
    result, trace = _run_postclose_fixture(tmp_path, import_status=1)
    assert result.returncode != 0
    assert trace == ["import", "postmarket", "update", "report"]


def test_postclose_skips_report_when_update_fails(tmp_path):
    result, trace = _run_postclose_fixture(tmp_path, update_status=1)
    assert result.returncode != 0
    assert trace == ["import", "postmarket", "update"]
    assert "report skipped: market data update failed" in result.stdout


def test_postclose_success_is_serial_and_zero(tmp_path):
    result, trace = _run_postclose_fixture(tmp_path)
    assert result.returncode == 0
    assert trace == ["import", "postmarket", "update", "report"]
```

The fake monitor must distinguish `$1 == postmarket` and `$1 == report`; place the fake updater at `scripts/data_collector/tushare/run_update_to_bin.sh` under the temporary root.

- [ ] **Step 2: Run RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_operational_wrappers.py -q
```

Expected: failures because `run_postclose_cron.sh` is missing.

- [ ] **Step 3: Implement the minimal wrapper**

Use `set -uo pipefail`, the standard config fallback, a directory lock, and an EXIT trap. Implement a `run_stage` helper that records nonzero status without terminating the pipeline. Run import, postmarket, and update in order; run report only when update returns zero. Append directly to the summary log without Bash process substitution (`/dev/fd` is not reliable in the cron/test sandbox), and return nonzero when any executed stage failed.

- [ ] **Step 4: Run GREEN**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_operational_wrappers.py -q
bash -n live_trading/run_postclose_cron.sh
```

- [ ] **Step 5: Commit**

```bash
git add live_trading/run_postclose_cron.sh tests/live_trading/test_operational_wrappers.py
git commit -m "feat(live): serialize postclose operations"
```

### Task 2: Publish/Data-update Exclusion

**Files:**
- Modify: `live_trading/run_publish_cron.sh`
- Modify: `live_trading/run_publish_catchup_cron.sh`
- Modify: `tests/live_trading/test_operational_wrappers.py`

**Interfaces:**
- Consumes: `.locks/<config_id>_postclose.lock` from Task 1.
- Produces: exit 75 before calendar, database, model, or SMB access when postclose is active.

- [ ] **Step 1: Write the failing lock test**

Copy both wrappers into a temporary `live_trading` directory, create the postclose lock, execute with `QMT_SIM_ACCOUNT_ID=test` and `LIVE_RUN_MODE=SIMULATE`, then assert return code 75 and stderr containing `postclose pipeline holds`.

- [ ] **Step 2: Run RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_operational_wrappers.py::test_publish_wrappers_refuse_postclose_overlap -q
```

- [ ] **Step 3: Add the preflight to both wrappers**

After creating `LOCK_ROOT` and before any trade-date/database work, add:

```bash
POSTCLOSE_LOCK_DIR="${LOCK_ROOT}/${CONFIG_ID}_postclose.lock"
if [[ -d "$POSTCLOSE_LOCK_DIR" ]]; then
    echo "postclose pipeline holds $POSTCLOSE_LOCK_DIR; refusing publish" >&2
    exit 75
fi
```

Mark catch-up manual-only in its header.

- [ ] **Step 4: Run GREEN and syntax checks**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_operational_wrappers.py -q
bash -n live_trading/run_publish_cron.sh live_trading/run_publish_catchup_cron.sh
```

- [ ] **Step 5: Commit**

```bash
git add live_trading/run_publish_cron.sh live_trading/run_publish_catchup_cron.sh tests/live_trading/test_operational_wrappers.py
git commit -m "fix(live): block publish during data update"
```

### Task 3: Actionable Alerts and Exact Schedule

**Files:**
- Modify: `live_trading/modules/pipeline_monitor.py`
- Modify: `live_trading/scripts/run_monitor.py`
- Modify: `live_trading/crontab.csi1000_postclose.example`
- Modify: `live_trading/configs/csi1000_b6m_b2s_postclose.yaml`
- Modify: `live_trading/README.md`
- Modify: `tests/live_trading/test_pipeline_monitor.py`
- Modify: `tests/live_trading/test_next_trade_date.py`
- Modify: `tests/live_trading/test_live_config.py`
- Modify: `tests/live_trading/test_operational_wrappers.py`

**Interfaces:**
- Consumes: `check_evening(next_trade_date, batch, inbox_files, config_id)`.
- Produces: `PUBLISH_MISSING` with publish log plus state-specific manual command.
- Produces: exactly 20:00 postclose, 21:30 publish, and 22:30 evening cron entries.

- [ ] **Step 1: Write failing tests**

Require no-batch alerts to name `run_publish_catchup_cron.sh <config_id>`, durable-batch missing-file alerts to name `run_publish_cron.sh <config_id> <trade_date>`, and every publish alert to name `live_trading/logs/<config_id>_publish_cron.log`. Require SMB errors to say restore the mount first.

Add exact schedule assertions:

```python
def test_crontab_matches_controlled_postclose_schedule():
    text = (REPO_ROOT / "live_trading" / "crontab.csi1000_postclose.example").read_text()
    assert "0 20 * * 1-5" in text and "run_postclose_cron.sh" in text
    assert "30 21 * * 1-5" in text and "run_publish_cron.sh" in text
    assert "30 22 * * 1-5" in text and "run_monitor_cron.sh evening" in text
    assert "run_publish_catchup_cron.sh" not in text
    assert "32 15" not in text and "35 15" not in text and "0 21" not in text
```

Update the real-config test to require `import_after == "20:00"`, `report_after == "after_data_update"`, `publish_after == "21:30"`, and `integrity_after == "22:30"`.

- [ ] **Step 2: Run RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_pipeline_monitor.py tests/live_trading/test_next_trade_date.py tests/live_trading/test_live_config.py tests/live_trading/test_operational_wrappers.py -q
```

- [ ] **Step 3: Implement alerts, template, metadata, and runbook**

Add `_publish_recovery_hint(config_id, trade_date, has_batch)` and thread `config["live"]["strategy_id"]` from `run_evening`. Replace the template with the three approved entries. Update schedule metadata and README to match executable order, report dependency, manual recovery, postclose lock, and deployment gates.

- [ ] **Step 4: Run GREEN and shell checks**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_pipeline_monitor.py tests/live_trading/test_next_trade_date.py tests/live_trading/test_live_config.py tests/live_trading/test_operational_wrappers.py -q
bash -n live_trading/run_postclose_cron.sh live_trading/run_import_cron.sh live_trading/run_monitor_cron.sh live_trading/run_publish_cron.sh live_trading/run_publish_catchup_cron.sh
```

- [ ] **Step 5: Commit**

```bash
git add live_trading/modules/pipeline_monitor.py live_trading/scripts/run_monitor.py live_trading/crontab.csi1000_postclose.example live_trading/configs/csi1000_b6m_b2s_postclose.yaml live_trading/README.md tests/live_trading/test_pipeline_monitor.py tests/live_trading/test_next_trade_date.py tests/live_trading/test_live_config.py tests/live_trading/test_operational_wrappers.py
git commit -m "chore(live): adopt controlled evening schedule"
```

### Task 4: Regression and Deployment Preflight

**Files:**
- Verify: `live_trading/`
- Verify: `tests/live_trading/`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: merge and later installation evidence.

- [ ] **Step 1: Run full live tests**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading -q
```

- [ ] **Step 2: Run static checks**

```bash
bash -n live_trading/run_postclose_cron.sh live_trading/run_import_cron.sh live_trading/run_monitor_cron.sh live_trading/run_publish_cron.sh live_trading/run_publish_catchup_cron.sh
git diff --check
git status --short
```

- [ ] **Step 3: Verify external gates**

Confirm writable SMB inbox, `LIVE_RUN_MODE=SIMULATE`, unset `LIVE_TRADING_CONFIRM`, and no `LIVE_OK_*`. Keep the database absent until reconciled opening cash is confirmed.
