#!/usr/bin/env bash
# launchd can deliver a missed calendar event after wake, including next morning.
# Only enter the current-date pipeline in the same weekday post-close window.
set -euo pipefail

read -r QLIB_LIVE_BUSINESS_DATE WEEKDAY HHMM <<< "$(date '+%Y-%m-%d %u %H%M')"
if (( WEEKDAY > 5 || 10#${HHMM} < 2000 )); then
    echo "scheduler skipped: outside weekday 20:00-23:59 window; recover missed business dates explicitly"
    exit 0
fi
export QLIB_LIVE_BUSINESS_DATE

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec /bin/bash "${SCRIPT_DIR}/run_scheduler_cron.sh" "$@"
