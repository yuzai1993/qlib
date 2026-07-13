#!/usr/bin/env bash
# 发布次日 SIMULATE 信号（工作日晚间由 crontab 调用）
# 用法：
#   bash live_trading/run_publish_cron.sh              # trade_date = 明天
#   bash live_trading/run_publish_cron.sh 2026-07-14   # 指定交易日

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="/opt/anaconda3/envs/qlib/bin/python"
CONFIG_ID="csi300_topk10_live"

# cron 环境无交互 shell；密钥放 ~/.qlib_live_env（sh 语法，勿进 git）
# 注意不要 source ~/.zshrc——它是 zsh 专用（oh-my-zsh），bash 下会中途退出
# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

if [[ -z "${QMT_ACCOUNT_ID:-}" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: QMT_ACCOUNT_ID unset" >&2
    exit 1
fi

if [[ -n "${1:-}" ]]; then
    TRADE_DATE="$1"
else
    TRADE_DATE="$(date -v+1d +%Y-%m-%d)"
fi

mkdir -p "${SCRIPT_DIR}/logs"
LOG_FILE="${SCRIPT_DIR}/logs/publish_cron.log"
export JOBLIB_MULTIPROCESSING="${JOBLIB_MULTIPROCESSING:-0}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mplconfig-live}"
mkdir -p "$MPLCONFIGDIR"

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') publish trade_date=${TRADE_DATE} ====="
    cd "$PROJECT_ROOT"
    "$PYTHON" live_trading/scripts/run_publish_signals.py \
        --config "$CONFIG_ID" \
        --trade-date "$TRADE_DATE" \
        --mode LIVE
    echo "===== done ====="
} >>"$LOG_FILE" 2>&1
