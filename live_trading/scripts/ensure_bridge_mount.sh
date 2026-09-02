#!/usr/bin/env bash
# cron 没有 Finder 会话，看不见已挂的 SMB。导入/发布前先保证挂载点可写。
# 用法：bash live_trading/scripts/ensure_bridge_mount.sh [bridge_root]
# 未挂上时用 QLIB_BRIDGE_SMB_URL（默认 //qmtshare@192.168.0.110/qmt_bridge）。

set -euo pipefail

BRIDGE_ROOT="${1:-${QLIB_BRIDGE_ROOT:-/Volumes/qmt_bridge}}"
SMB_URL="${QLIB_BRIDGE_SMB_URL:-//qmtshare@192.168.0.110/qmt_bridge}"
SMB_URL="${SMB_URL#smb:}"

if [[ -d "${BRIDGE_ROOT}/inbox" && -w "${BRIDGE_ROOT}/inbox" ]]; then
    echo "bridge ready: ${BRIDGE_ROOT}"
    exit 0
fi

echo "bridge not ready at ${BRIDGE_ROOT}; mounting ${SMB_URL}"
mkdir -p "$BRIDGE_ROOT"
if mount | grep -F " on ${BRIDGE_ROOT} " >/dev/null; then
    echo "unmounting stale ${BRIDGE_ROOT}"
    umount "$BRIDGE_ROOT" 2>/dev/null || diskutil unmount "$BRIDGE_ROOT"
fi
mount_smbfs "$SMB_URL" "$BRIDGE_ROOT"
if [[ ! -d "${BRIDGE_ROOT}/inbox" || ! -w "${BRIDGE_ROOT}/inbox" ]]; then
    echo "mount finished but ${BRIDGE_ROOT}/inbox is not writable" >&2
    exit 1
fi
echo "bridge mounted: ${BRIDGE_ROOT}"
