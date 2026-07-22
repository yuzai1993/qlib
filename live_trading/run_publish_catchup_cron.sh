#!/usr/bin/env bash
# 漏发兜底：若下一交易日尚无 LIVE 批次，则补跑发布（休眠漏 cron 时用）
# 建议 crontab：5 22 * * 1-5（evening 检查前/后均可；幂等）
# 用法：bash live_trading/run_publish_catchup_cron.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="/opt/anaconda3/envs/qlib/bin/python"
CONFIG_ID="csi300_topk10_live"

# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

mkdir -p "${SCRIPT_DIR}/logs"
LOG_FILE="${SCRIPT_DIR}/logs/publish_cron.log"
export JOBLIB_MULTIPROCESSING="${JOBLIB_MULTIPROCESSING:-0}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mplconfig-live}"
mkdir -p "$MPLCONFIGDIR"

TRADE_DATE="$("$PYTHON" "$PROJECT_ROOT/live_trading/scripts/next_trade_date.py" \
    --after "$(date +%Y-%m-%d)")"

DB_PATH="$PROJECT_ROOT/live_trading/data/${CONFIG_ID}.db"
EXISTING="$("$PYTHON" - "$DB_PATH" "$TRADE_DATE" <<'PY'
import sqlite3, sys
db, trade_date = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db)
row = con.execute(
    "SELECT batch_id FROM batches WHERE trade_date=? AND superseded_by IS NULL "
    "ORDER BY created_at DESC LIMIT 1",
    (trade_date,),
).fetchone()
print(row[0] if row else "")
PY
)"

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') publish catchup trade_date=${TRADE_DATE} ====="
    if [[ -n "$EXISTING" ]]; then
        echo "skip: batch already present (${EXISTING})"
        echo "===== done ====="
        exit 0
    fi
    echo "missing batch for ${TRADE_DATE}; running publish"
    cd "$PROJECT_ROOT"
    # 防止发布中途休眠；-i 抑制 idle sleep
    caffeinate -i "$PYTHON" live_trading/scripts/run_publish_signals.py \
        --config "$CONFIG_ID" \
        --trade-date "$TRADE_DATE" \
        --mode LIVE
    echo "===== done ====="
} >>"$LOG_FILE" 2>&1
