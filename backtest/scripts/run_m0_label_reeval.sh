#!/bin/bash
# 只重评（模型已有）：补分年切片 + 日换手。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo M0REVAL-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_m0_labels
LOG=backtest/result/m0_label_reeval.log
mkdir -p "$OUT"

echo "===== M0REVAL-START $(date '+%F %H:%M') =====" | tee "$LOG"
$PY -m pytest tests/backtest/test_regime_adapt.py -q --tb=short | tee -a "$LOG"

SEEDS=(42 1000 2000 3000 4000)
COMMON=(--pools all --segment test
  --horizons 2 3 5 10
  --head-k 5 15 50
  --exclude-limit-up
  --min-listing-days 60
  --min-amount 10000000
  --st-names "$CFG/st_names.csv"
  --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv"
  --regime-pools all)

run_eval() {
  local name="$1"; shift
  local json="$OUT/eval_${name}.json"
  echo "EVAL-START $name $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
      --sessions "$@" "${COMMON[@]}" --output "$json" | tee -a "$LOG"
  echo "EVAL-DONE $name $(date '+%m-%d %H:%M')" | tee -a "$LOG"
}

for h in 1 2 3 5 10 20; do
  SESSIONS=()
  for s in "${SEEDS[@]}"; do SESSIONS+=("regimeadaptfast_m0h${h}_s${s}:${s}"); done
  run_eval "m0h${h}" "${SESSIONS[@]}"
done

run_eval m0h40 \
  20260812_222756_regimeadaptfast_m0_s42:42 \
  20260812_223202_regimeadaptfast_m0_s1000:1000 \
  20260812_223531_regimeadaptfast_m0_s2000:2000 \
  20260812_224058_regimeadaptfast_m0_s3000:3000 \
  20260812_224512_regimeadaptfast_m0_s4000:4000

$PY backtest/scripts/build_regime_m0_label_report.py | tee -a "$LOG"
# registry 已有 v4 行，分年写进 JSON 即可，不重复登记
echo "===== M0REVAL-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
