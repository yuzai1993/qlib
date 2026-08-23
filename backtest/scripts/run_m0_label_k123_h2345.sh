#!/bin/bash
# 重评 M0 标签报告网格：k∈{1,2,3,4,5} × h∈{2,3,4,5}。
# 不覆盖官方 top5×h5 JSON（eval_m0h20_st_daily.json / eval_regime_m0_t5h5es/eval_m0h20.json）。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo M0LABEL-K123-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_m0_labels
LOG=backtest/result/m0_label_k123_h2345.log
mkdir -p "$OUT"

echo "===== M0LABEL-K123-START $(date '+%F %H:%M') =====" | tee "$LOG"

$PY -m pytest tests/backtest/test_regime_m0_label_report.py -q --tb=short | tee -a "$LOG"

COMMON=(--pools all --segment test
  --horizons 2 3 4 5
  --head-k 1 2 3 4 5
  --exclude-limit-up
  --min-listing-days 60
  --min-amount 10000000
  --st-daily scripts/data_collector/tushare/st_daily.csv
  --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv"
  --regime-pools all)

eval_arm() {
  local name="$1"
  local out="$2"
  shift 2
  echo "EVAL-START $name $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
      --sessions "$@" "${COMMON[@]}" --output "$out" | tee -a "$LOG"
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

eval_arm m0h20 "$OUT/eval_m0h20_k123h2345.json" "${V1[@]}"
eval_arm m0h20es "$OUT/eval_m0h20es_k123h2345.json" "${V2[@]}"

$PY backtest/scripts/build_regime_m0_label_report.py | tee -a "$LOG"
$PY backtest/scripts/register_regime_m0_labels.py --grid k123h2345 | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "===== M0LABEL-K123-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
