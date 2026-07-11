#!/usr/bin/env bash
# 定时执行 Tushare 日线增量入库，日志按日期写入 qlib 根目录下 logs/data/
# 用法：可直接执行，或由 crontab 在每周一至五 18:00 调用

set -e

export SMTP_USER="xqyu1993@126.com"
export SMTP_PASS="Yxq199304203615$"
export SMTP_FROM="$SMTP_USER"

# 报警邮箱地址，请按需修改
ALERT_EMAIL="xqyu1993@126.com"
# SMTP 告警配置（建议通过环境变量覆盖）
SMTP_HOST="${SMTP_HOST:-smtp.126.com}"
SMTP_PORT="${SMTP_PORT:-465}"
SMTP_USER="${SMTP_USER:-}"
SMTP_PASS="${SMTP_PASS:-}"
SMTP_FROM="${SMTP_FROM:-$SMTP_USER}"

QLIB_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$QLIB_ROOT"
mkdir -p logs/data
logfile="logs/data/$(date +%Y-%m-%d).log"
exec >> "$logfile" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="

send_alert_email() {
  local subject="$1"
  local body="$2"
  local py_bin
  py_bin="$(command -v python3 || command -v python || true)"

  if [[ -z "$py_bin" ]]; then
    echo "WARNING: 未找到 python/python3，无法发送 SMTP 告警。"
    return 1
  fi
  if [[ -z "$SMTP_USER" || -z "$SMTP_PASS" || -z "$SMTP_FROM" || -z "$ALERT_EMAIL" ]]; then
    echo "WARNING: SMTP 参数不完整，跳过告警。请设置 SMTP_USER/SMTP_PASS/SMTP_FROM/ALERT_EMAIL。"
    return 1
  fi

  ALERT_SUBJECT="$subject" ALERT_BODY="$body" ALERT_TO="$ALERT_EMAIL" \
  SMTP_HOST="$SMTP_HOST" SMTP_PORT="$SMTP_PORT" SMTP_USER="$SMTP_USER" \
  SMTP_PASS="$SMTP_PASS" SMTP_FROM="$SMTP_FROM" \
  "$py_bin" -c '
import os, smtplib
from email.mime.text import MIMEText
from email.header import Header
host = os.environ["SMTP_HOST"]
port = int(os.environ.get("SMTP_PORT", "465"))
user = os.environ["SMTP_USER"]
password = os.environ["SMTP_PASS"]
sender = os.environ["SMTP_FROM"]
to_addr = os.environ["ALERT_TO"]
subject = os.environ["ALERT_SUBJECT"]
body = os.environ["ALERT_BODY"]
msg = MIMEText(body, "plain", "utf-8")
msg["Subject"] = Header(subject, "utf-8")
msg["From"] = sender
msg["To"] = to_addr
server = smtplib.SMTP_SSL(host, port, timeout=20)
server.login(user, password)
server.sendmail(sender, [to_addr], msg.as_string())
server.quit()
' && return 0

  echo "WARNING: SMTP 告警发送失败。"
  return 1
}

# 显式指定 source/normalize 目录：必须与 vwap 全量回填使用同一目录，
# 否则增量更新会用不含 amount 的旧 source 全量重灌 bin，抹掉历史 vwap
SOURCE_DIR="scripts/data_collector/tushare/source"
NORMALIZE_DIR="scripts/data_collector/tushare/normalize"

/opt/anaconda3/envs/qlib/bin/python scripts/data_collector/tushare/collector.py update_data_to_bin --qlib_dir ~/.qlib/qlib_data/cn_data --source_dir "$SOURCE_DIR" --normalize_dir "$NORMALIZE_DIR" || {
  status=$?
  if [[ -n "$ALERT_EMAIL" ]]; then
    host_name=$(hostname)
    subject="[qlib] Tushare 定时任务失败 (exit=$status, host=$host_name)"
    body="$(
      {
        echo "Tushare 定时任务出错，退出码：$status"
        echo "主机：$host_name"
        echo "时间：$(date '+%Y-%m-%d %H:%M:%S')"
        echo
        echo "以下为最近的错误日志（tail）："
        echo "----------------------------------------"
        tail -n 200 "$logfile" || echo "无法读取日志文件：$logfile"
      }
    )"
    send_alert_email "$subject" "$body" || true
  fi
  exit $status
}

# 指数成分改由 csindex_v2（300/500/1000）+ 聚宽（2000 + 交叉校验）日更
# 失败只告警，不阻断个股更新成功状态
echo "===== index instruments daily update ====="
if /opt/anaconda3/envs/qlib/bin/python -m scripts.data_collector.update_indices_daily; then
  echo "index instruments update OK"
else
  idx_status=$?
  echo "WARNING: index instruments update failed (exit=$idx_status), stock dump already succeeded"
  if [[ -n "$ALERT_EMAIL" ]]; then
    host_name=$(hostname)
    send_alert_email \
      "[qlib] 指数成分日更失败 (exit=$idx_status, host=$host_name)" \
      "$(echo "指数成分更新失败，个股 dump 已成功，请检查日志。"; echo "时间：$(date '+%Y-%m-%d %H:%M:%S')"; echo; tail -n 80 "$logfile")" \
      || true
  fi
fi

# vwap 巡检：最新一行的 vwap 必须非空（捕捉“增量更新没产出 vwap”的回归）
vwap_check_output="$(/opt/anaconda3/envs/qlib/bin/python - "$NORMALIZE_DIR/sh600000.csv" <<'PYEOF'
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
)" || {
  echo "WARNING: vwap 巡检未通过：$vwap_check_output"
  send_alert_email "[qlib] Tushare vwap 巡检未通过 (host=$(hostname))" \
    "$vwap_check_output
时间：$(date '+%Y-%m-%d %H:%M:%S')
请检查 source 目录是否含 amount 列、collector 代码是否为最新。" || true
}
echo "$vwap_check_output"

# 前复权回溯完整性巡检（近 90 天）
ADJ_START="$(date -v-90d +%Y-%m-%d 2>/dev/null || date -d '90 days ago' +%Y-%m-%d)"
echo "===== adjust integrity check (start=$ADJ_START) ====="
PYTHONPATH="$QLIB_ROOT" /opt/anaconda3/envs/qlib/bin/python \
  "$QLIB_ROOT/scripts/data_collector/tushare/check_adjust_integrity.py" \
  --instruments csi300 --start "$ADJ_START" \
  || echo "[WARN] 复权回溯巡检未通过，请人工检查"

echo "===== end $(date '+%Y-%m-%d %H:%M:%S') ====="
