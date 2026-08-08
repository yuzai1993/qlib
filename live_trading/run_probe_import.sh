#!/usr/bin/env bash
# Import the isolated prType=49 outbound into the shared main-account ledger.

set -euo pipefail

if [[ "$#" -ne 0 ]]; then
    echo "run_probe_import.sh does not accept a config override" >&2
    exit 64
fi

# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

readonly SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
readonly PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
readonly PYTHON="${QLIB_LIVE_PYTHON:-/opt/anaconda3/envs/qlib/bin/python}"
readonly CONFIG_ID="csi1000_pr49_one_lot_probe"

LOG_DIR="${SCRIPT_DIR}/logs"
LOCK_ROOT="${SCRIPT_DIR}/.locks"
LOCK_DIR="${LOCK_ROOT}/${CONFIG_ID}_import.lock"
mkdir -p "$LOG_DIR" "$LOCK_ROOT"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "another probe import job holds $LOCK_DIR" >&2
    exit 75
fi
finish_job() {
    job_status=$?
    trap - EXIT
    rmdir "$LOCK_DIR" 2>/dev/null || :
    exit "$job_status"
}
trap finish_job EXIT

cd "$PROJECT_ROOT"
"$PYTHON" "${SCRIPT_DIR}/scripts/run_import_fills.py" \
    --config "$CONFIG_ID" 2>&1 | tee -a "${LOG_DIR}/${CONFIG_ID}_import.log"
