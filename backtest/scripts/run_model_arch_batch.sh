#!/bin/zsh
# Sequentially train remaining model-arch configs (xgboost s42 already done).
set -u
PY=/opt/anaconda3/envs/qlib/bin/python
export MPLCONFIGDIR=/tmp/mpl
cd "$(dirname "$0")/../.."

configs=(
  model-arch/xgboost/ma_xgboost_s1000.yaml
  model-arch/xgboost/ma_xgboost_s2000.yaml
  model-arch/xgboost/ma_xgboost_s3000.yaml
  model-arch/xgboost/ma_xgboost_s4000.yaml
  model-arch/catboost/ma_catboost_s42.yaml
  model-arch/catboost/ma_catboost_s1000.yaml
  model-arch/catboost/ma_catboost_s2000.yaml
  model-arch/catboost/ma_catboost_s3000.yaml
  model-arch/catboost/ma_catboost_s4000.yaml
  model-arch/double-ensemble/ma_double_ensemble_s42.yaml
  model-arch/double-ensemble/ma_double_ensemble_s1000.yaml
  model-arch/double-ensemble/ma_double_ensemble_s2000.yaml
  model-arch/double-ensemble/ma_double_ensemble_s3000.yaml
  model-arch/double-ensemble/ma_double_ensemble_s4000.yaml
)

fail=0
for cfg in "${configs[@]}"; do
  echo "===== BATCH START $cfg $(date '+%H:%M:%S') ====="
  if ! "$PY" backtest/scripts/run_backtest.py --config "$cfg" > /tmp/ma_batch_last.log 2>&1; then
    echo "===== BATCH FAIL $cfg ====="
    tail -30 /tmp/ma_batch_last.log
    fail=1
  else
    tail -4 /tmp/ma_batch_last.log | head -2
    echo "===== BATCH OK $cfg $(date '+%H:%M:%S') ====="
  fi
done
echo "===== BATCH ALL DONE fail=$fail ====="
exit $fail
