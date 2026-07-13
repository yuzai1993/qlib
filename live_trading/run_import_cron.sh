#!/usr/bin/env bash
# 导入当日 QMT 回执（工作日盘后由 crontab 调用）
# 用法：bash live_trading/run_import_cron.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="/opt/anaconda3/envs/qlib/bin/python"
CONFIG_ID="csi300_topk10_live"

# cron 环境无交互 shell；密钥放 ~/.qlib_live_env（sh 语法，勿进 git）
# 注意不要 source ~/.zshrc——它是 zsh 专用（oh-my-zsh），bash 下会中途退出
# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

mkdir -p "${SCRIPT_DIR}/logs"
LOG_FILE="${SCRIPT_DIR}/logs/import_cron.log"

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') import fills ====="
    cd "$PROJECT_ROOT"
    "$PYTHON" live_trading/scripts/run_import_fills.py --config "$CONFIG_ID"
    echo "===== done ====="
} >>"$LOG_FILE" 2>&1
