#!/usr/bin/env bash
# 实盘监控（工作日由 crontab 按 stage 调用）
# 用法：
#   bash live_trading/run_monitor_cron.sh postmarket   # 16:00 盘后对账检查
#   bash live_trading/run_monitor_cron.sh report       # 20:30 快照 + 日报
#   bash live_trading/run_monitor_cron.sh evening      # 22:00 发布检查

set -euo pipefail

STAGE="${1:?usage: run_monitor_cron.sh postmarket|report|evening}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="/opt/anaconda3/envs/qlib/bin/python"
CONFIG_ID="csi300_topk10_live"

# cron 环境无交互 shell；密钥放 ~/.qlib_live_env（sh 语法，勿进 git）
# 注意不要 source ~/.zshrc——它是 zsh 专用（oh-my-zsh），bash 下会中途退出
# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

mkdir -p "${SCRIPT_DIR}/logs"
LOG_FILE="${SCRIPT_DIR}/logs/monitor_cron.log"

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') monitor stage=${STAGE} ====="
    cd "$PROJECT_ROOT"
    # 监控退出码非 0 表示有 WARN/CRIT，属正常业务信号，不让 set -e 中断日志收尾
    "$PYTHON" live_trading/scripts/run_monitor.py \
        --config "$CONFIG_ID" --stage "$STAGE" || true
    echo "===== done ====="
} >>"$LOG_FILE" 2>&1
