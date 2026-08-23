#!/bin/bash
# 官方主格改为五种子 z-score 等权合成后再算 top5×h5。
# 过滤、评估窗、日频 ST 不变；不覆盖 8/16 的 eval_m0h20.json（st_names）。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo ENSEMBLE-REEVAL-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
LOG=backtest/result/m0_h20_ensemble_reeval.log
mkdir -p backtest/result/eval_regime_m0_labels backtest/result/eval_regime_m0_t5h5es

echo "===== ENSEMBLE-REEVAL-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

if [[ ! -f backtest/result/eval_regime_m0_labels/eval_m0h20_st_daily.seedmean.json ]]; then
  cp backtest/result/eval_regime_m0_labels/eval_m0h20_st_daily.json \
     backtest/result/eval_regime_m0_labels/eval_m0h20_st_daily.seedmean.json
fi
if [[ ! -f backtest/result/eval_regime_m0_t5h5es/eval_m0h20.seedmean.json ]]; then
  cp backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json \
     backtest/result/eval_regime_m0_t5h5es/eval_m0h20.seedmean.json
fi

eval_arm() {
  local name="$1"
  local out="$2"
  shift 2
  echo "EVAL-START $name $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
      --sessions "$@" \
      --pools all --segment test \
      --horizons 2 3 5 10 \
      --head-k 5 15 50 \
      --exclude-limit-up \
      --min-listing-days 60 \
      --min-amount 10000000 \
      --st-daily scripts/data_collector/tushare/st_daily.csv \
      --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv" \
      --regime-pools all \
      --output "$out" | tee -a "$LOG"
  echo "EVAL-DONE $name $(date '+%m-%d %H:%M')" | tee -a "$LOG"
}

SEEDS=(42 1000 2000 3000 4000)
V1=()
V2=()
for s in "${SEEDS[@]}"; do
  v1="regimeadaptfast_m0h20_s${s}"
  v2="regimeadaptfast_m0h20_t5h5es_s${s}"
  if [[ ! -d "backtest/result/${v1}" ]]; then echo "MISSING $v1" | tee -a "$LOG"; exit 1; fi
  if [[ ! -d "backtest/result/${v2}" ]]; then echo "MISSING $v2" | tee -a "$LOG"; exit 1; fi
  V1+=("${v1}:${s}")
  V2+=("${v2}:${s}")
done

eval_arm m0h20 backtest/result/eval_regime_m0_labels/eval_m0h20_st_daily.json "${V1[@]}"
eval_arm m0h20es backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json "${V2[@]}"

$PY backtest/scripts/register_regime_m0_labels.py --refresh | tee -a "$LOG"
$PY backtest/scripts/register_regime_ablation.py --spec t5h5es | tee -a "$LOG"
$PY backtest/scripts/build_regime_phase_m_detail_report.py --spec t5h5es | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "===== ENSEMBLE-REEVAL-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
