#!/bin/bash
# paper_trading/run_daily.sh
# Called by crontab: 0 21 * * 1-5 /home/yuzai/qlib/paper_trading/run_daily.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CONDA_ENV="qlib312"
CONDA_ROOT="${CONDA_ROOT:-/home/yuzai/miniforge3}"
TODAY=$(date +%Y-%m-%d)
LOG_DIR="${SCRIPT_DIR}/logs"
LOG_FILE="${LOG_DIR}/${TODAY}.log"
DATA_LOG_DIR="${PROJECT_ROOT}/logs/data"
DATA_LOG_FILE="${DATA_LOG_DIR}/${TODAY}.log"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Activate conda environment
if [ -f "${CONDA_ROOT}/etc/profile.d/conda.sh" ]; then
    # Cron has a minimal PATH; source conda.sh explicitly.
    source "${CONDA_ROOT}/etc/profile.d/conda.sh"
elif [ -x "${CONDA_ROOT}/bin/conda" ]; then
    eval "$("${CONDA_ROOT}/bin/conda" shell.bash hook)"
else
    export PATH="${CONDA_ROOT}/bin:${PATH}"
    if ! command -v conda >/dev/null 2>&1; then
        log "ERROR: conda not found (CONDA_ROOT=${CONDA_ROOT})"
        exit 1
    fi
    eval "$(conda shell.bash hook)"
fi

if ! conda activate "$CONDA_ENV"; then
    log "ERROR: failed to activate conda env ${CONDA_ENV}"
    exit 1
fi

log "Starting paper trading daily routine"

# Check if data update log exists and was successful
if [ -f "$DATA_LOG_FILE" ]; then
    if grep -q "ERROR\|FAILED\|Traceback" "$DATA_LOG_FILE" 2>/dev/null; then
        log "WARNING: Data update log contains errors, checking if critical..."
        if grep -q "DumpDataAll.*Done\|dump_bin.*completed" "$DATA_LOG_FILE" 2>/dev/null; then
            log "Data dump appears to have completed despite warnings, proceeding"
        else
            log "ERROR: Data update appears to have failed, aborting"
            if [ -n "${ALERT_EMAIL:-}" ]; then
                echo "Paper trading aborted: data update failed on $TODAY" | \
                    mail -s "[模拟盘] 数据更新失败 $TODAY" "$ALERT_EMAIL" 2>/dev/null || true
            fi
            exit 1
        fi
    fi
    log "Data update log found and appears successful"
else
    log "WARNING: Data update log not found at $DATA_LOG_FILE"
    log "Proceeding anyway (data may have been updated earlier)"
fi

# Run paper trading
cd "$PROJECT_ROOT"
log "Running: python paper_trading/paper_trading.py daily"

if python paper_trading/paper_trading.py daily >> "$LOG_FILE" 2>&1; then
    log "Daily routine completed successfully"
else
    EXIT_CODE=$?
    log "ERROR: Daily routine failed with exit code $EXIT_CODE"
    if [ -n "${ALERT_EMAIL:-}" ]; then
        tail -50 "$LOG_FILE" | \
            mail -s "[模拟盘] 执行失败 $TODAY (exit=$EXIT_CODE)" "$ALERT_EMAIL" 2>/dev/null || true
    fi
    exit $EXIT_CODE
fi
