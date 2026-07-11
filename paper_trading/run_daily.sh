#!/bin/bash
# paper_trading/run_daily.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CONDA_ENV="qlib"
CONDA_ROOT="${CONDA_ROOT:-}"

# 支持传入指定日期（补跑用）：bash run_daily.sh 2026-04-03
# 不传则默认今天
if [[ -n "${1:-}" ]]; then
    TODAY="$1"
else
    TODAY=$(date +%Y-%m-%d)
fi

LOG_DIR="${SCRIPT_DIR}/logs"
LOG_FILE="${LOG_DIR}/${TODAY}.log"
DATA_LOG_DIR="${PROJECT_ROOT}/logs/data"
DATA_LOG_FILE="${DATA_LOG_DIR}/${TODAY}.log"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Detect conda root on local machine when CONDA_ROOT is not provided.
if [ -z "$CONDA_ROOT" ]; then
    if command -v conda >/dev/null 2>&1; then
        CONDA_ROOT="$(conda info --base 2>/dev/null || true)"
    fi
fi

if [ -z "$CONDA_ROOT" ]; then
    for candidate in "$HOME/miniforge3" "$HOME/mambaforge" "$HOME/anaconda3" "/opt/anaconda3" "/opt/miniconda3"; do
        if [ -f "${candidate}/etc/profile.d/conda.sh" ] || [ -x "${candidate}/bin/conda" ]; then
            CONDA_ROOT="$candidate"
            break
        fi
    done
fi

# Activate conda environment
if [ -f "${CONDA_ROOT}/etc/profile.d/conda.sh" ]; then
    # Cron has a minimal PATH; source conda.sh explicitly.
    source "${CONDA_ROOT}/etc/profile.d/conda.sh"
elif command -v conda >/dev/null 2>&1; then
    eval "$(conda shell.bash hook)"
elif [ -x "${CONDA_ROOT}/bin/conda" ]; then
    export PATH="${CONDA_ROOT}/bin:${PATH}"
    eval "$("${CONDA_ROOT}/bin/conda" shell.bash hook)"
else
    log "ERROR: conda not found (CONDA_ROOT=${CONDA_ROOT:-unset})"
    exit 1
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
        if grep -q "DumpDataAll.*Done\|dump_bin.*completed\|end of features dump" "$DATA_LOG_FILE" 2>/dev/null; then
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
    # csi300 由 csindex_v2 维护；此处仅确认个股 dump 完成
    if ! grep -qE "end of features dump|DumpDataAll" "$DATA_LOG_FILE" 2>/dev/null; then
        log "WARNING: data dump may not have completed"
        log "WARNING: Predictions may fall back to previous day's features"
    fi
    log "Data update log found and appears successful"
else
    log "WARNING: Data update log not found at $DATA_LOG_FILE"
    log "Proceeding anyway (data may have been updated earlier)"
fi

# Run paper trading
cd "$PROJECT_ROOT"

if [[ "$TODAY" == "$(date +%Y-%m-%d)" ]]; then
    PT_CMD="paper_trading/paper_trading.py daily"
else
    PT_CMD="paper_trading/paper_trading.py run --date $TODAY"
fi

log "Running: python $PT_CMD"

if python $PT_CMD >> "$LOG_FILE" 2>&1; then
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
