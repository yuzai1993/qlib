#!/usr/bin/env bash
# Publish the next trading-day batch. QMT controls account/runtime execution.
# Usage: bash live_trading/run_publish_cron.sh [config_id] [trade_date]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="/opt/anaconda3/envs/qlib/bin/python"
CONFIG_ID="${1:-${LIVE_CONFIG_ID:-${QLIB_LIVE_CONFIG_ID:-alla_v4_ladder_k1h5_postclose_real}}}"

if [[ ! "$CONFIG_ID" =~ ^[A-Za-z0-9_-]+$ ]]; then
    printf 'invalid config identifier: %q\n' "$CONFIG_ID" >&2
    exit 1
fi

# Runtime credentials remain shared with the other cron stages.
# QMT start/stop and local account settings decide execution.
# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"
if [[ -f "${SCRIPT_DIR}/scripts/ensure_bridge_mount.sh" ]]; then
    bash "${SCRIPT_DIR}/scripts/ensure_bridge_mount.sh"
fi
unset LIVE_TRADING_CONFIRM
unset LIVE_RUN_MODE

LOCK_ROOT="${SCRIPT_DIR}/.locks"
mkdir -p "$LOCK_ROOT"
POSTCLOSE_LOCK_DIR="${LOCK_ROOT}/${CONFIG_ID}_postclose.lock"
if [[ -d "$POSTCLOSE_LOCK_DIR" ]]; then
    echo "postclose pipeline holds $POSTCLOSE_LOCK_DIR; refusing publish" >&2
    exit 75
fi

if [[ -n "${2:-}" ]]; then
    TRADE_DATE="$2"
else
    TRADE_DATE="$("$PYTHON" "$PROJECT_ROOT/live_trading/scripts/next_trade_date.py" \
        --after "$(date +%Y-%m-%d)")"
fi

mkdir -p "${SCRIPT_DIR}/logs"
LOG_FILE="${SCRIPT_DIR}/logs/${CONFIG_ID}_publish_cron.log"
LOCK_DIR="${LOCK_ROOT}/${CONFIG_ID}_publish.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "another publish/catch-up job holds $LOCK_DIR" >&2
    exit 75
fi
finish_job() {
    job_status=$?
    trap - EXIT
    rmdir "$LOCK_DIR" 2>/dev/null || :
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') exit status=${job_status} =====" \
        >>"$LOG_FILE" 2>&1 || :
    exit "$job_status"
}
trap finish_job EXIT

if [[ -d "$POSTCLOSE_LOCK_DIR" ]]; then
    echo "postclose pipeline holds $POSTCLOSE_LOCK_DIR; refusing publish" >&2
    exit 75
fi
export JOBLIB_MULTIPROCESSING="${JOBLIB_MULTIPROCESSING:-0}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mplconfig-live}"
mkdir -p "$MPLCONFIGDIR"

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') publish config=${CONFIG_ID} trade_date=${TRADE_DATE} ====="
    cd "$PROJECT_ROOT"
    caffeinate -i "$PYTHON" live_trading/scripts/run_publish_signals.py \
        --config "$CONFIG_ID" \
        --trade-date "$TRADE_DATE"
} >>"$LOG_FILE" 2>&1
