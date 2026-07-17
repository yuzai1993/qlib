#!/usr/bin/env bash
# 定时执行 Tushare 日线增量入库，日志按日期写入 qlib 根目录下 logs/data/
#
# 原则：
#   1. 所有步骤都跑完（单步失败不阻断后续）
#   2. 任意步骤失败都发微信告警（Server酱，密钥 SERVERCHAN_SENDKEY）
#   3. 任一失败则最终以非 0 退出
#
# 用法：可直接执行，或由 crontab 在工作日调用
#   30 16 * * 1-5 /path/to/qlib/scripts/data_collector/tushare/run_update_to_bin.sh

set -uo pipefail

QLIB_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$QLIB_ROOT"
PYTHON="/opt/anaconda3/envs/qlib/bin/python"

# cron 无交互 shell；与实盘监控共用 ~/.qlib_live_env（sh 语法，勿进 git）
# shellcheck disable=SC1090
[[ -f "$HOME/.qlib_live_env" ]] && source "$HOME/.qlib_live_env"

# 日志重定向后 tqdm 会用 \r 刷屏，关闭进度条；关键节点仍有 loguru/INFO
export TQDM_DISABLE=1

mkdir -p logs/data
logfile="logs/data/$(date +%Y-%m-%d).log"
exec >>"$logfile" 2>&1

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

FAILED=0
FAILURES=()
log "===== start ====="

send_wechat() {
  local title="$1"
  local body="$2"
  if [[ -z "${SERVERCHAN_SENDKEY:-}" ]]; then
    log "ERROR: SERVERCHAN_SENDKEY unset，无法推送：$title"
    return 1
  fi
  TITLE="$title" BODY="$body" "$PYTHON" -c '
import os, sys
sys.path.insert(0, ".")
from live_trading.modules.notifier import ServerChanNotifier
ok = ServerChanNotifier(os.environ["SERVERCHAN_SENDKEY"]).send(
    os.environ["TITLE"], os.environ["BODY"]
)
sys.exit(0 if ok else 1)
' && return 0
  log "WARNING: 微信告警发送失败：$title"
  return 1
}

alert_fail() {
  local step="$1"
  local detail="$2"
  FAILED=1
  FAILURES+=("$step")
  log "FAIL: $step"
  local host_name
  host_name="$(hostname)"
  send_wechat "[qlib] ${step}失败 (host=${host_name})" \
    "${detail}

时间：$(ts)
主机：${host_name}
日志：${QLIB_ROOT}/${logfile}" || true
}

# 显式指定 source/normalize 目录：必须与 vwap 全量回填使用同一目录，
# 否则增量更新会用不含 amount 的旧 source 全量重灌 bin，抹掉历史 vwap
SOURCE_DIR="scripts/data_collector/tushare/source"
NORMALIZE_DIR="scripts/data_collector/tushare/normalize"

# ---------- 1) 个股/指数日线增量 dump ----------
log "===== update_data_to_bin ====="
if "$PYTHON" scripts/data_collector/tushare/collector.py update_data_to_bin \
  --qlib_dir ~/.qlib/qlib_data/cn_data \
  --source_dir "$SOURCE_DIR" \
  --normalize_dir "$NORMALIZE_DIR"
then
  log "update_data_to_bin OK"
else
  status=$?
  alert_fail "Tushare日线入库" \
    "update_data_to_bin 退出码：${status}

最近日志：
----------------------------------------
$(tail -n 200 "$logfile" || echo "无法读取日志")"
fi

# ---------- 2) 指数成分日更 ----------
log "===== index instruments daily update ====="
if "$PYTHON" -m scripts.data_collector.update_indices_daily
then
  log "index instruments update OK"
else
  status=$?
  alert_fail "指数成分日更" \
    "update_indices_daily 退出码：${status}

最近日志：
----------------------------------------
$(tail -n 80 "$logfile" || echo "无法读取日志")"
fi

# ---------- 3) vwap 巡检 ----------
log "===== vwap check ====="
vwap_check_output="$("$PYTHON" - "$NORMALIZE_DIR/sh600000.csv" <<'PYEOF'
import sys
import pandas as pd

path = sys.argv[1]
try:
    df = pd.read_csv(path)
except FileNotFoundError:
    print(f"SKIP: {path} 不存在")
    sys.exit(0)
if "vwap" not in df.columns:
    print(f"FAIL: {path} 缺少 vwap 列")
    sys.exit(1)
recent = df.dropna(subset=["close"]).tail(1)
if recent.empty or pd.isna(recent["vwap"].iloc[0]):
    print(f"FAIL: {path} 最新交易日 vwap 为空")
    sys.exit(1)
print("OK: vwap 正常")
PYEOF
)" && vwap_status=0 || vwap_status=$?
log "$vwap_check_output"
if [[ "$vwap_status" -ne 0 ]]; then
  alert_fail "Tushare vwap巡检" \
    "${vwap_check_output}

请检查 source 目录是否含 amount 列、collector 代码是否为最新。"
fi

# ---------- 4) 前复权回溯完整性巡检（近 90 天）----------
ADJ_START="$(date -v-90d +%Y-%m-%d 2>/dev/null || date -d '90 days ago' +%Y-%m-%d)"
log "===== adjust integrity check (start=$ADJ_START) ====="
adj_output="$(
  PYTHONPATH="$QLIB_ROOT" "$PYTHON" \
    "$QLIB_ROOT/scripts/data_collector/tushare/check_adjust_integrity.py" \
    --instruments csi300 --start "$ADJ_START" 2>&1
)" && adj_status=0 || adj_status=$?
echo "$adj_output"
if [[ "$adj_status" -ne 0 ]]; then
  alert_fail "复权回溯巡检" \
    "${adj_output}

除权日 close 收益率与 \$change 偏差过大，历史前复权可能未正确回溯。"
fi

# ---------- 汇总 ----------
if [[ "$FAILED" -ne 0 ]]; then
  log "===== FAILED steps: ${FAILURES[*]} ====="
  log "===== end (FAILED) ====="
  exit 1
fi

log "===== all steps OK ====="
log "===== end ====="
exit 0
