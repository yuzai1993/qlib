#!/usr/bin/env bash
# Serialized post-close pipeline: receipts -> checks -> market data -> report.
# Usage: bash live_trading/run_postclose_cron.sh [config_id]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

PYTHON="${QLIB_LIVE_PYTHON:-/opt/anaconda3/envs/qlib/bin/python}"
CONFIG_ID="${1:-${LIVE_CONFIG_ID:-${QLIB_LIVE_CONFIG_ID:-csi1000_b6m_b2s_postclose_real}}}"

mkdir -p "${SCRIPT_DIR}/logs"
LOG_FILE="${SCRIPT_DIR}/logs/${CONFIG_ID}_postclose_cron.log"
LOCK_ROOT="${SCRIPT_DIR}/.locks"
LOCK_DIR="${LOCK_ROOT}/${CONFIG_ID}_postclose.lock"
mkdir -p "$LOCK_ROOT"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "another postclose pipeline holds $LOCK_DIR" >&2
    exit 75
fi

OVERALL_STATUS=0
finish_job() {
    job_status=$?
    trap - EXIT
    rmdir "$LOCK_DIR" 2>/dev/null || :
    if [[ "$job_status" -eq 0 && "$OVERALL_STATUS" -ne 0 ]]; then
        job_status="$OVERALL_STATUS"
    fi
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') postclose exit status=${job_status} ====="
    exit "$job_status"
}
trap finish_job EXIT

PUBLISH_LOCK_DIR="${LOCK_ROOT}/${CONFIG_ID}_publish.lock"
if [[ -d "$PUBLISH_LOCK_DIR" ]]; then
    echo "publish job holds $PUBLISH_LOCK_DIR; refusing postclose pipeline" >&2
    exit 75
fi

exec >>"$LOG_FILE" 2>&1

run_stage() {
    local name="$1"
    shift
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') stage=${name} ====="
    "$@"
    local status=$?
    if [[ "$status" -ne 0 ]]; then
        OVERALL_STATUS=1
        echo "stage=${name} exit status=${status}"
    fi
    return "$status"
}

run_stage import \
    "${SCRIPT_DIR}/run_import_cron.sh" "$CONFIG_ID"
import_status=$?

run_stage postmarket \
    "${SCRIPT_DIR}/run_monitor_cron.sh" postmarket "$CONFIG_ID"
postmarket_status=$?

run_stage update \
    "${PROJECT_ROOT}/scripts/data_collector/tushare/run_update_to_bin.sh"
update_status=$?

run_stage stock_names \
    "$PYTHON" "${SCRIPT_DIR}/scripts/refresh_stock_names.py" \
    --config "$CONFIG_ID"
stock_names_status=$?

if [[ "$update_status" -eq 0 ]]; then
    run_stage report \
        "${SCRIPT_DIR}/run_monitor_cron.sh" report "$CONFIG_ID"
    report_status=$?
else
    echo "report skipped: market data update failed"
fi

# Keep the explicit values in the summary for incident diagnosis.
echo "postclose summary: import=${import_status} postmarket=${postmarket_status} update=${update_status} stock_names=${stock_names_status} report=${report_status:-SKIPPED}"
exit "$OVERALL_STATUS"
