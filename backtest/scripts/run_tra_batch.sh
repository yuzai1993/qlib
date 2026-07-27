#!/bin/zsh
# Train remaining model-arch-nn/tra seeds sequentially (s42 trained separately as gate).
set -eu
PY=/opt/anaconda3/envs/qlib/bin/python
export MPLCONFIGDIR=/tmp/mpl
cd "$(dirname "$0")/../.."

for seed in 1000 2000 3000 4000; do
  echo "===== TRAIN ma_tra_s${seed} ====="
  "$PY" backtest/scripts/run_backtest.py --config "model-arch-nn/tra/ma_tra_s${seed}.yaml"
done
echo "===== TRA TRAIN ALL DONE ====="
