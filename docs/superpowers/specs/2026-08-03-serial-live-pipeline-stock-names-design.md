# Serial Live Pipeline and Stock-Name Cache Design

## Goal

Run the CSI1000 post-close workflow from one weekday trigger without separate
publish or integrity-check times, and ensure the read-only monitoring dashboard
shows Chinese stock names for prediction rows.

## Current-State Findings

- Cron currently wakes the dispatcher every minute. The dispatcher independently
  gates postclose, publish, and evening at 20:00, 21:30, and 22:30.
- The prediction UI already renders `name`, and the REST queries already left
  join `predictions.instrument` to `stock_names.instrument`.
- The production `stock_names` table is empty, so the API correctly returns
  `name: null`. A Tushare-backed refresh command exists but is not part of the
  scheduled workflow.

## Scheduling Design

The sole cron entry runs at 20:00 on weekdays and invokes the existing scheduler
wrapper once. The scheduler takes its existing atomic directory lock and executes
the following stages synchronously in fixed order:

1. `run_postclose_cron.sh <config>`
2. `run_publish_cron.sh <config>`
3. `run_monitor_cron.sh evening <config>`

There are no per-stage clock checks or sleeps. Each attempted stage still gets an
atomic daily JSON receipt. A receipt prevents duplicate automatic execution when
an operator reruns the scheduler on the same date. A failed stage is recorded and
contributes to the final non-zero status, but does not suppress later stages; this
preserves monitoring and allows non-critical postclose warnings to coexist with
signal publication. The unused 21:30 and 22:30 schedule keys are removed from the
active live configuration and documentation.

## Stock-Name Design

`run_postclose_cron.sh` refreshes the stock-name cache from Tushare after the
market-data update and before the daily report. This keeps the database lookup
local for all Web requests and refreshes renamed securities without coupling the
dashboard to network availability.

Name refresh is an observable postclose sub-stage. Failure sets the postclose
result non-zero and is logged, but the report still runs when the market-data
update succeeded, and the outer scheduler still proceeds to publish and evening
checks. Existing cached names remain available because the refresh command only
replaces rows after Tushare returns them.

Deployment includes one immediate refresh so the already stored 2026-08-03
prediction rows receive names without waiting for the next scheduled run. No
prediction rows or scores are rewritten.

## Verification

- Scheduler tests prove one invocation runs all three wrappers immediately and in
  order, records failures, continues after failure, and does not retry receipts.
- Cron contract test requires exactly one `0 20 * * 1-5` scheduler entry.
- Postclose wrapper tests prove name refresh occurs after data update, before the
  report, and does not prevent the report when refresh fails.
- Recorder/API tests prove prediction search, summary, and instrument endpoints
  return names after the cache is populated.
- Deployment verification checks crontab, three receipts, stock-name row count,
  a named prediction API response, LaunchAgent state, and loopback HTTP 200.

## Safety

The change does not alter account selection, order sizing, model or strategy,
bridge protocol, `LIVE_RUN_MODE`, confirmation gates, or `LIVE_OK` requirements.
The dashboard remains read-only and bound to `127.0.0.1:8081`.
