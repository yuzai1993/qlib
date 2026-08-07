#!/usr/bin/env bash
# Publish the next trading-day batch. Defaults to shadow SIMULATE mode.
# 用法：
#   bash live_trading/run_publish_cron.sh [config_id] [trade_date]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="/opt/anaconda3/envs/qlib/bin/python"
CONFIG_ID="${1:-${LIVE_CONFIG_ID:-${QLIB_LIVE_CONFIG_ID:-csi1000_b6m_b2s_postclose_real}}}"
STATE_HELPER="$PROJECT_ROOT/live_trading/scripts/set_execution_state.py"

if [[ ! "$CONFIG_ID" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "invalid config identifier: ${CONFIG_ID@Q}" >&2
    exit 1
fi
if [[ -f "$STATE_HELPER" ]] && ! "$PYTHON" "$STATE_HELPER" --validate-config-id "$CONFIG_ID" >/dev/null; then
    echo "invalid config identifier: ${CONFIG_ID@Q}" >&2
    exit 1
fi

# cron 环境无交互 shell；密钥放 ~/.qlib_live_env（sh 语法，勿进 git）
# 注意不要 source ~/.zshrc——它是 zsh 专用（oh-my-zsh），bash 下会中途退出
# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"
RUN_MODE="${LIVE_RUN_MODE:-SIMULATE}"

LOCK_ROOT="${SCRIPT_DIR}/.locks"
mkdir -p "$LOCK_ROOT"
POSTCLOSE_LOCK_DIR="${LOCK_ROOT}/${CONFIG_ID}_postclose.lock"
if [[ -d "$POSTCLOSE_LOCK_DIR" ]]; then
    echo "postclose pipeline holds $POSTCLOSE_LOCK_DIR; refusing publish" >&2
    exit 75
fi

# State is queried before LIVE confirmation so an intentional PAUSED strategy
# can make its evidence-only preview without an authorization token.  A failed
# query never permits a confirmed LIVE publish.
EXECUTION_STATE="ACTIVE"
STATE_QUERY_FAILED=0
if [[ -f "$STATE_HELPER" ]]; then
    if ! EXECUTION_STATE="$("$PYTHON" "$STATE_HELPER" --config "$CONFIG_ID" --get)"; then
        STATE_QUERY_FAILED=1
    fi
else
    # A configured deployment without the helper cannot prove it is ACTIVE.
    # The config-less fixture fallback keeps wrapper preflight tests isolated
    # while every real configured publish remains fail-closed.
    if [[ -f "$PROJECT_ROOT/live_trading/configs/${CONFIG_ID}.yaml" ]]; then
        STATE_QUERY_FAILED=1
    fi
fi
if [[ "$EXECUTION_STATE" != "ACTIVE" && "$EXECUTION_STATE" != "PAUSED" ]]; then
    STATE_QUERY_FAILED=1
fi

if [[ "$EXECUTION_STATE" != "PAUSED" && "$RUN_MODE" == "LIVE" && "${LIVE_TRADING_CONFIRM:-}" != "YES" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: LIVE mode requires LIVE_TRADING_CONFIRM=YES" >&2
    exit 1
fi
if [[ "$STATE_QUERY_FAILED" -ne 0 ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: execution state query failed" >&2
    exit 1
fi
if [[ "$EXECUTION_STATE" == "PAUSED" ]]; then
    if ! PREVIEW_STRATEGY_ID="$("$PYTHON" "$STATE_HELPER" --config "$CONFIG_ID" --get-strategy-id)" || [[ -z "$PREVIEW_STRATEGY_ID" ]]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: strategy id query failed" >&2
        exit 1
    fi
    if [[ ! "$PREVIEW_STRATEGY_ID" =~ ^[A-Za-z0-9_-]+$ ]]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: strategy id query failed" >&2
        exit 1
    fi
    if ! "$PYTHON" "$STATE_HELPER" --validate-strategy-id "$PREVIEW_STRATEGY_ID" >/dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: strategy id query failed" >&2
        exit 1
    fi
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

# Close the race with postclose starting after the first preflight but before
# this job acquired its own lock. Conservative double-failure is preferable
# to reading a provider while it is being rewritten.
if [[ -d "$POSTCLOSE_LOCK_DIR" ]]; then
    echo "postclose pipeline holds $POSTCLOSE_LOCK_DIR; refusing publish" >&2
    exit 75
fi
export JOBLIB_MULTIPROCESSING="${JOBLIB_MULTIPROCESSING:-0}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mplconfig-live}"
mkdir -p "$MPLCONFIGDIR"

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') publish config=${CONFIG_ID} mode=${RUN_MODE} trade_date=${TRADE_DATE} ====="
    cd "$PROJECT_ROOT"
    if [[ "$EXECUTION_STATE" == "PAUSED" ]]; then
        AUDIT_PREVIEW="$SCRIPT_DIR/logs/${PREVIEW_STRATEGY_ID}/previews/signal_${TRADE_DATE}.json"
        echo "publish paused preview-only state=PAUSED preview=${AUDIT_PREVIEW}"
        caffeinate -i "$PYTHON" live_trading/scripts/run_publish_signals.py \
            --config "$CONFIG_ID" \
            --trade-date "$TRADE_DATE" \
            --mode "$RUN_MODE" \
            --dry-run \
            --audit-preview "$AUDIT_PREVIEW"
        exit 0
    fi
    # caffeinate -i：发布约 15–20min，避免中途 Idle Sleep 被掐断
    caffeinate -i "$PYTHON" live_trading/scripts/run_publish_signals.py \
        --config "$CONFIG_ID" \
        --trade-date "$TRADE_DATE" \
        --mode "$RUN_MODE"
} >>"$LOG_FILE" 2>&1
