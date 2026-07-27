#!/bin/zsh
# Sequentially train loss-design configs passed as arguments (paths relative to backtest/configs/).
set -u
PY=/opt/anaconda3/envs/qlib/bin/python
export MPLCONFIGDIR=/tmp/mpl
cd "$(dirname "$0")/../.."

fail=0
for cfg in "$@"; do
  echo "===== BATCH START $cfg $(date '+%H:%M:%S') ====="
  if ! "$PY" backtest/scripts/run_backtest.py --config "$cfg" > /tmp/ls_batch_last.log 2>&1; then
    echo "===== BATCH FAIL $cfg ====="
    tail -30 /tmp/ls_batch_last.log
    fail=1
  else
    tail -4 /tmp/ls_batch_last.log | head -2
    echo "===== BATCH OK $cfg $(date '+%H:%M:%S') ====="
  fi
done
echo "===== BATCH ALL DONE fail=$fail ====="
exit $fail
