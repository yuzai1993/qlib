#!/bin/bash
# M0 H20：早停改为全A 1454 天 top5×h5 扣费净年化，五种子重训 + 评估 + 报告。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo T5H5ES-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_m0_t5h5es
LOG=backtest/result/m0_h20_t5h5es.log
mkdir -p "$OUT"

echo "===== T5H5ES-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

SEEDS=(42 1000 2000 3000 4000)
for s in "${SEEDS[@]}"; do
  sess="regimeadaptfast_m0h20_t5h5es_s${s}"
  if [[ -d "backtest/result/${sess}" ]]; then
    echo "TRAIN-SKIP $sess" | tee -a "$LOG"
    continue
  fi
  echo "TRAIN-START $sess $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/train_regime_arm.py --arm m0 --seed "$s" --model single \
      --label-horizon 20 --es-metric top5_h5_net_ann --session-name "$sess" \
      | tee -a "$LOG"
  echo "TRAIN-DONE $sess $(date '+%m-%d %H:%M')" | tee -a "$LOG"
done

SESSIONS=()
for s in "${SEEDS[@]}"; do SESSIONS+=("regimeadaptfast_m0h20_t5h5es_s${s}:${s}"); done
echo "EVAL-START $(date '+%m-%d %H:%M')" | tee -a "$LOG"
$PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
    --sessions "${SESSIONS[@]}" \
    --pools all --segment test \
    --horizons 2 3 5 10 \
    --head-k 5 15 50 \
    --exclude-limit-up \
    --min-listing-days 60 \
    --min-amount 10000000 \
    --st-names "$CFG/st_names.csv" \
    --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv" \
    --regime-pools all \
    --output "$OUT/eval_m0h20.json" | tee -a "$LOG"
echo "EVAL-DONE $(date '+%m-%d %H:%M')" | tee -a "$LOG"

$PY backtest/scripts/build_regime_phase_m_detail_report.py --spec t5h5es | tee -a "$LOG"
$PY backtest/scripts/register_regime_ablation.py --spec t5h5es | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "===== T5H5ES-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
