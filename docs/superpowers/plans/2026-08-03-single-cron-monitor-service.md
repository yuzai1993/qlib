# Single Cron and Monitor Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace three CSI1000 cron entries with one durable dispatcher and run the loopback-only monitoring dashboard as a self-restarting macOS LaunchAgent.

**Architecture:** A one-minute cron wrapper invokes a Python dispatcher that reads due times from the live YAML and writes one atomic daily receipt per attempted stage. A separate foreground Web wrapper is owned by a checked-in LaunchAgent using `RunAtLoad` and `KeepAlive`; deployment copies the plist into the user LaunchAgents directory and verifies HTTP health.

**Tech Stack:** Python 3.12, Bash, PyYAML, pytest, macOS cron, launchd, FastAPI/Uvicorn.

## Global Constraints

- The only scheduled workflow remains 20:00 postclose, 21:30 publish, and 22:30 integrity check on weekdays.
- Each stage is attempted automatically at most once per date, including non-zero exits; manual recovery remains explicit.
- No automatic `run_publish_catchup_cron.sh` invocation is permitted.
- The scheduler must run due stages in chronological order and use atomic receipts plus an atomic directory lock.
- The Web dashboard must bind only to `127.0.0.1:8081` and remain read-only.
- Keep Shadow safeguards unchanged: `LIVE_RUN_MODE=SIMULATE`, `LIVE_TRADING_CONFIRM` unset, and no `LIVE_OK` file.
- Actual crontab deployment must preserve unrelated entries and replace only the three known CSI1000 entries.

---

### Task 1: Durable single-entry scheduler

**Files:**
- Create: `live_trading/scripts/run_scheduler.py`
- Create: `live_trading/run_scheduler_cron.sh`
- Modify: `tests/live_trading/test_operational_wrappers.py`

**Interfaces:**
- Produces: `run_due_stages(config: dict, config_id: str, project_root: Path, now: datetime) -> int`.
- Produces receipts at `live_trading/.scheduler/<config>/<date>/<stage>.json` with `stage`, `scheduled_for`, `started_at`, `finished_at`, and `exit_code`.
- Invokes existing wrappers with exact argv: postclose, publish, and evening monitor.

- [ ] **Step 1: Write failing behavior tests** that create executable temporary wrappers, invoke `run_due_stages` with literal datetimes, and assert: before 20:00 no trace; at 20:00 only postclose; at 21:30 missing postclose then publish run in order; at 22:30 all three run in order; a second invocation creates no extra trace; a failed stage has a non-zero receipt and is not retried.
- [ ] **Step 2: Run the scheduler tests and verify RED** because `run_scheduler.py` does not exist.
- [ ] **Step 3: Implement the minimal dispatcher** with strict `HH:MM` parsing, chronological stage definitions, `subprocess.run`, atomic JSON replacement, and `mkdir` lock cleanup in `finally`; implement the Bash wrapper that sources `~/.qlib_live_env`, logs only actual work/errors, and calls the Python entry point.
- [ ] **Step 4: Run the focused tests and Bash syntax check and verify GREEN.**
- [ ] **Step 5: Commit** with `feat(live): add durable single cron scheduler`.

### Task 2: One-line cron contract and operator documentation

**Files:**
- Modify: `live_trading/crontab.csi1000_postclose.example`
- Modify: `live_trading/README.md`
- Modify: `tests/live_trading/test_operational_wrappers.py`

**Interfaces:**
- Crontab calls only `run_scheduler_cron.sh csi1000_b6m_b2s_postclose` every weekday minute.
- Scheduler due times continue to come from the live YAML schedule mapping.

- [ ] **Step 1: Write a failing integration assertion** that parses non-comment crontab lines and requires exactly one cron command containing `run_scheduler_cron.sh`, while rejecting direct scheduled references to postclose, publish, evening, or catch-up wrappers.
- [ ] **Step 2: Run the cron-contract test and verify RED** against the current three-line template.
- [ ] **Step 3: Replace the template and update README** with dispatcher receipts, same-day wake recovery, attempt-once semantics, inspection commands, and manual recovery guidance.
- [ ] **Step 4: Run the wrapper/cron tests and verify GREEN.**
- [ ] **Step 5: Commit** with `chore(live): consolidate cron schedule`.

### Task 3: Loopback monitoring LaunchAgent

**Files:**
- Create: `live_trading/run_web_service.sh`
- Create: `live_trading/launchd/com.yuxianqi.qlib-live-monitor.plist`
- Modify: `live_trading/configs/csi1000_b6m_b2s_postclose.yaml`
- Modify: `live_trading/README.md`
- Modify: `tests/live_trading/test_operational_wrappers.py`
- Modify: `tests/live_trading/test_live_config.py`

**Interfaces:**
- Foreground wrapper: `run_web_service.sh [config_id]` sources the cron env and `exec`s `run_web.py`.
- LaunchAgent label: `com.yuxianqi.qlib-live-monitor`.
- Health endpoint: existing `GET /api/overview` on `http://127.0.0.1:8081`.

- [ ] **Step 1: Write failing tests** that execute the service wrapper against a temporary Python shim to prove config forwarding and environment loading; parse the plist with `plistlib` to assert label, `RunAtLoad`, `KeepAlive`, wrapper argv, working directory, and dedicated logs; assert the live config host is `127.0.0.1`.
- [ ] **Step 2: Run the service/config tests and verify RED.**
- [ ] **Step 3: Implement the wrapper, plist, loopback config, and README service commands.**
- [ ] **Step 4: Run focused tests, `bash -n`, and `plutil -lint` and verify GREEN.**
- [ ] **Step 5: Commit** with `feat(live): run monitor as launch agent`.

### Task 4: Verification, integration, and deployment

**Files:**
- Install outside Git: `~/Library/LaunchAgents/com.yuxianqi.qlib-live-monitor.plist`
- Update outside Git: user crontab.

**Interfaces:**
- Consumes the verified one-line crontab template and LaunchAgent plist.
- Produces one installed cron line and one running user service.

- [ ] **Step 1: Run `pytest tests/live_trading -q`, parity validation, Python compilation, all wrapper syntax checks, plist lint, and `git diff --check`.**
- [ ] **Step 2: Merge the verified feature branch into `main` and rerun the full verification from `main`.**
- [ ] **Step 3: Build a replacement crontab by preserving unrelated current lines and removing only the three exact CSI1000 commands; install and read back exactly one scheduler line.**
- [ ] **Step 4: Copy the plist into `~/Library/LaunchAgents`, boot out any prior instance, bootstrap it into `gui/<uid>`, and enable/kickstart it.**
- [ ] **Step 5: Verify `launchctl print`, a single listener on `127.0.0.1:8081`, HTTP 200 from `/api/overview`, log paths, scheduler state directory permissions, and unchanged Shadow gates.**
