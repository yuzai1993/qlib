#!/usr/bin/env bash
# 导入/发布前复用可写的 SMB 挂载，断开时由 macOS NetFS 自动重连。
# 用法：bash live_trading/scripts/ensure_bridge_mount.sh [bridge_root]
# 未挂上时用 QLIB_BRIDGE_SMB_URL（默认 //qmtshare@192.168.0.110/qmt_bridge）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${QLIB_LIVE_PYTHON:-/opt/anaconda3/envs/qlib/bin/python}"
exec "$PYTHON" "${SCRIPT_DIR}/ensure_bridge_mount.py" \
    "${1:-${QLIB_BRIDGE_ROOT:-/Volumes/qmt_bridge}}"
