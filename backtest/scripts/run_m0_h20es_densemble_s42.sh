#!/bin/bash
# Phase M v1：相对 v2 换 B6-M DoubleEnsemble，先跑种子 42。不作晋升依据。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
if [[ -f backtest/result/.skip_densemble_s42 ]]; then
  echo "DENSEMBLE-SKIP $(date '+%F %H:%M') 用户要求暂不启动，避免内存爆"
  exit 0
fi
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo DENSEMBLE-V2-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_ablation
LOG=backtest/result/regime_densemble_v2_s42.log
SESS=regimeadapt_m0h20es_densemble_s42
mkdir -p "$OUT"

echo "===== DENSEMBLE-V2-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

if [[ -d "backtest/result/${SESS}" ]]; then
  echo "TRAIN-SKIP $SESS" | tee -a "$LOG"
else
  echo "TRAIN-START $SESS $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/train_regime_arm.py \
    --arm m0 --seed 42 --model densemble --label-horizon 20 \
    --session-name "$SESS" | tee -a "$LOG"
  echo "TRAIN-DONE $SESS $(date '+%m-%d %H:%M')" | tee -a "$LOG"
fi

echo "EVAL-START densemble-v2 $(date '+%m-%d %H:%M')" | tee -a "$LOG"
$PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
    --sessions "${SESS}:42" \
    --pools all --segment test \
    --horizons 2 3 5 10 \
    --head-k 5 15 50 \
    --exclude-limit-up \
    --min-listing-days 60 \
    --min-amount 10000000 \
    --st-daily scripts/data_collector/tushare/st_daily.csv \
    --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv" \
    --regime-pools all \
    --output "$OUT/eval_densemble_s42_vs_v2.json" | tee -a "$LOG"
echo "EVAL-DONE densemble-v2 $(date '+%m-%d %H:%M')" | tee -a "$LOG"

$PY backtest/scripts/register_regime_ablation.py --spec densemble-v2 | tee -a "$LOG"
$PY backtest/scripts/build_regime_phase_m_detail_report.py --spec densemble-v2 | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "===== DENSEMBLE-V2-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
