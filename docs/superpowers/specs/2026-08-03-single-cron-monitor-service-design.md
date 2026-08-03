# Single Cron and Monitor Service Design

## Goal

Reduce the CSI1000 deployment to one crontab entry while preserving the
approved 20:00, 21:30, and 22:30 workflow, and run the read-only monitoring
dashboard as a self-restarting macOS user service.

## Single Cron Dispatcher

The user crontab contains one weekday entry that invokes a lightweight Bash
wrapper every minute. The wrapper loads `~/.qlib_live_env` and calls a Python
dispatcher. The dispatcher reads all three due times from
`live_trading/configs/csi1000_b6m_b2s_postclose.yaml`; the crontab does not
duplicate the times.

The stages remain separate operational units and run in this order:

1. `postclose` at or after `schedule.import_after` (`20:00`): import receipts,
   run postmarket checks, update Qlib data, and create the report only after a
   successful update.
2. `publish` at or after `schedule.publish_after` (`21:30`): publish the next
   trading-day batch through the existing fail-closed wrapper.
3. `evening` at or after `schedule.integrity_after` (`22:30`): verify the
   publication is complete.

The dispatcher is not a long-lived sleeper. Each cron invocation evaluates the
current time, runs every due-but-unattempted stage in chronological order, and
exits. This survives sleep or a reboot within the same day and avoids holding a
process for hours.

## Idempotency and Failure Semantics

Daily stage receipts live under
`live_trading/.scheduler/<config-id>/<YYYY-MM-DD>/<stage>.json`. A receipt is
written atomically after the child wrapper exits and records the exit code and
timestamps. A stage is attempted at most once automatically per day even when
it returns non-zero; existing notifications and manual recovery commands remain
authoritative, so the scheduler never creates a blind retry loop.

If the scheduler process is killed before the receipt is committed, the next
minute may retry that stage. This is safe because the child wrappers already
use locks and durable/idempotent batch publication. A scheduler-wide atomic
directory lock prevents overlapping dispatcher invocations. Failures do not
prevent later scheduled stages from being considered once their time is due,
matching the former independent cron entries.

The one crontab entry is:

```cron
* * * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/run_scheduler_cron.sh csi1000_b6m_b2s_postclose
```

No automatic publish catch-up command is scheduled.

## Monitoring Service

The dashboard runs under a user LaunchAgent with label
`com.yuxianqi.qlib-live-monitor`. The checked-in service wrapper loads the same
environment file and `exec`s the existing `run_web.py` entry point. The
LaunchAgent uses `RunAtLoad`, `KeepAlive`, a ten-second throttle, the repository
as its working directory, and dedicated stdout/stderr logs in
`live_trading/logs/`.

The active configuration changes the listener from `0.0.0.0` to
`127.0.0.1:8081`. The dashboard contains account and strategy state but has no
authentication, so LAN exposure is not allowed by default. Local access is
`http://127.0.0.1:8081/`; any future remote access requires a separately
designed authenticated tunnel or reverse proxy.

The source plist is stored in `live_trading/launchd/`. Deployment copies it to
`~/Library/LaunchAgents/`, bootstraps it in the current GUI domain, then verifies
both `launchctl print` and `/api/overview`.

## Testing and Deployment Gates

- Unit/integration tests execute the real dispatcher against temporary wrapper
  scripts and assert timing, order, daily idempotency, failure receipts, and no
  automatic retry.
- Repository tests assert that the crontab template contains exactly one job,
  the LaunchAgent targets the controlled wrapper, and the active listener is
  loopback-only.
- Bash syntax, plist syntax, the full live-trading test suite, parity, and a
  clean Git diff must pass before deployment.
- Deployment replaces only the three known CSI1000 cron lines with the one
  dispatcher line, preserves unrelated crontab content, and reads it back.
- Service deployment must prove a listening process and HTTP 200 response.
- Shadow gates remain unchanged: `LIVE_RUN_MODE=SIMULATE`, no
  `LIVE_TRADING_CONFIRM=YES`, and no `LIVE_OK` marker.
