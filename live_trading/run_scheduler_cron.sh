#!/usr/bin/env bash
# One cron entry calls this lightweight due-stage dispatcher every minute.
# Usage: bash live_trading/run_scheduler_cron.sh [config_id]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="${QLIB_LIVE_PYTHON:-/opt/anaconda3/envs/qlib/bin/python}"
CONFIG_ID="${1:-${LIVE_CONFIG_ID:-${QLIB_LIVE_CONFIG_ID:-alla_v4_ladder_k1h5_postclose_real}}}"

# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

cd "$PROJECT_ROOT"
exec "$PYTHON" live_trading/scripts/run_scheduler.py --config "$CONFIG_ID"
