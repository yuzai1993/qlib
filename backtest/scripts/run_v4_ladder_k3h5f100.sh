#!/bin/bash
# v4 官方合成信号 × 真阶梯 k3h5 + 掉出前 100 必卖。只回测一次，不晋升执行层基线。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
PY=/opt/anaconda3/envs/qlib/bin/python
PRED_DIR=backtest/result/phase_s_regime/preds
LOG=backtest/result/m0h20rankices_ladder_k3h5f100.log
mkdir -p "$PRED_DIR"

echo "===== V4-LADDER-K3H5F100-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

if [[ ! -f "$PRED_DIR/regimeadaptfast_m0h20_rankices_s42_pred.pkl" ]]; then
  echo "MISSING v4 seed preds; dump first" | tee -a "$LOG"
  exit 1
fi

echo "BT-START $(date '+%m-%d %H:%M')" | tee -a "$LOG"
$PY backtest/scripts/run_regime_phase_s.py \
  --arms m0h20rankices --pool all --strategy ladder_k3h5f100 \
  --account 1000000 --universe-filter --ensemble-only --generate-figures \
  --no-skip-existing | tee -a "$LOG"
echo "BT-DONE $(date '+%m-%d %H:%M')" | tee -a "$LOG"

echo "===== V4-LADDER-K3H5F100-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
