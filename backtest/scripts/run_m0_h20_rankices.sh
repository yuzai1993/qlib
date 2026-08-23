#!/bin/bash
# Phase M v1：相对 v3，valid 仍用评估窗，只把早停换成 daily_rank_ic。五种子后正式评估。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo RANKICES-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_ablation
LOG=backtest/result/m0_h20_rankices.log
SEEDS=(42 1000 2000 3000 4000)
mkdir -p "$OUT"

echo "===== RANKICES-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

for s in "${SEEDS[@]}"; do
  sess="regimeadaptfast_m0h20_rankices_s${s}"
  if [[ -d "backtest/result/${sess}" ]]; then
    echo "TRAIN-SKIP $sess" | tee -a "$LOG"
    continue
  fi
  echo "TRAIN-START $sess $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/train_regime_arm.py \
    --arm m0 --seed "$s" --model single --label-horizon 20 \
    --es-metric daily_rank_ic --es-valid eval_window \
    --session-name "$sess" | tee -a "$LOG"
  echo "TRAIN-DONE $sess $(date '+%m-%d %H:%M')" | tee -a "$LOG"
done

SESSIONS=()
for s in "${SEEDS[@]}"; do
  sess="regimeadaptfast_m0h20_rankices_s${s}"
  if [[ ! -d "backtest/result/${sess}" ]]; then
    echo "MISSING $sess" | tee -a "$LOG"
    exit 1
  fi
  SESSIONS+=("${sess}:${s}")
done

echo "EVAL-START rankices $(date '+%m-%d %H:%M')" | tee -a "$LOG"
$PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
    --sessions "${SESSIONS[@]}" \
    --pools all --segment test \
    --horizons 2 3 4 5 \
    --head-k 1 2 3 4 5 \
    --exclude-limit-up \
    --min-listing-days 60 \
    --min-amount 10000000 \
    --st-daily scripts/data_collector/tushare/st_daily.csv \
    --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv" \
    --regime-pools all \
    --output "$OUT/eval_m0h20_rankices.json" | tee -a "$LOG"
echo "EVAL-DONE rankices $(date '+%m-%d %H:%M')" | tee -a "$LOG"

$PY backtest/scripts/register_regime_ablation.py --spec rankices | tee -a "$LOG"
$PY backtest/scripts/build_regime_phase_m_detail_report.py --spec rankices | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "===== RANKICES-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
