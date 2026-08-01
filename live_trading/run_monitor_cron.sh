#!/usr/bin/env bash
# 实盘监控（工作日由 crontab 按 stage 调用）
# 用法：
#   bash live_trading/run_monitor_cron.sh postmarket [config_id]
#   bash live_trading/run_monitor_cron.sh report [config_id]
#   bash live_trading/run_monitor_cron.sh evening [config_id]

set -euo pipefail

STAGE="${1:?usage: run_monitor_cron.sh postmarket|report|evening}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="/opt/anaconda3/envs/qlib/bin/python"
CONFIG_ID="${2:-${LIVE_CONFIG_ID:-${QLIB_LIVE_CONFIG_ID:-csi1000_b6m_b2s_postclose}}}"

# cron 环境无交互 shell；密钥放 ~/.qlib_live_env（sh 语法，勿进 git）
# 注意不要 source ~/.zshrc——它是 zsh 专用（oh-my-zsh），bash 下会中途退出
# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

mkdir -p "${SCRIPT_DIR}/logs"
LOG_FILE="${SCRIPT_DIR}/logs/${CONFIG_ID}_monitor_cron.log"
LOCK_ROOT="${SCRIPT_DIR}/.locks"
LOCK_DIR="${LOCK_ROOT}/${CONFIG_ID}_monitor_${STAGE}.lock"
mkdir -p "$LOCK_ROOT"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "another ${STAGE} monitor job holds $LOCK_DIR" >&2
    exit 75
fi
release_lock() { rmdir "$LOCK_DIR" 2>/dev/null || :; }
trap release_lock EXIT

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') monitor stage=${STAGE} ====="
    cd "$PROJECT_ROOT"
    status=0
    "$PYTHON" live_trading/scripts/run_monitor.py \
        --config "$CONFIG_ID" --stage "$STAGE" || status=$?
    echo "===== done status=${status} ====="
    exit "$status"
} >>"$LOG_FILE" 2>&1
