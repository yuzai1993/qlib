#!/bin/bash
# 同一组 M0 H20 五种子，用日频 st_daily 重评；不覆盖 8/16 的 eval_m0h20.json。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo M0H20-STDAILY-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_m0_labels
LOG=backtest/result/m0_h20_st_daily_reeval.log
mkdir -p "$OUT"

echo "===== M0H20-STDAILY-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

SEEDS=(42 1000 2000 3000 4000)
SESSIONS=()
for s in "${SEEDS[@]}"; do
  sess="regimeadaptfast_m0h20_s${s}"
  if [[ ! -d "backtest/result/${sess}" ]]; then
    echo "MISSING $sess" | tee -a "$LOG"
    exit 1
  fi
  SESSIONS+=("${sess}:${s}")
done

echo "EVAL-START $(date '+%m-%d %H:%M')" | tee -a "$LOG"
$PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
    --sessions "${SESSIONS[@]}" \
    --pools all --segment test \
    --horizons 2 3 5 10 \
    --head-k 5 15 50 \
    --exclude-limit-up \
    --min-listing-days 60 \
    --min-amount 10000000 \
    --st-daily scripts/data_collector/tushare/st_daily.csv \
    --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv" \
    --regime-pools all \
    --output "$OUT/eval_m0h20_st_daily.json" | tee -a "$LOG"
echo "EVAL-DONE $(date '+%m-%d %H:%M')" | tee -a "$LOG"

$PY backtest/scripts/build_regime_phase_m_detail_report.py --spec t5h5es | tee -a "$LOG"
$PY backtest/scripts/register_regime_ablation.py --spec m0h20-st-daily | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "===== M0H20-STDAILY-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
