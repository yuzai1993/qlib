#!/usr/bin/env bash
# 漏发兜底：若下一交易日尚无 LIVE 批次，则补跑发布（休眠漏 cron 时用）
# 建议 crontab：5 22 * * 1-5（evening 检查前/后均可；幂等）
# 用法：bash live_trading/run_publish_catchup_cron.sh [config_id]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="/opt/anaconda3/envs/qlib/bin/python"
CONFIG_ID="${1:-${LIVE_CONFIG_ID:-${QLIB_LIVE_CONFIG_ID:-csi1000_b6m_b2s_postclose}}}"
RUN_MODE="${LIVE_RUN_MODE:-SIMULATE}"

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

mkdir -p "${SCRIPT_DIR}/logs"
LOG_FILE="${SCRIPT_DIR}/logs/${CONFIG_ID}_publish_cron.log"
LOCK_ROOT="${SCRIPT_DIR}/.locks"
LOCK_DIR="${LOCK_ROOT}/${CONFIG_ID}_publish.lock"
mkdir -p "$LOCK_ROOT"
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

TRADE_DATE="$("$PYTHON" "$PROJECT_ROOT/live_trading/scripts/next_trade_date.py" \
    --after "$(date +%Y-%m-%d)")"

STATUS=0
EXISTING="$("$PYTHON" "$PROJECT_ROOT/live_trading/scripts/batch_status.py" \
    --config "$CONFIG_ID" --trade-date "$TRADE_DATE")" || STATUS=$?
if [[ "$STATUS" -eq 2 ]]; then
    echo "batch status lookup failed" >&2
    exit 2
fi

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') publish catchup config=${CONFIG_ID} mode=${RUN_MODE} trade_date=${TRADE_DATE} ====="
    if [[ -n "$EXISTING" ]]; then
        echo "skip: batch already present (${EXISTING})"
        exit 0
    fi
    echo "missing batch for ${TRADE_DATE}; running publish"
    cd "$PROJECT_ROOT"
    # 防止发布中途休眠；-i 抑制 idle sleep
    caffeinate -i "$PYTHON" live_trading/scripts/run_publish_signals.py \
        --config "$CONFIG_ID" \
        --trade-date "$TRADE_DATE" \
        --mode "$RUN_MODE"
} >>"$LOG_FILE" 2>&1
