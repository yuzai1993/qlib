#!/usr/bin/env bash
# 定时执行 Tushare 日线增量入库，日志按日期写入 qlib 根目录下 logs/data/
# 用法：可直接执行，或由 crontab 在每周一至五 18:00 调用

set -e

# 报警邮箱地址，请按需修改
ALERT_EMAIL="xqyu1993@126.com"

QLIB_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$QLIB_ROOT"
mkdir -p logs/data
logfile="logs/data/$(date +%Y-%m-%d).log"
exec >> "$logfile" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="

/home/yuzai/miniforge3/envs/qlib312/bin/python scripts/data_collector/tushare/collector.py update_data_to_bin --qlib_dir ~/.qlib/qlib_data/cn_data || {
  status=$?
  if [[ -n "$ALERT_EMAIL" ]]; then
    host_name=$(hostname)
    subject="[qlib] Tushare 定时任务失败 (exit=$status, host=$host_name)"
    {
      echo "Tushare 定时任务出错，退出码：$status"
      echo "主机：$host_name"
      echo "时间：$(date '+%Y-%m-%d %H:%M:%S')"
      echo
      echo "以下为最近的错误日志（tail）："
      echo "----------------------------------------"
      tail -n 200 "$logfile" || echo "无法读取日志文件：$logfile"
    } | mail -s "$subject" "$ALERT_EMAIL" || true
  fi
  exit $status
}

echo "===== end $(date '+%Y-%m-%d %H:%M:%S') ====="
