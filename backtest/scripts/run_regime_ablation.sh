#!/bin/bash
# Phase M v1：M0 H20 上分别只加 regime 特征、只改样本采样。
# 五种子串行；先 feat 再 sample；每组训完再评估出报告。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo ABLATION-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_ablation
LOG=backtest/result/regime_ablation.log
SEEDS=(42 1000 2000 3000 4000)
mkdir -p "$OUT"

echo "===== ABLATION-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

COMMON=(--pools all --segment test
  --horizons 2 3 5 10
  --head-k 5 15 50
  --exclude-limit-up
  --min-listing-days 60
  --min-amount 10000000
  --st-names "$CFG/st_names.csv"
  --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv"
  --regime-pools all)

train_arm() {
  local feat_arm="$1" weight_arm="$2" sess_prefix="$3"
  for s in "${SEEDS[@]}"; do
    local sess="${sess_prefix}_s${s}"
    if [[ -d "backtest/result/${sess}" ]]; then
      echo "TRAIN-SKIP $sess" | tee -a "$LOG"
      continue
    fi
    echo "TRAIN-START $sess $(date '+%m-%d %H:%M')" | tee -a "$LOG"
    $PY backtest/scripts/train_regime_arm.py \
      --arm "$feat_arm" --weights "$weight_arm" --seed "$s" --model single \
      --label-horizon 20 --session-name "$sess" | tee -a "$LOG"
    echo "TRAIN-DONE $sess $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  done
}

eval_arm() {
  local name="$1" cfg="$2" prefix="$3"
  local json="$OUT/eval_${name}.json"
  local sessions=()
  for s in "${SEEDS[@]}"; do sessions+=("${prefix}_s${s}:${s}"); done
  echo "EVAL-START $name $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/eval_ic_multi_pool.py --config "$cfg" \
      --sessions "${sessions[@]}" "${COMMON[@]}" --output "$json" | tee -a "$LOG"
  echo "EVAL-DONE $name $(date '+%m-%d %H:%M')" | tee -a "$LOG"
}

echo "----- feat: m3 features + m0 weights + H20 -----" | tee -a "$LOG"
train_arm m3 m0 regimeadaptfast_feat_h20
eval_arm feat "$CFG/eval_m3fast.yaml" regimeadaptfast_feat_h20
$PY backtest/scripts/register_regime_ablation.py --spec feat | tee -a "$LOG"
$PY backtest/scripts/build_regime_phase_m_detail_report.py --spec feat | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "----- sample: m0 features + m3 weights + H20 -----" | tee -a "$LOG"
train_arm m0 m3 regimeadaptfast_sample_h20
eval_arm sample "$CFG/eval_m0fast.yaml" regimeadaptfast_sample_h20
$PY backtest/scripts/register_regime_ablation.py --spec sample | tee -a "$LOG"
$PY backtest/scripts/build_regime_phase_m_detail_report.py --spec sample | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "===== ABLATION-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
