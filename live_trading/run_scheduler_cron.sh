#!/usr/bin/env bash
# Shared entry point for the user LaunchAgent and explicit manual recovery.
# Usage: bash live_trading/run_scheduler_cron.sh [config_id]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="${QLIB_LIVE_PYTHON:-/opt/anaconda3/envs/qlib/bin/python}"
CONFIG_ID="${1:-${LIVE_CONFIG_ID:-${QLIB_LIVE_CONFIG_ID:-alla_v4_ladder_k1h5_postclose_real}}}"

# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"
export QLIB_LIVE_BUSINESS_DATE="${QLIB_LIVE_BUSINESS_DATE:-$(date +%Y-%m-%d)}"

# 导入前复核共享盘；未连接时通过 macOS NetFS 自动挂载。
# 测试夹具只拷 wrapper 时跳过。
if [[ -f "${SCRIPT_DIR}/scripts/ensure_bridge_mount.sh" ]]; then
    bash "${SCRIPT_DIR}/scripts/ensure_bridge_mount.sh"
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" live_trading/scripts/run_scheduler.py --config "$CONFIG_ID"
