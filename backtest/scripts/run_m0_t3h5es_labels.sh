#!/bin/bash
# 官方主格 / 早停改为 top3×h5 后，重训 M0 标签 H1/H5/H10/H20 五种子并评估。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo T3H5ES-LABEL-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_m0_t3h5es
LOG=backtest/result/m0_t3h5es_labels.log
mkdir -p "$OUT"

echo "===== T3H5ES-LABEL-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

SEEDS=(42 1000 2000 3000 4000)
HORIZONS=(1 5 10 20)

for h in "${HORIZONS[@]}"; do
  for s in "${SEEDS[@]}"; do
    sess="regimeadaptfast_m0h${h}_t3h5es_s${s}"
    if [[ -d "backtest/result/${sess}" ]]; then
      echo "TRAIN-SKIP $sess" | tee -a "$LOG"
      continue
    fi
    echo "TRAIN-START $sess $(date '+%m-%d %H:%M')" | tee -a "$LOG"
    $PY backtest/scripts/train_regime_arm.py --arm m0 --seed "$s" --model single \
        --label-horizon "$h" --es-metric top3_h5_net_ann --session-name "$sess" \
        | tee -a "$LOG"
    echo "TRAIN-DONE $sess $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  done
done

COMMON=(--pools all --segment test
  --horizons 2 3 4 5
  --head-k 1 2 3 4 5
  --exclude-limit-up
  --min-listing-days 60
  --min-amount 10000000
  --st-daily scripts/data_collector/tushare/st_daily.csv
  --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv"
  --regime-pools all)

for h in "${HORIZONS[@]}"; do
  json="$OUT/eval_m0h${h}.json"
  SESSIONS=()
  for s in "${SEEDS[@]}"; do
    sess="regimeadaptfast_m0h${h}_t3h5es_s${s}"
    if [[ ! -d "backtest/result/${sess}" ]]; then
      echo "MISSING $sess" | tee -a "$LOG"
      exit 1
    fi
    SESSIONS+=("${sess}:${s}")
  done
  echo "EVAL-START m0h${h} $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
      --sessions "${SESSIONS[@]}" "${COMMON[@]}" --output "$json" | tee -a "$LOG"
  echo "EVAL-DONE m0h${h} $(date '+%m-%d %H:%M')" | tee -a "$LOG"
done

$PY backtest/scripts/promote_phase_m_v1_t3h5es.py | tee -a "$LOG"
$PY backtest/scripts/build_regime_m0_label_report.py | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "===== T3H5ES-LABEL-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
