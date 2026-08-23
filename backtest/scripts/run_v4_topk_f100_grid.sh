#!/bin/bash
# v4 官方合成信号 × TopkDropout top15d3 / top5d1 / top3d1 + 掉出前100必卖。不晋升。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
PY=/opt/anaconda3/envs/qlib/bin/python
PRED_DIR=backtest/result/phase_s_regime/preds
LOG=backtest/result/m0h20rankices_topk_f100_grid.log

echo "===== V4-TOPK-F100-GRID-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

if [[ ! -f "$PRED_DIR/regimeadaptfast_m0h20_rankices_s42_pred.pkl" ]]; then
  echo "MISSING v4 seed preds" | tee -a "$LOG"
  exit 1
fi

for strat in top15d3f100 top5d1f100 top3d1f100; do
  echo "BT-START $strat $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/run_regime_phase_s.py \
    --arms m0h20rankices --pool all --strategy "$strat" \
    --account 1000000 --universe-filter --ensemble-only --generate-figures \
    --no-skip-existing | tee -a "$LOG"
  echo "BT-DONE $strat $(date '+%m-%d %H:%M')" | tee -a "$LOG"
done

echo "===== V4-TOPK-F100-GRID-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
