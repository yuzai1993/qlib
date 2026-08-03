#!/usr/bin/env bash
# Publish the next trading-day batch. Defaults to shadow SIMULATE mode.
# 用法：
#   bash live_trading/run_publish_cron.sh [config_id] [trade_date]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="/opt/anaconda3/envs/qlib/bin/python"
CONFIG_ID="${1:-${LIVE_CONFIG_ID:-${QLIB_LIVE_CONFIG_ID:-csi1000_b6m_b2s_postclose}}}"
RUN_MODE="${LIVE_RUN_MODE:-SIMULATE}"

# cron 环境无交互 shell；密钥放 ~/.qlib_live_env（sh 语法，勿进 git）
# 注意不要 source ~/.zshrc——它是 zsh 专用（oh-my-zsh），bash 下会中途退出
# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

if [[ -z "${QMT_SIM_ACCOUNT_ID:-}" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: QMT_SIM_ACCOUNT_ID unset" >&2
    exit 1
fi
if [[ "$RUN_MODE" == "LIVE" && "${LIVE_TRADING_CONFIRM:-}" != "YES" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: LIVE mode requires LIVE_TRADING_CONFIRM=YES" >&2
    exit 1
fi

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
export JOBLIB_MULTIPROCESSING="${JOBLIB_MULTIPROCESSING:-0}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mplconfig-live}"
mkdir -p "$MPLCONFIGDIR"

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') publish config=${CONFIG_ID} mode=${RUN_MODE} trade_date=${TRADE_DATE} ====="
    cd "$PROJECT_ROOT"
    # caffeinate -i：发布约 15–20min，避免中途 Idle Sleep 被掐断
    caffeinate -i "$PYTHON" live_trading/scripts/run_publish_signals.py \
        --config "$CONFIG_ID" \
        --trade-date "$TRADE_DATE" \
        --mode "$RUN_MODE"
} >>"$LOG_FILE" 2>&1
