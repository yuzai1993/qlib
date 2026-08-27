#!/usr/bin/env bash
# Long-running read-only monitoring dashboard, intended for launchd.
# Usage: bash live_trading/run_web_service.sh [config_id]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

PYTHON="${QLIB_LIVE_PYTHON:-/opt/anaconda3/envs/qlib/bin/python}"
CONFIG_ID="${1:-${LIVE_CONFIG_ID:-${QLIB_LIVE_CONFIG_ID:-alla_v4_ladder_k1h5_postclose_real}}}"

cd "$PROJECT_ROOT"
exec "$PYTHON" live_trading/scripts/run_web.py --config "$CONFIG_ID"
