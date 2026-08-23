#!/bin/bash
# Phase M v1：M0 H20 DoubleEnsemble 单种子 42，对照 M0 H20。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo DENSEMBLE-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_ablation
LOG=backtest/result/regime_densemble.log
SESS=regimeadapt_m0h20_s42
mkdir -p "$OUT"

echo "===== DENSEMBLE-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

if [[ -d "backtest/result/${SESS}" ]]; then
  echo "TRAIN-SKIP $SESS" | tee -a "$LOG"
else
  echo "TRAIN-START $SESS $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/train_regime_arm.py \
    --arm m0 --seed 42 --model densemble --label-horizon 20 \
    --session-name "$SESS" | tee -a "$LOG"
  echo "TRAIN-DONE $SESS $(date '+%m-%d %H:%M')" | tee -a "$LOG"
fi

echo "EVAL-START densemble $(date '+%m-%d %H:%M')" | tee -a "$LOG"
$PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
  --sessions "${SESS}:42" \
  --pools all --segment test \
  --horizons 2 3 5 10 \
  --head-k 5 15 50 \
  --exclude-limit-up \
  --min-listing-days 60 \
  --min-amount 10000000 \
  --st-names "$CFG/st_names.csv" \
  --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv" \
  --regime-pools all \
  --output "$OUT/eval_densemble.json" | tee -a "$LOG"
echo "EVAL-DONE densemble $(date '+%m-%d %H:%M')" | tee -a "$LOG"

$PY backtest/scripts/register_regime_ablation.py --spec densemble | tee -a "$LOG"
$PY backtest/scripts/build_regime_phase_m_detail_report.py --spec densemble | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "===== DENSEMBLE-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
