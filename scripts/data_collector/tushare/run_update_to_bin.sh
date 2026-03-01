#!/usr/bin/env bash
# 定时执行 Tushare 日线增量入库，日志按日期写入 qlib 根目录下 logs/data/
# 用法：可直接执行，或由 crontab 在每周一至五 18:00 调用

set -e
QLIB_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$QLIB_ROOT"
mkdir -p logs/data
logfile="logs/data/$(date +%Y-%m-%d).log"
exec >> "$logfile" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
python scripts/data_collector/tushare/collector.py update_data_to_bin --qlib_dir ~/.qlib/qlib_data/cn_data
echo "===== end $(date '+%Y-%m-%d %H:%M:%S') ====="
